from __future__ import annotations

from dualforge.ui.compat_dialog import _DOC, render_compat_markdown

_SAMPLE = """\
# Title

Intro line.

## Section

- one
- two

| Engine | Format | Verified |
| --- | --- | --- |
| Unity | .assets | yes |

```text
code
here
```

> note text
"""


def test_render_compat_markdown_headers():
    rendered = render_compat_markdown(_SAMPLE)
    assert "█ Title" in rendered
    assert "── Section ──" in rendered


def test_render_compat_markdown_table_keeps_cells():
    rendered = render_compat_markdown(_SAMPLE)
    assert "Unity   .assets   yes" in rendered
    assert "Engine   Format   Verified" in rendered
    assert "---" not in rendered


def test_render_compat_markdown_lists_and_blockquote():
    rendered = render_compat_markdown(_SAMPLE)
    assert "• one" in rendered
    assert "• two" in rendered
    assert "▸ note text" in rendered


def test_render_compat_markdown_code_block_kept():
    rendered = render_compat_markdown(_SAMPLE)
    assert "code" in rendered
    assert "here" in rendered


def test_compat_doc_exists_and_readable():
    assert _DOC.exists(), "docs/COMPATIBILITY.md is missing"
    rendered = render_compat_markdown(_DOC.read_text(encoding="utf-8"))
    assert "Compatibility matrix" in rendered