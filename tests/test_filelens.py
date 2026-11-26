"""Tests for the vendored FileLens tool.

Imports retargeted to harness/filelens/; the assertions are the original suite,
unedited, so the vendored copy is held to the same behaviour it shipped with.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempfile
import textwrap
from pathlib import Path
from harness.filelens.filelens import FileLens
from harness.filelens.outline import build_outline
from harness.filelens.search import search_file
from harness.filelens.summarize import summarize_file


SAMPLE_PY = textwrap.dedent(
    """\
    import os
    import sys

    class UserAuth:
        def __init__(self, db):
            self.db = db

        def login(self, email, password):
            return self.db.check(email, password)

        def logout(self, user_id):
            self.db.invalidate(user_id)

    def main():
        auth = UserAuth(db=None)
        print(auth)
"""
)


def _make_file(content: str, suffix: str = ".py") -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
    f.write(content)
    f.close()
    return f.name


def test_outline_detects_class():
    path = _make_file(SAMPLE_PY)
    outline = build_outline(path)
    names = [n.name for n in outline.nodes]
    assert "UserAuth" in names


def test_outline_detects_methods():
    path = _make_file(SAMPLE_PY)
    outline = build_outline(path)
    user_auth = next(n for n in outline.nodes if n.name == "UserAuth")
    method_names = [c.name for c in user_auth.children]
    assert "login" in method_names
    assert "logout" in method_names


def test_outline_detects_top_level_function():
    path = _make_file(SAMPLE_PY)
    outline = build_outline(path)
    names = [n.name for n in outline.nodes]
    assert "main" in names


def test_outline_render_returns_string():
    path = _make_file(SAMPLE_PY)
    outline = build_outline(path)
    rendered = outline.render()
    assert isinstance(rendered, str)
    assert "UserAuth" in rendered


def test_outline_markdown():
    md = "# Heading 1\n\n## Heading 2\n\nSome text\n\n### Heading 3\n"
    path = _make_file(md, suffix=".md")
    outline = build_outline(path)
    assert outline.language == "Markdown"
    headings = [n.name for n in outline.nodes]
    assert "Heading 1" in headings
    assert "Heading 2" in headings


def test_search_finds_match():
    path = _make_file(SAMPLE_PY)
    results = search_file(path, "login")
    assert len(results) >= 1
    assert any("login" in r.line for r in results)


def test_search_includes_context():
    path = _make_file(SAMPLE_PY)
    results = search_file(path, "login", context_lines=2)
    assert results[0].context_before or results[0].context_after


def test_search_no_match():
    path = _make_file(SAMPLE_PY)
    results = search_file(path, "xyzzy_nonexistent")
    assert results == []


def test_search_regex():
    path = _make_file(SAMPLE_PY)
    results = search_file(path, r"def \w+", use_regex=True)
    assert len(results) >= 3  # __init__, login, logout, main


def test_chunk_returns_correct_lines():
    path = _make_file(SAMPLE_PY)
    lens = FileLens(path)
    result = lens.chunk(1, 2)
    assert "import os" in result
    assert "import sys" in result


def test_chunk_includes_line_numbers():
    path = _make_file(SAMPLE_PY)
    lens = FileLens(path)
    result = lens.chunk(1, 1)
    assert "1" in result


def test_summarize_contains_structure():
    path = _make_file(SAMPLE_PY)
    summary = summarize_file(path)
    assert "STRUCTURE" in summary
    assert "UserAuth" in summary


def test_summarize_contains_head():
    path = _make_file(SAMPLE_PY)
    summary = summarize_file(path)
    assert "HEAD" in summary
    assert "import os" in summary


def test_filelens_file_not_found():
    import pytest

    with pytest.raises(FileNotFoundError):
        FileLens("/nonexistent/path/file.py")


def test_filelens_all_modes():
    path = _make_file(SAMPLE_PY)
    lens = FileLens(path)
    assert lens.outline() is not None
    assert isinstance(lens.search("login"), list)
    assert isinstance(lens.chunk(1, 5), str)
    assert isinstance(lens.summarize(), str)


# --- added in verdict-bench: cover the branches the original suite left open ---


SAMPLE_MD = textwrap.dedent(
    """\
    # Title

    Some prose.

    ## Section A

    More prose.

    ### Sub A1
    """
)


def test_outline_handles_markdown():
    path = _make_file(SAMPLE_MD, suffix=".md")
    outline = build_outline(path).render()
    assert "Title" in outline
    assert "Section A" in outline
    assert "Sub A1" in outline


def test_summarize_reports_language_and_size():
    path = _make_file(SAMPLE_PY, ".py")
    summary = summarize_file(path)
    assert "LANGUAGE" in summary
    assert "python" in summary.lower()


def test_search_regex_mode():
    path = _make_file(SAMPLE_PY)
    hits = FileLens(path).search(r"def \w+", use_regex=True)
    assert hits, "regex search must match methods"
    assert all("def" in h.line for h in hits)


def test_search_returns_empty_for_absent_query():
    path = _make_file(SAMPLE_PY)
    assert FileLens(path).search("nonexistent_token_xyz") == []


def test_chunk_clamps_out_of_range_bounds():
    path = _make_file(SAMPLE_PY)
    text = FileLens(path).chunk(0, 9999)
    assert text.strip(), "clamped chunk must still return content"
    assert FileLens(path).chunk(1, 0).strip() == ""


def test_chunk_line_numbers_are_present():
    path = _make_file(SAMPLE_PY)
    line = FileLens(path).chunk(1, 1).split("\n")[0]
    assert line.strip().startswith("1")
