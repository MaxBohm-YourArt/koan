"""Tests for blank-line layout of Slack-bound Markdown."""

from app.messaging.markdown_layout import lay_out_markdown


def test_consecutive_prose_lines_become_paragraphs():
    """The core fix: single newlines collapse in Markdown, so prose needs air."""
    out = lay_out_markdown("First sentence.\nSecond sentence.")
    assert out == "First sentence.\n\nSecond sentence."


def test_list_runs_stay_tight():
    """Markdown already breaks list items; spacing them out would read worse."""
    out = lay_out_markdown("- one\n- two\n- three")
    assert out == "- one\n- two\n- three"


def test_blank_line_inserted_between_prose_and_list():
    """Also a correctness fix: a list glued to a paragraph may not render."""
    out = lay_out_markdown("ACT TODAY\n- merge #463")
    assert out == "ACT TODAY\n\n- merge #463"


def test_prose_after_list_is_separated():
    out = lay_out_markdown("- one\n- two\nNo review requested of you.")
    assert out == "- one\n- two\n\nNo review requested of you."


def test_heading_gets_air_on_both_sides():
    out = lay_out_markdown("intro\n## Section\nbody")
    assert out == "intro\n\n## Section\n\nbody"


def test_numbered_lists_stay_tight():
    out = lay_out_markdown("1. first\n2. second")
    assert out == "1. first\n2. second"


def test_table_rows_stay_tight():
    src = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert lay_out_markdown(src) == src


def test_blockquote_lines_stay_tight():
    src = "> quoted one\n> quoted two"
    assert lay_out_markdown(src) == src


def test_indented_list_continuation_is_not_severed():
    """An indented continuation belongs to its item, not to a new paragraph."""
    src = "- item one\n  continued here\n- item two"
    assert lay_out_markdown(src) == src


def test_fenced_code_is_preserved_verbatim():
    """Indentation and internal blank lines inside a fence are significant."""
    src = "before\n```python\ndef f():\n\n    return 1\n```\nafter"
    out = lay_out_markdown(src)
    assert "```python\ndef f():\n\n    return 1\n```" in out
    assert out.startswith("before\n\n```")
    assert out.endswith("```\n\nafter")


def test_tilde_fences_are_honoured():
    src = "~~~\n  indented\n~~~"
    assert "  indented" in lay_out_markdown(src)


def test_runs_of_blank_lines_collapse_to_one():
    out = lay_out_markdown("a\n\n\n\nb")
    assert out == "a\n\nb"


def test_leading_and_trailing_blanks_are_stripped():
    assert lay_out_markdown("\n\nbody\n\n\n") == "body"


def test_trailing_whitespace_per_line_is_stripped():
    assert lay_out_markdown("a   \nb\t") == "a\n\nb"


def test_transform_is_idempotent():
    src = "## Report\n\nOne finding.\n\n- alpha\n- beta\n\nDone."
    assert lay_out_markdown(lay_out_markdown(src)) == lay_out_markdown(src)


def test_single_line_is_untouched():
    """Lifecycle one-liners are the common case and must not gain padding."""
    assert lay_out_markdown("✅ Active — ready to work") == "✅ Active — ready to work"


def test_empty_and_whitespace_only_input():
    assert lay_out_markdown("") == ""
    assert lay_out_markdown("\n\n") == ""


def test_horizontal_rule_gets_air():
    out = lay_out_markdown("above\n---\nbelow")
    assert out == "above\n\n---\n\nbelow"


def test_real_digest_shape_becomes_readable():
    """The shape Kōan actually produced on 2026-08-14."""
    src = (
        "ACT TODAY\n"
        "- marketplace#463 — approved, CLEAN, green: merge it.\n"
        "- marketplace#408 — approved, CLEAN, green since Jul 28.\n"
        "\n"
        "WAITING ON ME\n"
        "- marketplace#408 — 17d\n"
        "No review requested of you. No red CI anywhere."
    )
    out = lay_out_markdown(src)
    assert "ACT TODAY\n\n- marketplace#463" in out
    assert "WAITING ON ME\n\n- marketplace#408" in out
    assert "— 17d\n\nNo review requested" in out
    # list runs themselves stay tight
    assert "merge it.\n- marketplace#408" in out
