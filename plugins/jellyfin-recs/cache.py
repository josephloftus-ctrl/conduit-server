"""SQLite cache for Jellyfin recommendation results.

Stores LLM-generated recommendation rows so the tvOS app gets instant
responses.  The heartbeat pipeline checks ``needs_refresh()`` before
calling the LLM, and the REST endpoint reads from ``get_latest()``.

Follows the ``_RadarDB`` pattern from the release-radar plugin.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("conduit.plugin.jellyfin-recs.cache")


class RecsCache:
    """Persistent SQLite store for recommendation snapshots."""

    def __init__(self, data_dir: str) -> None:
        data_path = Path(data_dir).expanduser()
        data_path.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(data_path / "recs.db"))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS recommendations (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        TEXT NOT NULL,
                generated_at   TEXT NOT NULL,
                profile_hash   TEXT NOT NULL,
                rows_json      TEXT NOT NULL
            )"""
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get_latest(self, user_id: str) -> dict | None:
        """Return the most recent recommendation snapshot for *user_id*.

        Returns ``{generated_at, profile_hash, rows}`` or ``None``.
        """
        row = self._conn.execute(
            "SELECT generated_at, profile_hash, rows_json "
            "FROM recommendations WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "generated_at": row["generated_at"],
            "profile_hash": row["profile_hash"],
            "rows": json.loads(row["rows_json"]),
        }

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def store(self, user_id: str, profile_hash: str, rows: list[dict]) -> None:
        """Persist a new recommendation snapshot and prune old entries."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO recommendations (user_id, generated_at, profile_hash, rows_json) "
            "VALUES (?, ?, ?, ?)",
            (user_id, now, profile_hash, json.dumps(rows)),
        )
        # Keep only the 3 most recent rows per user_id.
        self._conn.execute(
            "DELETE FROM recommendations WHERE user_id = ? AND id NOT IN ("
            "  SELECT id FROM recommendations WHERE user_id = ? "
            "  ORDER BY id DESC LIMIT 3"
            ")",
            (user_id, user_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Staleness check
    # ------------------------------------------------------------------
    def needs_refresh(
        self, user_id: str, current_hash: str, max_age_hours: int = 24
    ) -> bool:
        """Return ``True`` when recommendations should be regenerated.

        Triggers when:
        - No cached result exists for *user_id*.
        - The cached ``profile_hash`` differs from *current_hash* (viewing
          profile changed since last generation).
        - The cached result is older than *max_age_hours*.
        """
        latest = self.get_latest(user_id)
        if latest is None:
            return True
        if latest["profile_hash"] != current_hash:
            return True
        generated = datetime.fromisoformat(latest["generated_at"])
        age = datetime.now(timezone.utc) - generated
        if age.total_seconds() >= max_age_hours * 3600:
            return True
        return False
