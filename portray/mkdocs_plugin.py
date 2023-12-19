from mkdocs.config import base, config_options as c
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.plugins import BasePlugin, get_plugin_logger
from mkdocs.structure import StructureItem
from mkdocs.structure.files import Files
from mkdocs.structure.nav import Navigation, Section
from mkdocs.structure.pages import Page
from portray.config import project, PDOCS_DEFAULTS, PORTRAY_DEFAULTS
from portray.render import _nested_docs, pdocs

import logging
import os
import re
from typing import Dict, List, Union


HTML_LINK_REGEX = re.compile(r"<a[^>]*href=[\"']?(?P<href>[^\"' >]*)[\"']?[^>]*>[^<]*</a>")
REFERENCE_PLACEHOLDER = "$references"

MkDocsConfigNav = List[Dict[str, Union[str, "MkDocsConfigNav"]]]


class PortrayOptions(base.Config):
    append_directory_to_python_path = c.Type(bool, default=PORTRAY_DEFAULTS["append_directory_to_python_path"])
    include_reference_documentation = c.Type(bool, default=PORTRAY_DEFAULTS["include_reference_documentation"])
    compress_package_names_for_reference_documentation = c.Type(bool, default=True)
    labels = c.DictOfItems(c.Type(str), default=PORTRAY_DEFAULTS["labels"])


class PdocsOptions(base.Config):
    exclude_source = c.Type(bool, default=PDOCS_DEFAULTS["exclude_source"])


class MkdocsPluginConfig(base.Config):
    portray = c.SubConfig(PortrayOptions)
    pdocs = c.SubConfig(PdocsOptions)

    api_path = c.Type(str, default="references")
    api_title = c.Optional(c.Type(str))
    config_file = c.Type(str, default="pyproject.toml")
    modules = c.Optional(c.ListOfItems(c.Type(str)))
    output_dir = c.Optional(c.Type(str))
    project_root = c.Type(str, default=os.getcwd())


