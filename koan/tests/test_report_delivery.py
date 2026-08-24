"""Tests for report delivery: the enable gate, notify modes, and text fallback."""

from unittest.mock import MagicMock, patch

import pytest

from app.messaging.base import ReportRef
from app.notify import NotificationPriority


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A resolvable KOAN_ROOT plus a stub provider that publishes successfully."""
    (tmp_path / "instance").mkdir()
    monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
    provider = MagicMock()
    provider.publish_report.return_value = ReportRef(
        key="ops-digest", surface_id="F1", url="https://slack/docs/F1",
    )
    return tmp_path, provider


def _run(provider, *, enabled=True, mode="pointer", key="ops-digest",
         title="Ops Digest", body="## Body"):
    from app.report_delivery import deliver_report
    # Pinned to `canvas`: these cases were written for the shape whose publish is
    # silent and therefore needs a pointer message. `upload` is covered by
    # TestUploadShapeDelivery.
    with patch("app.messaging.get_messaging_provider", return_value=provider), \
         patch("app.config.get_report_surfaces_enabled", return_value=enabled), \
         patch("app.config.get_report_notify_mode", return_value=mode), \
         patch("app.config.get_report_surface_kind", return_value="canvas"), \
         patch("app.report_delivery.send_telegram", return_value=True) as send:
        result = deliver_report(key, title, body, priority=NotificationPriority.ACTION)
    return result, send


class TestEnableGate:
    def test_disabled_never_touches_the_provider(self, env):
        _, provider = env
        result, send = _run(provider, enabled=False)
        assert result.handled is False
        provider.publish_report.assert_not_called()
        send.assert_not_called()


class TestNotifyModes:
    def test_pointer_posts_a_short_message_with_the_link(self, env):
        _, provider = env
        result, send = _run(provider, mode="pointer")
        assert result.handled is True
        sent = send.call_args[0][0]
        assert "Ops Digest" in sent
        assert "https://slack/docs/F1" in sent
        assert "## Body" not in sent

    def test_silent_posts_nothing(self, env):
        _, provider = env
        result, send = _run(provider, mode="silent")
        assert result.handled is True
        send.assert_not_called()

    def test_full_publishes_and_asks_the_caller_to_send_the_text(self, env):
        _, provider = env
        result, send = _run(provider, mode="full")
        assert result.handled is False           # caller still sends the body
        provider.publish_report.assert_called_once()   # but the surface updated

    def test_pointer_omits_the_link_when_the_provider_has_no_url(self, env):
        _, provider = env
        provider.publish_report.return_value = ReportRef("ops-digest", "F1", url="")
        result, send = _run(provider)
        assert result.handled is True
        assert "\n" not in send.call_args[0][0]


class TestFallback:
    def test_unsupported_provider_falls_back(self, env):
        _, provider = env
        provider.publish_report.return_value = None
        result, send = _run(provider)
        assert result.handled is False
        send.assert_not_called()

    def test_provider_exception_falls_back(self, env):
        _, provider = env
        provider.publish_report.side_effect = RuntimeError("boom")
        result, _ = _run(provider)
        assert result.handled is False

    def test_failed_pointer_does_not_resend_the_whole_report(self, env):
        """The surface already holds the content — a retry would duplicate it."""
        from app.report_delivery import deliver_report
        _, provider = env
        with patch("app.messaging.get_messaging_provider", return_value=provider), \
             patch("app.config.get_report_surfaces_enabled", return_value=True), \
             patch("app.config.get_report_notify_mode", return_value="pointer"), \
             patch("app.report_delivery.send_telegram", side_effect=RuntimeError("net")):
            assert deliver_report("ops-digest", "T", "body").handled is True


class TestSurfaceIdReuse:
    def test_first_publish_passes_no_surface_id(self, env):
        _, provider = env
        _run(provider)
        assert provider.publish_report.call_args[0][3] is None

    def test_second_publish_reuses_the_recorded_surface_id(self, env):
        _, provider = env
        _run(provider)
        _run(provider)
        assert provider.publish_report.call_args[0][3] == "F1"

    def test_a_recreated_surface_replaces_the_recorded_id(self, env):
        _, provider = env
        _run(provider)
        provider.publish_report.return_value = ReportRef("ops-digest", "F2", url="")
        _run(provider)
        _run(provider)
        assert provider.publish_report.call_args[0][3] == "F2"

    def test_distinct_keys_get_distinct_surfaces(self, env):
        _, provider = env
        _run(provider, key="ops-digest")
        provider.publish_report.return_value = ReportRef("pr-report", "F9", url="")
        _run(provider, key="pr-report")
        _run(provider, key="ops-digest")
        assert provider.publish_report.call_args[0][3] == "F1"

    def test_missing_koan_root_still_publishes(self, env, monkeypatch):
        """No store means no id reuse — degraded, not broken."""
        _, provider = env
        monkeypatch.delenv("KOAN_ROOT", raising=False)
        result, _ = _run(provider)
        assert result.handled is True
        assert provider.publish_report.call_args[0][3] is None


class TestFullModeFooter:
    """`full` mode must carry the surface link, or there is no way to tell from
    the channel whether the surface actually updated."""

    def test_full_mode_returns_a_link_footer(self, env):
        _, provider = env
        result, _ = _run(provider, mode="full")
        assert result.handled is False
        assert "https://slack/docs/F1" in result.footer

    def test_footer_is_omitted_when_the_provider_exposes_no_url(self, env):
        from app.messaging.base import ReportRef
        _, provider = env
        provider.publish_report.return_value = ReportRef("ops-digest", "F1", url="")
        result, _ = _run(provider, mode="full")
        assert result.handled is False
        assert result.footer == ""

    def test_pointer_mode_has_no_footer(self, env):
        _, provider = env
        result, _ = _run(provider, mode="pointer")
        assert result.footer == ""

    def test_unavailable_surface_has_no_footer(self, env):
        _, provider = env
        provider.publish_report.return_value = None
        result, _ = _run(provider)
        assert result.footer == ""


def _run_kind(provider, kind, mode):
    from app.report_delivery import deliver_report
    with patch("app.messaging.get_messaging_provider", return_value=provider), \
         patch("app.config.get_report_surfaces_enabled", return_value=True), \
         patch("app.config.get_report_notify_mode", return_value=mode), \
         patch("app.config.get_report_surface_kind", return_value=kind), \
         patch("app.report_delivery.send_telegram", return_value=True) as send:
        return deliver_report("ops-digest", "Ops Digest", "## Body"), send


class TestUploadShapeDelivery:
    """With `upload` the shared artifact card is already the channel message."""

    def test_upload_pointer_posts_no_duplicate_message(self, env):
        _, provider = env
        result, send = _run_kind(provider, "upload", "pointer")
        assert result.handled is True
        send.assert_not_called()

    def test_upload_silent_posts_nothing_extra(self, env):
        _, provider = env
        result, send = _run_kind(provider, "upload", "silent")
        assert result.handled is True
        send.assert_not_called()

    def test_upload_full_still_asks_for_the_text_with_a_link(self, env):
        _, provider = env
        result, send = _run_kind(provider, "upload", "full")
        assert result.handled is False
        assert "https://slack/docs/F1" in result.footer

    def test_canvas_pointer_still_posts_the_pointer(self, env):
        """The canvas publish is silent, so it needs the message."""
        _, provider = env
        result, send = _run_kind(provider, "canvas", "pointer")
        assert result.handled is True
        send.assert_called_once()

    def test_canvas_silent_posts_nothing(self, env):
        _, provider = env
        result, send = _run_kind(provider, "canvas", "silent")
        assert result.handled is True
        send.assert_not_called()
