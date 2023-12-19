import os

from logging import getLogger
from mkdocs.config import base, config_options as c
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.plugins import BasePlugin
from mkdocs.structure import StructureItem
from mkdocs.structure.files import Files
from mkdocs.structure.nav import Navigation, Section
from mkdocs.structure.pages import Page
from portray.config import project, PDOCS_DEFAULTS, PORTRAY_DEFAULTS
from portray.render import _nested_docs, pdocs
import re
from typing import Dict, List, Tuple, Union


PDOC_LINK_REGEX = re.compile(r"\[([^]]*)]\(pdoc:([^)]*)\)")

QualName = Tuple[str, ...]
PdocReferences = List[Dict[str, Union[str, "PdocReferences"]]]


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

    - only support root items in navigation
    - while using a manual set navigation console output contains:
        - info message: file not included in the "nav" configuration
        - warning message: relative path to '$references' [...] not found in the documentation files
    """

    def __init__(self):
        self.docs_dir: str = ""
        self.site_url: str = ""
        self.project_config: dict = dict()
        self.logger = getLogger(__name__)

    def _qualname_to_filename(self, qname: QualName) -> str:
        p = "/".join(qname)
        return os.path.join(self.config["api_path"], f"{p}.html")

    def _resolve_link(self, qname: QualName) -> str:
        original_qname = qname
        while qname:
            path = self._qualname_to_filename(qname)
            filename = os.path.join(self.docs_dir, path)
            if os.path.isfile(filename):
                path = os.path.join(self.site_url, path)
                rest = original_qname[len(qname):]
                if rest:
                    return path + "#" + ".".join(rest)
                else:
                    return path
            qname = qname[:-1]

        p = ".".join(original_qname)
        self.logger.error(f"Invalid reference: {p}")
        return f"!!! Unresolved path to: {p}"

    def _remove_none_items(self, dictionary: dict, *, recursive: bool = True) -> dict:
        return {
            k: self._remove_none_items(v) if recursive and isinstance(v, dict) else v
            for k, v in dictionary.items()
            if v is not None
        }

    def _convert_to_section(self, title: str, refs: PdocReferences, config: MkDocsConfig, files: Files) -> Section:
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
        self.config["api_title"] = self.config["api_title"] or self.config["api_path"].title()
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

    def on_page_markdown(self, markdown: str, *, page: Page, config: MkDocsConfig, files: Files) -> str | None:
        links: List[Tuple[str, str]] = []
        for match in PDOC_LINK_REGEX.finditer(markdown):
            links.append((match.group(0), match.group(1)))

        for name, path in links:
            qname = tuple(path.split("."))
            target_name = name or (qname[-1] if qname else "")
            markdown = markdown.replace(
                f"[{name}](pdoc:{path})",
                f"[{target_name}]({self._resolve_link(qname)})",
            )
        return markdown

    def on_pre_build(self, config: MkDocsConfig, **kwargs):
        pdocs(
            self.project_config["pdocs"],
            self.project_config["compress_package_names_for_reference_documentation"],
            modules=self.project_config["modules"],
        )

    def on_nav(self, nav: Navigation, config: MkDocsConfig, files: Files):
        reference_section_index = 0
        reference_section_title = self.config["api_title"]

        # remove reference item (Link or Section) from the navigation
        for idx in range(len(nav.items) - 1, -1, -1):
            if nav.items[idx].title == self.config["api_title"] or nav.items[idx].url == "$references":
                reference_section_title = nav.items.pop(idx).title
                reference_section_index = idx
                break

        # remove all pages according to api_path from the navigation
        for idx in range(len(nav.pages) - 1, -1, -1):
            if nav.pages[idx].url.startswith(self.config["api_path"]):
                nav.pages.pop(idx)

        # get API doc pages including their formated labels in correct order
        reference_docs: PdocReferences = _nested_docs(
            self.project_config["pdocs"]["output_dir"],
            config["docs_dir"],
            self.project_config
        )

        # build and add reference section
        reference_section = self._convert_to_section(reference_section_title, reference_docs, config, files)
        nav.items.insert(reference_section_index, reference_section)
        return self._post_nav_manipulation(nav)
