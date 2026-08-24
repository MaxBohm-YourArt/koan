"""Tests for `[report:key]` outbox routing to a persistent report surface.

Covers the marker parser and the flush-time routing decision, including the
mandatory degradation to an ordinary message when the surface is unavailable
(see `specs/components/messaging.md` — "Never load-bearing").
"""

from unittest.mock import patch

import pytest

# Imported so `patch("app.report_delivery.deliver_report")` can resolve the
# attribute — outbox_manager imports it lazily inside the flush path.
import app.report_delivery  # noqa: F401
from app.report_delivery import ReportDelivery
from app.outbox_manager import OutboxManager, parse_outbox_report


class TestParseMarker:
    def test_no_marker_returns_none_key_and_untouched_body(self):
        key, title, body = parse_outbox_report("Just a normal message.")
        assert key is None
        assert title == ""
        assert body == "Just a normal message."

    def test_marker_with_title(self):
        key, title, body = parse_outbox_report(
            "[report:ops-digest] Ops Digest — 2026-08-24\n## ACT TODAY\n- merge #463"
        )
        assert key == "ops-digest"
        assert title == "Ops Digest — 2026-08-24"
        assert body == "## ACT TODAY\n- merge #463"

    def test_marker_without_title_falls_back_to_a_readable_key(self):
        key, title, body = parse_outbox_report("[report:ops-digest]\nbody here")
        assert key == "ops-digest"
        assert title == "Ops Digest"
        assert body == "body here"

    def test_marker_is_stripped_from_the_body(self):
        _, _, body = parse_outbox_report("[report:x] T\ncontent")
        assert "[report:" not in body

    def test_first_marker_wins_when_a_flush_batches_several(self):
        key, title, _ = parse_outbox_report(
            "[report:first] One\nbody\n[report:second] Two\nmore"
        )
        assert key == "first"
        assert title == "One"

    def test_all_markers_are_stripped_even_though_the_first_wins(self):
        _, _, body = parse_outbox_report(
            "[report:first] One\nbody\n[report:second] Two\nmore"
        )
        assert "[report:" not in body

    def test_marker_may_follow_leading_blank_lines(self):
        key, _, _ = parse_outbox_report("\n\n[report:ops-digest] T\nbody")
        assert key == "ops-digest"

    def test_uppercase_key_is_not_a_marker(self):
        """Keys are lowercase by contract — anything else stays literal text."""
        key, _, body = parse_outbox_report("[report:OPS] T\nbody")
        assert key is None
        assert body.startswith("[report:OPS]")

    def test_key_charset_allows_dots_dashes_underscores(self):
        key, _, _ = parse_outbox_report("[report:pr_report.v2-b] T\nx")
        assert key == "pr_report.v2-b"

    def test_marker_mid_paragraph_is_not_a_marker(self):
        text = "See the [report:x] marker syntax."
        key, _, body = parse_outbox_report(text)
        assert key is None
        assert body == text

    def test_empty_body_is_tolerated(self):
        key, title, body = parse_outbox_report("[report:ops-digest] Title only")
        assert key == "ops-digest"
        assert title == "Title only"
        assert body == ""


