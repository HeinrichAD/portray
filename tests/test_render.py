import pathlib

from hypothesis_auto import auto_test
from portray import render


def test_mkdocs_config():
    auto_test(
        render._mkdocs_config,
        auto_allow_exceptions_=(render._mkdocs_exceptions.ConfigurationError,),
    )


def test_document_sort(temporary_dir):
    temporary_dir = pathlib.Path(temporary_dir)

    # create dummy files: a.md, b.md, c.md, ..., z.md
    files = []
    for i in range(ord("a"), ord("z") + 1):
        file = temporary_dir.joinpath(f"{chr(i)}.md")
        file.touch()
        files.append(str(file))

    # sort files (no index.md)
    docs = render._sorted_docs(temporary_dir)
    assert docs == files

    # add index.md
    file = temporary_dir.joinpath("index.md")
    file.touch()
    files.insert(0, str(file))  # index.md have to be first!

    # sort files (with index.md)
    docs = render._sorted_docs(temporary_dir)
    assert docs == files


def test_nested_modules_extraction():
    assert render._remove_nested_modules(["a.b"]) == ["a.b"]
    assert render._remove_nested_modules(["a.b", "a.b.d"]) == ["a.b"]
    assert render._remove_nested_modules(["a.b.c", "a.b.d"]) == ["a.b.c", "a.b.d"]
    assert render._remove_nested_modules(["a.b.c", "a.b.d", "a"]) == ["a"]
    assert render._remove_nested_modules(["a.b", "a.b.c", "b"]) == ["a.b", "b"]
    assert render._remove_nested_modules(["a", "ab.c", "ab.d", "abc"]) == [
        "a",
        "ab.c",
        "ab.d",
        "abc",
    ]
    assert render._remove_nested_modules(["aa_bb", "aa_cc", "aa_dd"]) == ["aa_bb", "aa_cc", "aa_dd"]
