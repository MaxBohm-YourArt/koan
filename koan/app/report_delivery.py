"""Deliver a report to a persistent channel surface, with text fallback.

This is the caller side of the ``publish_report`` capability described in
``specs/components/messaging.md``. It owns three decisions the provider must not
make for itself:

1. whether report surfaces are enabled at all (``messaging.reports.enabled``),
2. remembering which surface a report key already owns
   (``instance/.report-surfaces.json``),
3. how loudly to announce an update (``messaging.reports.notify``).

**A report is never dropped.** Every failure path returns False, which obliges
the caller to send the report as an ordinary message instead.
"""

import sys
from pathlib import Path
from typing import Optional

from app.messaging.base import ReportRef
from app.messaging.report_surfaces import DEFAULT_STORE_NAME, ReportSurfaceStore
from app.notify import NotificationPriority, send_telegram


def deliver_report(
    key: str,
    title: str,
    body: str,
    *,
    priority: NotificationPriority = NotificationPriority.ACTION,
) -> bool:
    """Publish ``body`` to the report surface named ``key``.

    Args:
        key: Stable report identifier (e.g. "ops-digest").
        title: Human-readable surface title.
        body: Report body as Markdown.
        priority: Priority for the pointer message (the surface write itself is
            not a notification and is never priority-filtered).

    Returns:
        True if the report is fully delivered and the caller need do nothing
        more. False means **the caller must still send ``body`` as an ordinary
        message** — which covers both "no surface was available" (disabled,
        unsupported, failed publish) and the deliberate ``notify: full`` mode,
        where the surface *was* updated and the text is wanted as well.
    """
    from app.config import get_report_notify_mode, get_report_surfaces_enabled

    if not get_report_surfaces_enabled():
        return False

    store = _store()
    known_id = store.get(key) if store is not None else None

    ref = _publish(key, title, body, known_id)
    if ref is None:
        return False

    if store is not None:
        store.put(ref.key, ref.surface_id, url=ref.url)

    mode = get_report_notify_mode()
    if mode == "silent":
        return True
    if mode == "full":
        # Surface updated *and* the whole report posted — deliberately noisy,
        # for operators migrating off message-only reports.
        return False

    return _send_pointer(title, ref, priority)


def _publish(
    key: str, title: str, body: str, surface_id: Optional[str]
) -> Optional[ReportRef]:
    """Ask the active provider to create-or-update the surface.

    Returns None on any failure, including an unsupported provider. Errors are
    logged, never raised — the caller's fallback is the safety net.
    """
    try:
        from app.messaging import get_messaging_provider
        provider = get_messaging_provider()
        # Same layout pass applied to ordinary sends: a lone newline is a soft
        # wrap in Markdown, so constructs need blank lines between them.
        return provider.publish_report(key, title, _lay_out(body), surface_id)
    # Broad by contract: an unavailable surface must degrade to text, not raise.
    except Exception as e:
        _log("error", f"Report surface publish failed for '{key}': {e}")
        return None


def _lay_out(body: str) -> str:
    """Apply the shared Markdown blank-line layout pass, if it is available.

    ``messaging.markdown_layout`` inserts blank lines *between* Markdown
    constructs (a lone newline is only a soft wrap, so a list glued to a
    paragraph may not render). It is a separate, optional concern: when the
    module is absent the body is published unchanged, which is exactly the
    behaviour of a plain message today.
    """
    try:
        from app.messaging.markdown_layout import lay_out_markdown
        return lay_out_markdown(body)
    except ImportError:
        return body


def _send_pointer(
    title: str, ref: ReportRef, priority: NotificationPriority
) -> bool:
    """Announce the update with a short message carrying the permalink.

    Canvases and pinned messages are silent, so without this the human would
    never learn the report had been refreshed.
    """
    pointer = f"📊 *{title}* — updated"
    if ref.url:
        pointer = f"{pointer}\n{ref.url}"
    try:
        return bool(send_telegram(pointer, priority=priority))
    # A failed pointer is not a failed report: the surface already holds the
    # content, so re-sending the whole body would duplicate it.
    except Exception as e:
        _log("error", f"Report pointer message failed for '{ref.key}': {e}")
        return True


def _store() -> Optional[ReportSurfaceStore]:
    """Build the surface store, or None if ``KOAN_ROOT`` can't be resolved.

    Path resolution mirrors ``notify_dedup.py`` — the other piece of persistent
    bridge state living under ``instance/``.
    """
    try:
        import os
        root = os.environ.get("KOAN_ROOT", "")
        if not root:
            return None
        return ReportSurfaceStore(Path(root) / "instance" / DEFAULT_STORE_NAME)
    # Surface ids are a cache; losing the store degrades to always creating a
    # new surface, which is recoverable, so this must not raise.
    except Exception as e:
        _log("error", f"Report surface store unavailable: {e}")
        return None


def _log(level: str, message: str) -> None:
    """Log via the bridge logger without importing it at module import time."""
    try:
        from app.bridge_log import log
        log(level, message)
    # The logger itself failed — stderr is the last resort, so a report problem
    # is never completely invisible.
    except Exception:
        print(f"[report_delivery] {message}", file=sys.stderr)