@pytest.fixture
def outbox_env(tmp_path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    outbox_file = instance_dir / "outbox.md"
    outbox_file.write_text("")
    conv_file = instance_dir / "conversation.jsonl"
    return OutboxManager(outbox_file, instance_dir, conv_file), outbox_file, instance_dir


@pytest.fixture
def flush_env(outbox_env):
    """Patch out everything slow/network in the flush path."""
    mgr, outbox_file, instance_dir = outbox_env
    with patch("app.outbox_manager.log"), \
         patch("app.outbox_manager.save_conversation_message"), \
         patch("app.outbox_manager.scan_and_log") as scan, \
         patch("app.outbox_manager.fallback_format", side_effect=lambda t: t), \
         patch("app.active_mission.is_mission_active", return_value=True):
        scan.return_value.blocked = False
        yield mgr, outbox_file, instance_dir


class TestFlushRouting:
    def test_report_is_published_and_body_is_not_sent_as_a_message(self, flush_env):
        mgr, outbox_file, _ = flush_env
        outbox_file.write_text("[report:ops-digest] Ops Digest\n## ACT TODAY\n- merge")

        with patch("app.report_delivery.deliver_report", return_value=ReportDelivery(True)) as deliver, \
             patch("app.outbox_manager.send_telegram") as send:
            mgr.flush()

        deliver.assert_called_once()
        args, kwargs = deliver.call_args
        assert args[0] == "ops-digest"
        assert args[1] == "Ops Digest"
        assert "## ACT TODAY" in args[2]
        send.assert_not_called()

    def test_unavailable_surface_falls_back_to_a_plain_message(self, flush_env):
        mgr, outbox_file, _ = flush_env
        outbox_file.write_text("[report:ops-digest] Ops Digest\n## ACT TODAY")

        with patch("app.report_delivery.deliver_report", return_value=ReportDelivery(False)), \
             patch("app.outbox_manager.send_telegram", return_value=True) as send:
            mgr.flush()

        send.assert_called_once()
        assert "## ACT TODAY" in send.call_args[0][0]

    def test_fallback_message_never_leaks_the_marker(self, flush_env):
        mgr, outbox_file, _ = flush_env
        outbox_file.write_text("[report:ops-digest] Ops Digest\nbody")

        with patch("app.report_delivery.deliver_report", return_value=ReportDelivery(False)), \
             patch("app.outbox_manager.send_telegram", return_value=True) as send:
            mgr.flush()

        assert "[report:" not in send.call_args[0][0]

    def test_report_body_bypasses_the_ai_formatter(self, flush_env):
        """A report is already a formatted document — never let Claude rewrite it."""
        mgr, outbox_file, _ = flush_env
        outbox_file.write_text("[report:ops-digest] Ops Digest\n## VERBATIM")

        with patch("app.report_delivery.deliver_report", return_value=ReportDelivery(False)), \
             patch("app.outbox_manager.send_telegram", return_value=True) as send, \
             patch.object(mgr, "_format_message") as fmt:
            mgr.flush()

        fmt.assert_not_called()
        assert "## VERBATIM" in send.call_args[0][0]

    def test_ordinary_content_still_goes_through_the_normal_path(self, flush_env):
        mgr, outbox_file, _ = flush_env
        outbox_file.write_text("Plain notification")

        with patch("app.report_delivery.deliver_report") as deliver, \
             patch("app.outbox_manager.send_telegram", return_value=True) as send:
            mgr.flush()

        deliver.assert_not_called()
        send.assert_called_once()

    def test_priority_marker_and_report_marker_compose(self, flush_env):
        mgr, outbox_file, _ = flush_env
        outbox_file.write_text(
            "[priority:urgent]\n[report:ops-digest] Ops Digest\nbody"
        )

        with patch("app.report_delivery.deliver_report", return_value=ReportDelivery(True)) as deliver:
            mgr.flush()

        from app.notify import NotificationPriority
        assert deliver.call_args.kwargs["priority"] is NotificationPriority.URGENT

    def test_successful_publish_clears_the_staging_file(self, flush_env):
        mgr, outbox_file, _ = flush_env
        outbox_file.write_text("[report:ops-digest] T\nbody")

        with patch("app.report_delivery.deliver_report", return_value=ReportDelivery(True)):
            mgr.flush()

        assert not mgr.staging_path.exists()

    def test_delivery_crash_degrades_to_a_plain_message(self, flush_env):
        """deliver_report must never be able to lose a report by raising."""
        mgr, outbox_file, _ = flush_env
        outbox_file.write_text("[report:ops-digest] T\n## BODY")

        with patch("app.report_delivery.deliver_report", side_effect=RuntimeError("boom")), \
             patch("app.outbox_manager.send_telegram", return_value=True) as send:
            mgr.flush()

        send.assert_called_once()
        assert "## BODY" in send.call_args[0][0]

    def test_github_refs_are_expanded_before_publishing(self, flush_env):
        """Enrichment still applies — a canvas deserves real links too."""
        mgr, outbox_file, _ = flush_env
        outbox_file.write_text("[report:ops-digest] T\nmerge #463")

        with patch("app.report_delivery.deliver_report", return_value=ReportDelivery(True)) as deliver, \
             patch.object(
                 OutboxManager, "_expand_github_refs",
                 return_value="merge https://gh/x/pull/463",
             ):
            mgr.flush()

        assert deliver.call_args[0][2] == "merge https://gh/x/pull/463"


class TestMarkerIndentTolerance:
    """A prompt that renders the marker indented must still route (see the
    regex comment) — the failure mode is a leaked `[report:…]` line in chat."""

    @pytest.mark.parametrize("indent", ["", " ", "  ", "   "])
    def test_up_to_three_leading_spaces_still_routes(self, indent):
        key, title, body = parse_outbox_report(f"{indent}[report:ops-digest] T\nbody")
        assert key == "ops-digest"
        assert title == "T"
        assert body == "body"

    def test_four_leading_spaces_is_an_indented_code_block_not_a_marker(self):
        key, _, _ = parse_outbox_report("    [report:ops-digest] T\nbody")
        assert key is None

    def test_a_tab_indent_is_not_a_marker(self):
        key, _, _ = parse_outbox_report("\t[report:ops-digest] T\nbody")
        assert key is None


class TestFullModeMessage:
    def test_footer_is_appended_to_the_message(self, flush_env):
        mgr, outbox_file, _ = flush_env
        outbox_file.write_text("[report:ops-digest] T\n## ACT TODAY")
        outcome = ReportDelivery(False, footer="\n\n— [report surface updated](https://x)")

        with patch("app.report_delivery.deliver_report", return_value=outcome), \
             patch("app.outbox_manager.send_telegram", return_value=True) as send:
            mgr.flush()

        sent = send.call_args[0][0]
        assert "## ACT TODAY" in sent
        assert "https://x" in sent

    def test_no_footer_leaves_the_message_untouched(self, flush_env):
        mgr, outbox_file, _ = flush_env
        outbox_file.write_text("[report:ops-digest] T\n## ACT TODAY")

        with patch("app.report_delivery.deliver_report", return_value=ReportDelivery(False)), \
             patch("app.outbox_manager.send_telegram", return_value=True) as send:
            mgr.flush()

        assert send.call_args[0][0] == "## ACT TODAY"
