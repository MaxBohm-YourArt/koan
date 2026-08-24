"""Persistent ``key -> provider surface id`` map for report surfaces.

A *report surface* is a persistent, named document in the messaging channel (a
Slack canvas, a pinned Telegram message, a replaced Matrix event). Republishing
under the same key must land on the same surface, which means the provider-side
id has to outlive the process that created it.

**Surface ids are cache, not truth** (see ``specs/components/messaging.md``). If
a stored id has gone away — canvas deleted, message unpinned — the provider
creates a fresh surface and overwrites the entry. Every operation here is
therefore fail-open: a corrupt, unreadable, or unwritable store degrades to
"no id known", never to an exception in the outbox flush path.
"""

import json
import sys
from pathlib import Path
from typing import Dict, Optional

from app.utils import atomic_write

# Store filename, relative to the instance directory.
DEFAULT_STORE_NAME = ".report-surfaces.json"

_SURFACES_KEY = "surfaces"


class ReportSurfaceStore:
    """Fail-open JSON store mapping a report key to its provider surface id.

    Args:
        path: Location of the JSON file (typically
            ``instance/.report-surfaces.json``). Neither the file nor its parent
            directory needs to exist.
    """

    def __init__(self, path: Path):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def snapshot(self) -> Dict[str, Dict[str, str]]:
        """Return a deep copy of the current mapping.

        Callers own the result and may mutate it freely — it is disconnected
        from the store.
        """
        return {
            key: dict(entry)
            for key, entry in self._read().items()
        }

    def get(self, key: str) -> Optional[str]:
        """Return the surface id for ``key``, or None if unknown."""
        entry = self._read().get(key)
        return entry.get("surface_id") or None if entry else None

    def url(self, key: str) -> str:
        """Return the last-known permalink for ``key`` ("" if unknown)."""
        entry = self._read().get(key)
        return entry.get("url", "") if entry else ""

    def put(self, key: str, surface_id: str, url: str = "") -> None:
        """Record (or replace) the surface id for ``key``.

        Builds a new mapping rather than mutating the loaded one, so a snapshot
        handed out earlier is never altered underneath its owner.
        """
        current = self._read()
        updated = {
            **current,
            key: {"surface_id": surface_id, "url": url},
        }
        self._write(updated)

    def forget(self, key: str) -> None:
        """Drop ``key``'s entry. A no-op if it was never recorded."""
        current = self._read()
        if key not in current:
            return
        self._write({k: v for k, v in current.items() if k != key})

    # --- internals -------------------------------------------------------

    def _read(self) -> Dict[str, Dict[str, str]]:
        """Load the mapping, resolving any problem to an empty mapping."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        surfaces = raw.get(_SURFACES_KEY)
        if not isinstance(surfaces, dict):
            return {}
        return {
            str(key): _coerce_entry(value)
            for key, value in surfaces.items()
            if _coerce_entry(value) is not None
        }

    def _write(self, surfaces: Dict[str, Dict[str, str]]) -> None:
        """Persist the mapping. A failure is logged, never raised."""
        try:
            atomic_write(
                self._path,
                json.dumps({_SURFACES_KEY: surfaces}, indent=2) + "\n",
            )
        # Broad by contract: a store write must never break the outbox flush.
        except Exception as e:
            _log_write_failure(self._path, e)


def _coerce_entry(value) -> Optional[Dict[str, str]]:
    """Normalize one stored value into ``{"surface_id", "url"}``.

    Tolerates a bare id string where a dict is expected, so a hand-edited or
    older store still resolves instead of poisoning the whole read.
    """
    if isinstance(value, str):
        return {"surface_id": value, "url": ""}
    if isinstance(value, dict):
        surface_id = value.get("surface_id")
        if isinstance(surface_id, str) and surface_id:
            return {"surface_id": surface_id, "url": str(value.get("url", ""))}
    return None


def _log_write_failure(path: Path, error: Exception) -> None:
    """Report a store write failure without importing the bridge logger eagerly."""
    try:
        from app.bridge_log import log
        log("error", f"Report surface store write failed ({path}): {error}")
    # The logger itself failed — stderr is the last resort. Logging must never
    # be the thing that breaks the fail-open path.
    except Exception:
        print(f"[report_surfaces] store write failed ({path}): {error}",
              file=sys.stderr)
