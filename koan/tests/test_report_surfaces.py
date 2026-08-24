"""Tests for the report-surface id store (key -> provider surface id)."""

import json

import pytest

from app.messaging.report_surfaces import ReportSurfaceStore


@pytest.fixture
def store(tmp_path):
    return ReportSurfaceStore(tmp_path / ".report-surfaces.json")


class TestRoundTrip:
    def test_unknown_key_returns_none(self, store):
        assert store.get("ops-digest") is None

    def test_put_then_get(self, store):
        store.put("ops-digest", "F123", url="https://slack/canvas/F123")
        assert store.get("ops-digest") == "F123"
        assert store.url("ops-digest") == "https://slack/canvas/F123"

    def test_put_persists_across_instances(self, store, tmp_path):
        store.put("ops-digest", "F123")
        reopened = ReportSurfaceStore(tmp_path / ".report-surfaces.json")
        assert reopened.get("ops-digest") == "F123"

    def test_put_overwrites_same_key(self, store):
        store.put("ops-digest", "F123")
        store.put("ops-digest", "F456")
        assert store.get("ops-digest") == "F456"

    def test_keys_are_independent(self, store):
        store.put("ops-digest", "F1")
        store.put("pr-report", "F2")
        assert store.get("ops-digest") == "F1"
        assert store.get("pr-report") == "F2"

    def test_forget_drops_only_that_key(self, store):
        store.put("ops-digest", "F1")
        store.put("pr-report", "F2")
        store.forget("ops-digest")
        assert store.get("ops-digest") is None
        assert store.get("pr-report") == "F2"

    def test_forget_unknown_key_is_a_noop(self, store):
        store.forget("never-existed")  # must not raise

    def test_url_defaults_to_empty_string(self, store):
        store.put("ops-digest", "F123")
        assert store.url("ops-digest") == ""

    def test_url_of_unknown_key_is_empty(self, store):
        assert store.url("nope") == ""


class TestImmutability:
    def test_put_does_not_mutate_a_previously_returned_snapshot(self, store):
        store.put("a", "F1")
        before = store.snapshot()
        store.put("b", "F2")
        assert before == {"a": {"surface_id": "F1", "url": ""}}

    def test_snapshot_mutation_does_not_affect_the_store(self, store):
        store.put("a", "F1")
        snap = store.snapshot()
        snap["a"]["surface_id"] = "TAMPERED"
        assert store.get("a") == "F1"


class TestFailOpen:
    """A surface id is a cache, never truth — a broken store must never raise."""

    def test_corrupt_json_reads_as_empty(self, tmp_path):
        p = tmp_path / ".report-surfaces.json"
        p.write_text("{not json at all")
        assert ReportSurfaceStore(p).get("ops-digest") is None

    def test_corrupt_json_is_recoverable_by_writing(self, tmp_path):
        p = tmp_path / ".report-surfaces.json"
        p.write_text("{not json at all")
        s = ReportSurfaceStore(p)
        s.put("ops-digest", "F1")
        assert s.get("ops-digest") == "F1"

    def test_wrong_shape_reads_as_empty(self, tmp_path):
        p = tmp_path / ".report-surfaces.json"
        p.write_text(json.dumps(["not", "a", "dict"]))
        assert ReportSurfaceStore(p).get("ops-digest") is None

    def test_legacy_flat_string_values_are_tolerated(self, tmp_path):
        """Forward-compat: a bare id where a dict is expected must not crash."""
        p = tmp_path / ".report-surfaces.json"
        p.write_text(json.dumps({"surfaces": {"ops-digest": "F123"}}))
        assert ReportSurfaceStore(p).get("ops-digest") == "F123"

    def test_unwritable_path_does_not_raise(self, tmp_path):
        s = ReportSurfaceStore(tmp_path / "no-such-dir" / "x.json")
        s.put("ops-digest", "F1")  # must not raise
        assert s.get("ops-digest") is None

    def test_missing_file_reads_as_empty(self, tmp_path):
        assert ReportSurfaceStore(tmp_path / "absent.json").get("k") is None
