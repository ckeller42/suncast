import dataclasses
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from suncast.models import DailyRatio, ForecastSeries, PanelConfig


class Store:
    def __init__(self, path: str):
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        # Reentrant: public methods may call each other (get_panel -> set_panel).
        self._lock = threading.RLock()

        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                day TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                panel TEXT NOT NULL,
                provider TEXT NOT NULL,
                hourly TEXT NOT NULL,
                daily TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ratios (
                day TEXT PRIMARY KEY,
                forecast_wh REAL NOT NULL,
                actual_wh REAL NOT NULL,
                ratio REAL NOT NULL,
                snapshot_id INT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS panel (
                id INTEGER PRIMARY KEY CHECK(id=1),
                json TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def save_snapshot(self, s: ForecastSeries, lat: float, lon: float, panel: PanelConfig) -> int:
        with self._lock:
            # Snapshot day = fetched_at UTC date
            day = s.fetched_at.date().isoformat()

            # Hourly: [[iso_ts, watts], ...]
            hourly = [[p.ts.isoformat(), p.watts] for p in s.points]
            hourly_json = json.dumps(hourly)

            # Daily: JSON object
            daily_json = json.dumps(s.daily_wh)

            # Panel: JSON from dataclass
            panel_json = json.dumps(dataclasses.asdict(panel))

            cursor = self.conn.execute(
                """
                INSERT INTO snapshots (created_at, day, lat, lon, panel, provider, hourly, daily)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    s.fetched_at.isoformat(),
                    day,
                    lat,
                    lon,
                    panel_json,
                    s.provider,
                    hourly_json,
                    daily_json,
                ),
            )
            self.conn.commit()
            return cursor.lastrowid

    def snapshot_forecast_wh(self, day: str) -> float | None:
        """Return daily_wh[day] from EARLIEST snapshot created on that day."""
        with self._lock:
            cursor = self.conn.execute(
                """
                SELECT daily FROM snapshots WHERE day = ? ORDER BY created_at ASC LIMIT 1
                """,
                (day,),
            )
            row = cursor.fetchone()
            if row is None:
                return None

            daily_wh = json.loads(row[0])
            return daily_wh.get(day)

    def snapshot_hourly_for_day(self, day: str) -> dict[str, float] | None:
        """Hourly forecast {iso_ts: watts} from the EARLIEST snapshot of `day`."""
        with self._lock:
            cursor = self.conn.execute(
                "SELECT hourly FROM snapshots WHERE day = ? ORDER BY created_at ASC LIMIT 1",
                (day,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {ts: watts for ts, watts in json.loads(row[0])}

    def snapshot_id_for_day(self, day: str) -> int | None:
        """Id of the earliest snapshot created on `day` (UTC), or None."""
        with self._lock:
            cursor = self.conn.execute(
                "SELECT id FROM snapshots WHERE day = ? ORDER BY created_at ASC LIMIT 1", (day,)
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def has_snapshot_today(self, day: str) -> bool:
        with self._lock:
            cursor = self.conn.execute("SELECT 1 FROM snapshots WHERE day = ? LIMIT 1", (day,))
            return cursor.fetchone() is not None

    def save_ratio(self, r: DailyRatio, snapshot_id: int) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO ratios (day, forecast_wh, actual_wh, ratio, snapshot_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (r.day, r.forecast_wh, r.actual_wh, r.ratio, snapshot_id),
            )
            self.conn.commit()

    def ratios(self, limit: int = 90) -> list[DailyRatio]:
        """Newest first."""
        with self._lock:
            cursor = self.conn.execute(
                """
                SELECT day, forecast_wh, actual_wh, ratio FROM ratios
                ORDER BY day DESC LIMIT ?
                """,
                (limit,),
            )
            return [
                DailyRatio(day=row[0], forecast_wh=row[1], actual_wh=row[2], ratio=row[3])
                for row in cursor.fetchall()
            ]

    def has_ratio(self, day: str) -> bool:
        with self._lock:
            cursor = self.conn.execute("SELECT 1 FROM ratios WHERE day = ? LIMIT 1", (day,))
            return cursor.fetchone() is not None

    def get_panel(self) -> PanelConfig:
        with self._lock:
            cursor = self.conn.execute("SELECT json FROM panel WHERE id = 1")
            row = cursor.fetchone()

            if row is None:
                # Auto-create defaults row
                default_panel = PanelConfig()
                self.set_panel(default_panel)
                return default_panel

            return PanelConfig(**json.loads(row[0]))

    def set_panel(self, p: PanelConfig) -> None:
        with self._lock:
            panel_json = json.dumps(dataclasses.asdict(p))
            self.conn.execute(
                """
                INSERT OR REPLACE INTO panel (id, json)
                VALUES (1, ?)
                """,
                (panel_json,),
            )
            self.conn.commit()

    def last_snapshot_age_s(self, now: datetime) -> float | None:
        with self._lock:
            cursor = self.conn.execute(
                "SELECT created_at FROM snapshots ORDER BY created_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row is None:
                return None

            created_at = datetime.fromisoformat(row[0])
            delta = now - created_at
            return delta.total_seconds()
