import os
import shutil

from hypothesis_auto import auto_test
from portray import config, exceptions

REPO_URL = "https://github.com/timothycrosley/portray"

FAKE_SETUP_FILE = """
from setuptools import setup

setup(name='fake',
      version='0.1',
      description='Fake package for tesitng pourposes',
      author='Flying Circus',
      author_email='flyingcircus@example.com',
      license='MIT',
      packages=['fake'])
"""

FAKE_PYPROJECT_TOML_FLIT = """
[tool.flit.metadata]
module = "preconvert"
author = "Timothy Edmund Crosley"
author-email = "timothy.crosley@gmail.com"
home-page = "https://github.com/timothycrosley/preconvert"
requires-python=">=3.5"
description-file="README.md"
classifiers=[
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Topic :: Software Development :: Libraries :: Python Modules",
]

[tool.portray.pdoc3]
just = "kidding"

[tool.portray]
extra_markdown_extensions = ["smarty"]
"""

FAKE_MKDOCS_YML_FILE = """
site_name: Fake mkdocs site
theme:
  name: material
  features:
    - content.code.annotation
    - navigation.tabs
    - search.highlight
    - toc.integrate
  language: en
  palette:
    - scheme: default
      toggle:
        icon: material/toggle-switch-off-outline
        name: Switch to dark mode
      primary: teal
      accent: purple
    - scheme: slate
      toggle:
        icon: material/toggle-switch
        name: Switch to light mode
      primary: teal
      accent: lime
markdown_extensions:
  - admonition
  - attr_list
  - footnotes
  - md_in_html
"""


def test_project_properties(project_dir):
    auto_test(config.project, auto_allow_exceptions_=(exceptions.NoProjectFound,))
    auto_test(
        config.project, directory=project_dir, auto_allow_exceptions_=(exceptions.NoProjectFound,)
    )


def test_project_setup_py(temporary_dir):
    with open(os.path.join(temporary_dir, "setup.py"), "w") as setup_file:
        setup_file.write(FAKE_SETUP_FILE)

    project_config = config.project(directory=temporary_dir, config_file="")
    assert project_config["modules"] == ["fake"]


def test_project_flit_setup(temporary_dir):
    with open(os.path.join(temporary_dir, "pyproject.toml"), "w") as setup_file:
        setup_file.write(FAKE_PYPROJECT_TOML_FLIT)

    project_config = config.project(directory=temporary_dir, config_file="pyproject.toml")
    assert project_config["modules"] == ["preconvert"]


def test_setup_py_properties():
    auto_test(config.setup_py)


def test_toml_properties():
    auto_test(config.toml)


def test_mkdocs_properties():
    auto_test(config.mkdocs)


def test_mkdocs_properties_with_custom_mkdocs_file(temporary_dir, project_dir, chdir):
    with chdir(temporary_dir):
        temp_project_dir = os.path.join(temporary_dir, "portray")
        shutil.copytree(project_dir, temp_project_dir)
        with chdir(temp_project_dir):
            with open(os.path.join(temp_project_dir, "mkdocs.yml"), "w") as mkdocs_file:
                mkdocs_file.write(FAKE_MKDOCS_YML_FILE)
            auto_test(config.mkdocs, temp_project_dir)


def test_pdocs_properties():
    auto_test(config.pdocs)


def test_repository_properties():
    auto_test(config.repository)


def test_repository_custom_config(project_dir):
    assert config.repository(project_dir, repo_url=REPO_URL) == {
        "edit_uri": "edit/main/",
        "repo_name": "portray",
        "repo_url": REPO_URL,
    }

    assert config.repository(project_dir, repo_name="different_name", repo_url=REPO_URL) == {
        "edit_uri": "edit/main/",
        "repo_name": "different_name",
        "repo_url": REPO_URL,
    }

    assert config.repository(project_dir, edit_uri="edit/develop/", repo_url=REPO_URL) == {
        "edit_uri": "edit/develop/",
        "repo_name": "portray",
        "repo_url": REPO_URL,
    }

    assert config.repository(
        project_dir, repo_url="https://github.com/timothycrosley/examples"
    ) == {
        "edit_uri": "edit/main/",
        "repo_name": "examples",
        "repo_url": "https://github.com/timothycrosley/examples",
    }

    assert config.repository(
        project_dir, repo_url="https://bitbucket.org/atlassian/stash-example-plugin.git"
    ) == {
        "edit_uri": "src/default/docs/",
        "repo_name": "stash-example-plugin",
        "repo_url": "https://bitbucket.org/atlassian/stash-example-plugin",
    }

    assert config.repository(
        project_dir, repo_url="git@bitbucket.org:atlassian/stash-example-plugin.git"
    ) == {
        "edit_uri": "src/default/docs/",
        "repo_name": "stash-example-plugin",
        "repo_url": "https://bitbucket.org/atlassian/stash-example-plugin",
    }

    assert config.repository(project_dir, repo_url="not_actually_a_valid_url") == {
        "repo_name": "not_actually_a_valid_url",
        "repo_url": "not_actually_a_valid_url",
    }

    assert config.repository(
        project_dir, repo_url="https://gitlab.ci.token:password@gitlab.net/app.git"
    ) == {"edit_uri": "edit/main/", "repo_name": "app", "repo_url": "https://gitlab.net/app"}


def test_repository_no_config_no_repository(temporary_dir):
    assert config.repository(temporary_dir) == {}
