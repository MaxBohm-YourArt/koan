"""Blank-line layout for text bound for a Slack Block Kit ``markdown`` block.

Why this exists
---------------
Kōan's missions, skills and reports are authored as *plain-text files*: bare
uppercase section labels, bullet lists glued to the line above them, a closing
sentence glued to the end of a list. That reads fine in a terminal or a vault
note, where every newline is a line break.

A Block Kit ``markdown`` block follows standard Markdown, where a lone newline
is a **soft wrap**, not a break. So the same text arrives in Slack as one dense
grey paragraph with nothing for the eye to anchor on. The older mrkdwn ``text``
field preserved single newlines, which is why output read as airier before the
markdown-block switch — the density is a side effect of that change, not of the
content getting terser.

This module inserts the blank lines Markdown actually needs: a paragraph break
between consecutive prose lines, and air around block-level elements. Runs of
list items, table rows and blockquote lines stay tight, because Markdown already
breaks those correctly and spacing them out would be worse, not better.

Fenced code is copied through byte-for-byte — indentation is significant there.

The transform is idempotent: text already laid out this way is returned
unchanged, so it is safe to apply on a retry path or to already-formatted input.
"""

import re
from typing import List

# A fence toggles verbatim mode. Matched loosely: an LLM may indent it slightly.
_FENCE_RE = re.compile(r"^\s{0,3}(?:```|~~~)")

# Block-level starts. Markdown renders each of these on its own line already,
# so they never need a hard break — only surrounding air.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_LIST_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s)")
_QUOTE_RE = re.compile(r"^\s{0,3}>")
_TABLE_RE = re.compile(r"^\s*\|")
_RULE_RE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$")

# Kinds whose consecutive lines belong to one construct and must stay adjacent.
_TIGHT_KINDS = frozenset({"list", "table", "quote"})


def _classify(line: str, in_list: bool) -> str:
    """Name the Markdown construct ``line`` begins.

    ``in_list`` lets an indented continuation line be recognised as part of the
    preceding list item rather than as fresh prose — inserting a blank line
    there would sever the item from its own continuation.
    """
    if _RULE_RE.match(line):
        return "rule"
    if _HEADING_RE.match(line):
        return "heading"
    if _LIST_RE.match(line):
        return "list"
    if _QUOTE_RE.match(line):
        return "quote"
    if _TABLE_RE.match(line):
        return "table"
    if in_list and line[:1].isspace():
        return "list"
    return "prose"


def lay_out_markdown(text: str) -> str:
    """Return ``text`` with the blank lines a Markdown renderer needs.

    Args:
        text: Agent-authored Markdown, typically written with single newlines.

    Returns:
        The same content with paragraph breaks inserted between prose lines and
        around block elements, runs of list/table/quote lines left tight, fenced
        code preserved verbatim, runs of blank lines collapsed to one, and no
        leading or trailing blank lines.
    """
    if not text:
        return text

    out: List[str] = []
    in_fence = False
    in_list = False
    prev_kind = ""

    for raw in text.split("\n"):
        line = raw.rstrip()

        if _FENCE_RE.match(line):
            if not in_fence and out and out[-1] != "":
                out.append("")
            in_fence = not in_fence
            out.append(line)
            prev_kind = "fence"
            in_list = False
            continue

        if in_fence:
            out.append(raw)  # verbatim: leading whitespace is meaningful
            continue

        if not line:
            if out and out[-1] != "":
                out.append("")
            # A blank line does not end a list: an indented continuation may
            # still follow it (a "loose" list). prev_kind is kept so the next
            # line is still judged against the construct it belongs to.
            continue

        kind = _classify(line, in_list)

        # Air between constructs, but never inside one.
        needs_blank = (
            out
            and out[-1] != ""
            and not (kind in _TIGHT_KINDS and kind == prev_kind)
        )
        if needs_blank:
            out.append("")

        out.append(line)
        prev_kind = kind
        in_list = kind == "list"

    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)
