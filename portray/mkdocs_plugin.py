import atexit
import logging
import math
import os
import re
import shutil
from tempfile import mkdtemp
from typing import Dict, List, Literal, Optional, Tuple, Union

from mkdocs.config import base
from mkdocs.config import config_options as c
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.plugins import BasePlugin, get_plugin_logger
from mkdocs.structure import StructureItem
from mkdocs.structure.files import Files, get_files
from mkdocs.structure.nav import Navigation, Section
from mkdocs.structure.pages import Page
from pdocs.extract import ExtractError, extract_module
from portray.config import PDOCS_DEFAULTS, PORTRAY_DEFAULTS, project
from portray.render import _nested_docs, _remove_nested_modules, pdocs

HTML_LINK_REGEX = re.compile(r"<a[^>]*href=[\"']?(?P<href>[^\"' >]*)[\"']?[^>]*>")
REFERENCE_PLACEHOLDER = "$references"

MkDocsConfigNav = List[Dict[str, Union[str, "MkDocsConfigNav"]]]


@atexit.register
def cleanup():
    if MkdocsPlugin.temp_dir:
        # remove temporary directory including generated API doc files
        shutil.rmtree(MkdocsPlugin.temp_dir, ignore_errors=True)


class PortrayOptions(base.Config):
    append_directory_to_python_path = c.Type(
        bool, default=PORTRAY_DEFAULTS["append_directory_to_python_path"]
    )
    include_reference_documentation = c.Type(
        bool, default=PORTRAY_DEFAULTS["include_reference_documentation"]
    )
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
    Python API documentation plugin for MkDocs.

    This plugin generates API documentation using pdoc and adds it to the navigation.
    Furthermore, it replaces links to pdoc documentation with links to the generated documentation.

    References:

    - <https://github.com/spirali/mkdocs-pdoc-plugin>
    - <https://www.mkdocs.org/dev-guide/plugins>
    - <https://github.com/HeinrichAD/portray/tree/develop>
    - <https://github.com/HeinrichAD/pdocs/tree/develop>

    Known issues:

    - only support to have the API references as root item inside the navigation
    """

    code_modification_time = 0.0
    root_packages: Optional[List[str]] = None
    temp_dir: Optional[str] = None

    def __init__(self):
        self.dirtyreload = False  # mkdocs flag "--dirtyreload"; see on_startup
        self.nav_already_fixed = False
        self.logger = get_plugin_logger(__name__)

    def _remove_none_items(self, dictionary: dict, *, recursive: bool = True) -> dict:
        return {
            k: self._remove_none_items(v) if recursive and isinstance(v, dict) else v
            for k, v in dictionary.items()
            if v is not None
        }

    def _add_ignore_api_relative_link_log_messages(self):
        # disable "unrecognized relative link" warning for API links
        pattern = r"Doc file '{}/.+\.md' contains an unrecognized relative link '.+/', it was left as is\.( Did you mean '.+\.md'\?)?"
        pattern = pattern.format(self.config["api_path"])
        pattern = re.compile(pattern)

        class IgnoreAPIRelativeLinkWarningFilter(logging.Filter):
            def filter(self, record):
                return not pattern.match(record.getMessage())

        logging.getLogger("mkdocs.structure.pages").addFilter(IgnoreAPIRelativeLinkWarningFilter())

    def _check_if_code_changed(self) -> bool:
        latest_code_modification_time = max(
            self._get_latest_modification_time(module_path)
            for module_path in self._get_module_path(self.project_config["modules"])
        )
        generate_code_references = (
            latest_code_modification_time > self.__class__.code_modification_time
            and not math.isclose(latest_code_modification_time, self.code_modification_time)
        )
        self.__class__.code_modification_time = latest_code_modification_time
        return generate_code_references

    def _get_latest_modification_time(self, path: str) -> float:
        latest_modification_time = 0.0
        for root, _, files in os.walk(path):
            for file in files:
                file_path = os.path.join(root, file)
                file_modification_time = os.path.getmtime(file_path)
                if file_modification_time > latest_modification_time:
                    latest_modification_time = file_modification_time
        return latest_modification_time

    def _get_module_path(self, module_names: List[str]) -> List[str]:
        if not module_names:
            return []
        try:
            return [extract_module(module_name).module.__path__[0] for module_name in module_names]
        except ExtractError as error:
            self.logger.error(error)
            return []

    def _get_api_nav_part(self) -> MkDocsConfigNav:
        # get API doc pages including their formated labels in correct order
        return _nested_docs(
            self.project_config["pdocs"]["output_dir"], self.api_dir, self.project_config
        )

    def _replace_nav_placeholder(self, config: MkDocsConfig):
        if not config["nav"]:
            return

        # search and replace "$references" placeholder
        for idx in range(len(config["nav"])):
            key, value = next(iter(config["nav"][idx].items()))
            if value == REFERENCE_PLACEHOLDER:
                # get API doc pages including their formated labels in correct order
                config["nav"][idx][key] = self._get_api_nav_part()
                self.nav_already_fixed = True
                return

    def _get_api_files(self, config: MkDocsConfig) -> Files:
        original_docs_dir = config["docs_dir"]
        try:
            config["docs_dir"] = self.api_dir
            return get_files(config)
        finally:
            config["docs_dir"] = original_docs_dir

    def _get_navigation_api_item_title_and_position(self, nav: Navigation) -> Tuple[str, int]:
        reference_section_title = self.config["api_title"]

        # search for existing reference item, drop it and remember its title and navigation index/position
        for idx in range(len(nav.items) - 1, -1, -1):
            if nav.items[idx].title == reference_section_title or (
                hasattr(nav.items[idx], "url") and nav.items[idx].url == REFERENCE_PLACEHOLDER
            ):
                return nav.items.pop(idx).title, idx

        # if reference item was not found, search for the correct position alphabetically by its title or file name
        for idx in range(len(nav.items)):
            title = nav.items[idx].title or nav.items[idx].file.name.title()
            if title > reference_section_title:
                return reference_section_title, idx

        # if the correct alphabetically order of the API reference is at the end
        return reference_section_title, len(nav.items)

    def _convert_to_section(
        self, title: str, refs: MkDocsConfigNav, config: MkDocsConfig, files: Files
    ) -> Section:
        children: List[StructureItem] = []
        for ref in refs:
            key, value = tuple(ref.items())[0]
            if isinstance(value, str):
                children.append(Page(key, files.src_paths[value], config))
            else:
                children.append(self._convert_to_section(key, value, config, files))
        return Section(title, children)

    def _post_nav_manipulation(self, nav: Navigation) -> Navigation:
        from mkdocs.structure.nav import (
            _add_parent_links,
            _add_previous_and_next_links,
            _get_by_type,
        )

        # Get only the pages from the navigation, ignoring any sections and links.
        nav.pages = _get_by_type(nav, Page)

        # Include next, previous and parent links.
        _add_previous_and_next_links(nav.pages)
        _add_parent_links(nav)

        return nav

    def _resolve_qname(self, qualified_name: str) -> Tuple[str, ...]:
        qname = qualified_name.replace(".", "/")
        if self.project_config["compress_package_names_for_reference_documentation"]:
            cls = self.__class__
            if not cls.root_packages or not self.dirtyreload:
                cls.root_packages = _remove_nested_modules(self.project_config["modules"])
            for root_package in cls.root_packages:
                qname = qname.replace(root_package.replace(".", "/"), root_package)
        return tuple(qname.split("/"))

    def _resolve_link(self, qualified_name: str, files: Files) -> str:
        qname = self._resolve_qname(qualified_name)
        original_qname = qname
        while qname:
            url = f"{self.config['api_path']}/{'/'.join(qname)}/"
            if any((True for file in files._files if file.url == url)):
                path = os.path.join(self.site_url, url)
                rest = original_qname[len(qname) :]
                if rest:
                    return path + "#" + ".".join(rest)
                else:
                    return path
            qname = qname[:-1]

        self.logger.error(f"Invalid reference: {qualified_name}")
        return qualified_name

    def on_startup(self, *, command: Literal["build", "gh-deploy", "serve"], dirty: bool) -> None:
        self.dirtyreload = dirty

    def on_config(self, config: MkDocsConfig, **kwargs):
        self.config["api_path"] = self.config["api_path"].rstrip("/")
        self.config["api_title"] = (
            self.config["api_title"] or self.config["api_path"].rsplit("/", 1)[-1].title()
        )
        self.api_dir = self.config["output_dir"]
        if not self.api_dir:
            if not self.__class__.temp_dir:
                self.__class__.temp_dir = mkdtemp()
            self.api_dir = self.__class__.temp_dir

        self.docs_dir = config["site_dir"] or config["docs_dir"]
        self.site_url = config.get("site_url", config["site_dir"])
        overrides = self._remove_none_items(
            dict(
                pdocs=self.config["pdocs"],
                modules=self.config["modules"],
                output_dir=self.api_dir,
                **self.config["portray"],
            )
        )
        self.project_config = project(
            self.config["project_root"], self.config["config_file"], **overrides
        )
        self.project_config["pdocs"]["output_dir"] = os.path.join(
            self.api_dir, self.config["api_path"]
        )

        # disable "unrecognized relative link" warning for API links
        self._add_ignore_api_relative_link_log_messages()

    def on_pre_build(self, config: MkDocsConfig, **kwargs):
        if self._check_if_code_changed():
            # code changes detected, generate new API documentation
            pdocs(
                self.project_config["pdocs"],
                self.project_config["compress_package_names_for_reference_documentation"],
                modules=self.project_config["modules"],
            )
        else:
            # no code changes detected
            self.logger.info("No code changes detected. Skipping code reference generation.")

        # collect API doc files
        self.api_files = self._get_api_files(config)
        if config["nav"]:
            self._replace_nav_placeholder(config)

    def on_files(self, files: Files, *, config: MkDocsConfig) -> Files | None:
        # add API doc files to the MkDocs file collection
        # these files are not automatically added by MkDocs since they are not located in the docs directory
        for file in self.api_files:
            files.append(file)
        return files

    def on_nav(self, nav: Navigation, config: MkDocsConfig, files: Files):
        if self.nav_already_fixed:
            return nav

        # get API doc pages including their formated labels in correct order
        reference_docs = self._get_api_nav_part()

        # build and add reference section
        (
            reference_section_title,
            reference_section_index,
        ) = self._get_navigation_api_item_title_and_position(nav)
        reference_section = self._convert_to_section(
            reference_section_title, reference_docs, config, files
        )
        nav.items.insert(reference_section_index, reference_section)
        return self._post_nav_manipulation(nav)

    def on_page_content(
        self, html: str, page: Page, config: MkDocsConfig, files: Files
    ) -> str | None:
        # replace all links to pdoc documentation with links to the generated documentation
        # use on_page_content instead of on_page_markdown since the latter would also replace links in code blocks
        handled: List[str] = []
        for match in HTML_LINK_REGEX.finditer(html):
            href = match.group("href")
            if href.startswith("pdoc:") and not handled.__contains__(href):
                html = html.replace(href, self._resolve_link(href[5:], files))
                handled.append(href)
        return html