class MkdocsPlugin(BasePlugin[MkdocsPluginConfig]):
    """
    This plugin generates API documentation using pdoc and adds it to the navigation.
    Furthermore, it replaces links to pdoc documentation with links to the generated documentation.

    References:

    - <https://github.com/spirali/mkdocs-pdoc-plugin>
    - <https://www.mkdocs.org/dev-guide/plugins>
    - <https://github.com/HeinrichAD/portray/tree/develop>
    - <https://github.com/HeinrichAD/pdocs/tree/develop>

    Known issues:

    - only support to have the API references as root item inside the navigation
    - pdocs library creates absolute (create only relative links to child pages)
    """

    def __init__(self):
        self.docs_dir: str = ""
        self.site_url: str = ""
        self.project_config: dict = dict()
        self.nav_already_fixed: bool = False
        self.logger = get_plugin_logger(__name__)
        
    def _add_ignore_api_relative_link_log_messages(self):
        # disable "unrecognized relative link" warning for API links
        pattern = r"Doc file '{}/.+\.md' contains an unrecognized relative link '.+/', it was left as is\.( Did you mean '.+\.md'\?)?"
        pattern = pattern.format(self.config["api_path"])
        pattern = re.compile(pattern)

        class IgnoreAPIRelativeLinkWarningFilter(logging.Filter):
            def filter(self, record):
                return not pattern.match(record.getMessage())

        logging.getLogger("mkdocs.structure.pages").addFilter(IgnoreAPIRelativeLinkWarningFilter())

    def _resolve_link(self, qualified_name: str, files: Files) -> str:
        qname = tuple(qualified_name.split("."))
        original_qname = qname
        while qname:
            url = f"{self.config['api_path']}/{'/'.join(qname)}/"
            if any((True for file in files._files if file.url == url)):
                path = os.path.join(self.site_url, url)
                rest = original_qname[len(qname):]
                if rest:
                    return path + "#" + ".".join(rest)
                else:
                    return path
            qname = qname[:-1]

        self.logger.error(f"Invalid reference: {qualified_name}")
        return qualified_name

    def _get_api_nav_part(self, docs_dir: str) -> MkDocsConfigNav:
        # get API doc pages including their formated labels in correct order
        return _nested_docs(
            self.project_config["pdocs"]["output_dir"],
            docs_dir,
            self.project_config
        )

    def _replace_nav_placeholder(self, config: MkDocsConfig):
        if not config["nav"]:
            return

        # search and replace "$references" placeholder
        for idx in range(len(config["nav"])):
            key, value = next(iter(config["nav"][idx].items()))
            if value == REFERENCE_PLACEHOLDER:
                # get API doc pages including their formated labels in correct order
                config["nav"][idx][key] = self._get_api_nav_part(config["docs_dir"])
                self.nav_already_fixed = True
                return

    def _remove_none_items(self, dictionary: dict, *, recursive: bool = True) -> dict:
        return {
            k: self._remove_none_items(v) if recursive and isinstance(v, dict) else v
            for k, v in dictionary.items()
            if v is not None
        }

    def _convert_to_section(self, title: str, refs: MkDocsConfigNav, config: MkDocsConfig, files: Files) -> Section:
        children: List[StructureItem] = []
        for ref in refs:
            key, value = tuple(ref.items())[0]
            if isinstance(value, str):
                children.append(Page(key, files.src_paths[value], config))
                # alternative to `Page(key, files.src_paths[value], config)` could be:
                # `Page(key, File(value, "", config["site_dir"], config["use_directory_urls"]), config)`
            else:
                children.append(self._convert_to_section(key, value, config, files))
        return Section(title, children)

    def _post_nav_manipulation(self, nav: Navigation) -> Navigation:
        from mkdocs.structure.nav import _add_previous_and_next_links, _add_parent_links, _get_by_type

        # Get only the pages from the navigation, ignoring any sections and links.
        nav.pages = _get_by_type(nav, Page)

        # Include next, previous and parent links.
        _add_previous_and_next_links(nav.pages)
        _add_parent_links(nav)

        return nav

    def on_config(self, config: MkDocsConfig, **kwargs):
        self.config["api_path"] = self.config["api_path"].rstrip("/")
        self.config["api_title"] = self.config["api_title"] or self.config["api_path"].rsplit("/", 1)[-1].title()
        self.docs_dir = config["docs_dir"]
        self.site_url = config.get("site_url", config["site_dir"])
        overrides = self._remove_none_items(dict(
            pdocs=self.config["pdocs"],
            modules=self.config["modules"],
            output_dir=self.config["output_dir"],
            **self.config["portray"]
        ))

        self.project_config = project(self.config["project_root"], self.config["config_file"], **overrides)
        if "output_dir" not in self.project_config["pdocs"]:
            self.project_config["pdocs"]["output_dir"] = os.path.join(config["docs_dir"], self.config["api_path"])

        # disable "unrecognized relative link" warning for API links
        self._add_ignore_api_relative_link_log_messages()

    def on_pre_build(self, config: MkDocsConfig, **kwargs):
        pdocs(
            self.project_config["pdocs"],
            self.project_config["compress_package_names_for_reference_documentation"],
            modules=self.project_config["modules"],
        )
        if config["nav"]:
            self._replace_nav_placeholder(config)

    def on_nav(self, nav: Navigation, config: MkDocsConfig, files: Files):
        if self.nav_already_fixed:
            return nav

        reference_section_index = 0
        reference_section_title = self.config["api_title"]

        # remove reference item (Link or Section) from the navigation
        for idx in range(len(nav.items) - 1, -1, -1):
            if nav.items[idx].title == self.config["api_title"] or nav.items[idx].url == REFERENCE_PLACEHOLDER:
                reference_section_title = nav.items.pop(idx).title
                reference_section_index = idx
                break

        # remove all pages according to api_path from the navigation
        for idx in range(len(nav.pages) - 1, -1, -1):
            if nav.pages[idx].url.startswith(self.config["api_path"]):
                nav.pages.pop(idx)

        # get API doc pages including their formated labels in correct order
        reference_docs = self._get_api_nav_part(config["docs_dir"])

        # build and add reference section
        reference_section = self._convert_to_section(reference_section_title, reference_docs, config, files)
        nav.items.insert(reference_section_index, reference_section)
        return self._post_nav_manipulation(nav)

    def on_page_content(self, html: str, page: Page, config: MkDocsConfig, files: Files) -> str | None:
        handled: List[str] = []
        for match in HTML_LINK_REGEX.finditer(html):
            href = match.group("href")
            if href.startswith("pdoc:") and not handled.__contains__(href):
                html = html.replace(href, self._resolve_link(href[5:], files))
                handled.append(href)
        return html
