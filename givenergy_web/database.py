"""SD-Card-Safe SQLite WAL Time-Series Database with RAM Buffering and Multi-Tier Rollups."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from givenergy_web.models import PlantSnapshot


class HistoricDatabase:
    """Manages historic time-series storage with batched writes to protect SD card longevity."""

    def __init__(self, db_path: str | Path, flush_interval_s: float = 60.0) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.flush_interval_s = flush_interval_s
        self._lock = threading.Lock()

        # In-memory ring buffer for live 5-second points (last 3 hours = 2,160 points)
        self._live_ring: deque[dict[str, Any]] = deque(maxlen=2160)

        # Accumulator for current 1-minute bucket
        self._current_minute_key: str | None = None
        self._minute_samples: list[PlantSnapshot] = []

        # Accumulator for current 5-minute bucket
        self._current_5m_key: str | None = None
        self._five_min_samples: list[PlantSnapshot] = []

        # Pending SQL operations queued in RAM, flushed to disk once per minute
        self._pending_1m_rows: list[tuple] = []
        self._pending_5m_rows: list[tuple] = []
        self._pending_daily_upserts: dict[str, tuple] = {}
        self._last_flush_mono = time.monotonic()
        self.total_disk_flushes = 0
        self.total_samples_recorded = 0

        self._init_schema()
        self._seed_live_ring()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.execute("PRAGMA cache_size=-8000;")  # ~8MB page cache in RAM
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS telemetry_1m (
                        ts TEXT PRIMARY KEY,
                        epoch INTEGER NOT NULL,
                        date_str TEXT NOT NULL,
                        solar_w INTEGER NOT NULL,
                        pv1_w INTEGER NOT NULL,
                        pv2_w INTEGER NOT NULL,
                        battery_w INTEGER NOT NULL,
                        home_w INTEGER NOT NULL,
                        grid_w INTEGER NOT NULL,
                        ev_w INTEGER NOT NULL,
                        soc_pct INTEGER NOT NULL,
                        v_ac1 REAL NOT NULL,
                        f_ac1 REAL NOT NULL,
                        v_battery REAL NOT NULL,
                        i_battery REAL NOT NULL,
                        t_inverter REAL NOT NULL,
                        t_battery REAL NOT NULL,
                        cell_min_v REAL NOT NULL,
                        cell_max_v REAL NOT NULL,
                        cell_delta_mv REAL NOT NULL,
                        ev_state TEXT NOT NULL,
                        ev_current_a REAL NOT NULL,
                        e_pv_day REAL NOT NULL,
                        e_cons_day REAL NOT NULL,
                        e_grid_in_day REAL NOT NULL,
                        e_grid_out_day REAL NOT NULL,
                        e_bat_chg_day REAL NOT NULL,
                        e_bat_dis_day REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_t1m_date ON telemetry_1m(date_str);
                    CREATE INDEX IF NOT EXISTS idx_t1m_epoch ON telemetry_1m(epoch);

                    CREATE TABLE IF NOT EXISTS telemetry_5m (
                        ts TEXT PRIMARY KEY,
                        epoch INTEGER NOT NULL,
                        date_str TEXT NOT NULL,
                        solar_w_avg INTEGER NOT NULL,
                        solar_w_max INTEGER NOT NULL,
                        battery_w_avg INTEGER NOT NULL,
                        home_w_avg INTEGER NOT NULL,
                        grid_w_avg INTEGER NOT NULL,
                        ev_w_avg INTEGER NOT NULL,
                        soc_pct INTEGER NOT NULL,
                        v_ac1 REAL NOT NULL,
                        t_inverter REAL NOT NULL,
                        t_battery REAL NOT NULL,
                        cell_delta_mv REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_t5m_date ON telemetry_5m(date_str);
                    CREATE INDEX IF NOT EXISTS idx_t5m_epoch ON telemetry_5m(epoch);

                    CREATE TABLE IF NOT EXISTS daily_summary (
                        date_str TEXT PRIMARY KEY,
                        updated_at TEXT NOT NULL,
                        pv_kwh REAL NOT NULL,
                        pv1_kwh REAL NOT NULL,
                        pv2_kwh REAL NOT NULL,
                        consumption_kwh REAL NOT NULL,
                        self_consumption_kwh REAL NOT NULL,
                        grid_import_kwh REAL NOT NULL,
                        grid_export_kwh REAL NOT NULL,
                        battery_charge_kwh REAL NOT NULL,
                        battery_discharge_kwh REAL NOT NULL,
                        ac_charge_kwh REAL NOT NULL,
                        ev_session_kwh REAL NOT NULL,
                        ev_total_kwh REAL NOT NULL,
                        peak_solar_w INTEGER NOT NULL,
                        peak_load_w INTEGER NOT NULL,
                        min_soc_pct INTEGER NOT NULL,
                        max_soc_pct INTEGER NOT NULL,
                        battery_cycles INTEGER NOT NULL,
                        max_cell_delta_mv REAL NOT NULL
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def record_snapshot(self, snap: PlantSnapshot) -> None:
        """Record a live 5s snapshot into RAM and roll up into 1m / 5m / daily buckets."""
        inv = snap.inverter
        bat = snap.battery
        evc = snap.ev_charger
        if not inv.connected and not evc.connected:
            return

        now = datetime.now()
        ts_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
        soc = bat.soc if bat.present else inv.battery_soc
        bat_temp = bat.t_max if bat.present and bat.t_max > 0 else inv.t_battery

        point = {
            "ts": ts_iso,
            "time": now.strftime("%H:%M:%S"),
            "epoch": int(now.timestamp()),
            "solar_w": inv.p_pv_total,
            "pv1_w": inv.p_pv1,
            "pv2_w": inv.p_pv2,
            "battery_w": inv.p_battery,
            "home_w": inv.p_load_demand,
            "grid_w": inv.p_grid_out,
            "ev_w": evc.active_power_w,
            "soc_pct": soc,
            "v_ac1": round(inv.v_ac1, 1),
            "v_battery": round(bat.v_out if bat.present and bat.v_out > 0 else inv.v_battery, 2),
            "t_inverter": round(inv.t_inverter_heatsink, 1),
            "t_battery": round(bat_temp, 1),
            "cell_delta_mv": round(bat.cell_delta_mv, 1) if bat.present else 0.0,
        }

        with self._lock:
            self._live_ring.append(point)
            self.total_samples_recorded += 1

            # 1-minute bucket check
            min_key = now.strftime("%Y-%m-%dT%H:%M:00")
            if self._current_minute_key is None:
                self._current_minute_key = min_key
            if min_key != self._current_minute_key and self._minute_samples:
                self._finalize_1m_bucket(self._current_minute_key, self._minute_samples)
                self._minute_samples = []
                self._current_minute_key = min_key
            self._minute_samples.append(snap)

            # 5-minute bucket check
            m5 = (now.minute // 5) * 5
            m5_key = now.strftime(f"%Y-%m-%dT%H:{m5:02d}:00")
            if self._current_5m_key is None:
                self._current_5m_key = m5_key
            if m5_key != self._current_5m_key and self._five_min_samples:
                self._finalize_5m_bucket(self._current_5m_key, self._five_min_samples)
                self._five_min_samples = []
                self._current_5m_key = m5_key
            self._five_min_samples.append(snap)

            # Update today's summary in RAM
            if inv.connected:
                date_str = now.strftime("%Y-%m-%d")
                self._stage_daily_summary(date_str, now.strftime("%Y-%m-%dT%H:%M:%S"), snap)

            # Flush to SQLite on disk once every flush_interval_s (60s)
            if (time.monotonic() - self._last_flush_mono) >= self.flush_interval_s:
                self._flush_to_disk_locked()

    @staticmethod
    def _agg_battery(snap: PlantSnapshot) -> tuple[int, float, float, float, float]:
        inv = snap.inverter
        bats = [b for b in (snap.batteries or ([snap.battery] if snap.battery.present else [])) if b.present]
        if not bats:
            return inv.battery_soc, inv.t_battery, 0.0, 0.0, 0.0
        soc = round(sum(b.soc for b in bats) / len(bats)) if len(bats) > 1 else bats[0].soc
        temps = [b.t_max for b in bats if b.t_max > 0]
        bat_temp = max(temps) if temps else inv.t_battery
        c_mins = [b.cell_min_v for b in bats if b.cell_min_v > 0]
        c_maxs = [b.cell_max_v for b in bats if b.cell_max_v > 0]
        delta = max(b.cell_delta_mv for b in bats)
        return (
            int(soc),
            round(bat_temp, 1),
            round(min(c_mins), 3) if c_mins else 0.0,
            round(max(c_maxs), 3) if c_maxs else 0.0,
            round(delta, 1),
        )

    def _finalize_1m_bucket(self, bucket_ts: str, samples: list[PlantSnapshot]) -> None:
        n = len(samples)
        last = samples[-1]
        inv = last.inverter
        bat = last.battery
        evc = last.ev_charger
        dt = datetime.fromisoformat(bucket_ts)
        soc, bat_temp, c_min, c_max, delta_mv = self._agg_battery(last)

        row = (
            bucket_ts,
            int(dt.timestamp()),
            bucket_ts[:10],
            int(round(sum(s.inverter.p_pv_total for s in samples) / n)),
            int(round(sum(s.inverter.p_pv1 for s in samples) / n)),
            int(round(sum(s.inverter.p_pv2 for s in samples) / n)),
            int(round(sum(s.inverter.p_battery for s in samples) / n)),
            int(round(sum(s.inverter.p_load_demand for s in samples) / n)),
            int(round(sum(s.inverter.p_grid_out for s in samples) / n)),
            int(round(sum(s.ev_charger.active_power_w for s in samples) / n)),
            int(soc),
            round(inv.v_ac1, 1),
            round(inv.f_ac1, 2),
            round(bat.v_out if bat.present and bat.v_out > 0 else inv.v_battery, 3),
            round(bat.i_battery if bat.present else inv.i_battery, 2),
            round(inv.t_inverter_heatsink, 1),
            round(bat_temp, 1),
            c_min,
            c_max,
            delta_mv,
            evc.charging_state,
            round(evc.current_l1_a, 1),
            round(inv.e_pv_generation_today, 2),
            round(inv.e_consumption_today, 2),
            round(inv.e_grid_in_day, 2),
            round(inv.e_grid_out_day, 2),
            round(inv.e_battery_charge_today, 2),
            round(inv.e_battery_discharge_today, 2),
        )
        self._pending_1m_rows.append(row)

    def _finalize_5m_bucket(self, bucket_ts: str, samples: list[PlantSnapshot]) -> None:
        n = len(samples)
        last = samples[-1]
        inv = last.inverter
        bat = last.battery
        dt = datetime.fromisoformat(bucket_ts)
        soc, bat_temp, _, _, delta_mv = self._agg_battery(last)

        row = (
            bucket_ts,
            int(dt.timestamp()),
            bucket_ts[:10],
            int(round(sum(s.inverter.p_pv_total for s in samples) / n)),
            int(max(s.inverter.p_pv_total for s in samples)),
            int(round(sum(s.inverter.p_battery for s in samples) / n)),
            int(round(sum(s.inverter.p_load_demand for s in samples) / n)),
            int(round(sum(s.inverter.p_grid_out for s in samples) / n)),
            int(round(sum(s.ev_charger.active_power_w for s in samples) / n)),
            int(soc),
            round(inv.v_ac1, 1),
            round(inv.t_inverter_heatsink, 1),
            round(bat_temp, 1),
            delta_mv,
        )
        self._pending_5m_rows.append(row)

    def _stage_daily_summary(self, date_str: str, updated_at: str, snap: PlantSnapshot) -> None:
        inv = snap.inverter
        bat = snap.battery
        evc = snap.ev_charger
        soc, _, _, _, delta_mv = self._agg_battery(snap)

        prev = self._pending_daily_upserts.get(date_str)
        peak_solar = max(inv.p_pv_total, prev[14] if prev else inv.p_pv_total)
        peak_load = max(inv.p_load_demand, prev[15] if prev else inv.p_load_demand)
        min_soc = min(soc, prev[16] if prev else soc) if soc > 0 else (prev[16] if prev else soc)
        max_soc = max(soc, prev[17] if prev else soc)
        max_delta = max(delta_mv, prev[19] if prev else delta_mv)

        self._pending_daily_upserts[date_str] = (
            date_str,
            updated_at,
            round(inv.e_pv_generation_today, 2),
            round(inv.e_pv1_day, 2),
            round(inv.e_pv2_day, 2),
            round(inv.e_consumption_today, 2),
            round(inv.e_self_consumption_today, 2),
            round(inv.e_grid_in_day, 2),
            round(inv.e_grid_out_day, 2),
            round(inv.e_battery_charge_today, 2),
            round(inv.e_battery_discharge_today, 2),
            round(inv.e_ac_charge_today, 2),
            round(evc.session_energy_kwh, 2),
            round(evc.total_meter_energy_kwh, 1),
            int(peak_solar),
            int(peak_load),
            int(min_soc),
            int(max_soc),
            int(bat.num_cycles if bat.present else 0),
            float(max_delta),
        )

    def flush(self) -> None:
        """Force immediate flush of any staged RAM rows to SQLite on disk."""
        with self._lock:
            if self._minute_samples and self._current_minute_key:
                self._finalize_1m_bucket(self._current_minute_key, self._minute_samples)
                self._minute_samples = []
            if self._five_min_samples and self._current_5m_key:
                self._finalize_5m_bucket(self._current_5m_key, self._five_min_samples)
                self._five_min_samples = []
            self._flush_to_disk_locked()

    def _flush_to_disk_locked(self) -> None:
        if not self._pending_1m_rows and not self._pending_5m_rows and not self._pending_daily_upserts:
            self._last_flush_mono = time.monotonic()
            return

        conn = self._connect()
        try:
            with conn:
                if self._pending_1m_rows:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO telemetry_1m VALUES (
                            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                        )
                        """,
                        self._pending_1m_rows,
                    )
                    self._pending_1m_rows.clear()

                if self._pending_5m_rows:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO telemetry_5m VALUES (
                            ?,?,?,?,?,?,?,?,?,?,?,?,?,?
                        )
                        """,
                        self._pending_5m_rows,
                    )
                    self._pending_5m_rows.clear()

                for row in self._pending_daily_upserts.values():
                    conn.execute(
                        """
                        INSERT INTO daily_summary VALUES (
                            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                        )
                        ON CONFLICT(date_str) DO UPDATE SET
                            updated_at = excluded.updated_at,
                            pv_kwh = excluded.pv_kwh,
                            pv1_kwh = excluded.pv1_kwh,
                            pv2_kwh = excluded.pv2_kwh,
                            consumption_kwh = excluded.consumption_kwh,
                            self_consumption_kwh = excluded.self_consumption_kwh,
                            grid_import_kwh = excluded.grid_import_kwh,
                            grid_export_kwh = excluded.grid_export_kwh,
                            battery_charge_kwh = excluded.battery_charge_kwh,
                            battery_discharge_kwh = excluded.battery_discharge_kwh,
                            ac_charge_kwh = excluded.ac_charge_kwh,
                            ev_session_kwh = excluded.ev_session_kwh,
                            ev_total_kwh = excluded.ev_total_kwh,
                            peak_solar_w = MAX(daily_summary.peak_solar_w, excluded.peak_solar_w),
                            peak_load_w = MAX(daily_summary.peak_load_w, excluded.peak_load_w),
                            min_soc_pct = CASE WHEN daily_summary.min_soc_pct > 0
                                THEN MIN(daily_summary.min_soc_pct, excluded.min_soc_pct)
                                ELSE excluded.min_soc_pct END,
                            max_soc_pct = MAX(daily_summary.max_soc_pct, excluded.max_soc_pct),
                            battery_cycles = MAX(daily_summary.battery_cycles, excluded.battery_cycles),
                            max_cell_delta_mv = MAX(daily_summary.max_cell_delta_mv, excluded.max_cell_delta_mv)
                        """,
                        row,
                    )

                # Prune 1-minute rows older than 120 days to keep DB compact
                cutoff_epoch = int((datetime.now() - timedelta(days=120)).timestamp())
                conn.execute("DELETE FROM telemetry_1m WHERE epoch < ?", (cutoff_epoch,))

            self.total_disk_flushes += 1
            self._last_flush_mono = time.monotonic()
        finally:
            conn.close()

    def get_live_ring(self, max_points: int = 1440, max_age_s: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            pts = list(self._live_ring)
        if max_age_s > 0 and pts:
            cutoff = int(time.time()) - max_age_s
            pts = [p for p in pts if int(p.get("epoch", 0) or 0) >= cutoff]
        return pts[-max_points:]

    def get_intraday_series(self, date_str: str | None = None) -> list[dict[str, Any]]:
        """Return 1-minute telemetry for a specific date (YYYY-MM-DD), combined with any pending RAM rows."""
        target_date = date_str or datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM telemetry_1m WHERE date_str = ? ORDER BY epoch ASC",
                    (target_date,),
                ).fetchall()
                result = [dict(r) for r in rows]
            finally:
                conn.close()

        # If today has very few 1m rows yet (first few minutes of service), supplement from live_ring
        if not result and target_date == datetime.now().strftime("%Y-%m-%d"):
            return self.get_live_ring(360)
        return result

    def get_range_series(self, days: int = 7, end_date: str | None = None) -> list[dict[str, Any]]:
        """Return 5-minute or 1-minute historical series across N days ending on end_date (or now)."""
        if end_date:
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            except ValueError:
                end_dt = datetime.now()
        else:
            end_dt = datetime.now()
        start_epoch = int((end_dt - timedelta(days=days)).timestamp())
        end_epoch = int(end_dt.timestamp())
        table = "telemetry_1m" if days <= 2 else "telemetry_5m"
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT * FROM {table} WHERE epoch >= ? AND epoch <= ? ORDER BY epoch ASC",
                    (start_epoch, end_epoch),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_daily_summaries(self, limit: int = 90) -> list[dict[str, Any]]:
        """Return daily energy ledger summaries ordered newest first."""
        with self._lock:
            # Ensure today's staged summary is flushed or merged
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM daily_summary ORDER BY date_str DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                items = {r["date_str"]: dict(r) for r in rows}
            finally:
                conn.close()

            for d_str, tup in self._pending_daily_upserts.items():
                if d_str not in items:
                    items[d_str] = {
                        "date_str": tup[0],
                        "updated_at": tup[1],
                        "pv_kwh": tup[2],
                        "pv1_kwh": tup[3],
                        "pv2_kwh": tup[4],
                        "consumption_kwh": tup[5],
                        "self_consumption_kwh": tup[6],
                        "grid_import_kwh": tup[7],
                        "grid_export_kwh": tup[8],
                        "battery_charge_kwh": tup[9],
                        "battery_discharge_kwh": tup[10],
                        "ac_charge_kwh": tup[11],
                        "ev_session_kwh": tup[12],
                        "ev_total_kwh": tup[13],
                        "peak_solar_w": tup[14],
                        "peak_load_w": tup[15],
                        "min_soc_pct": tup[16],
                        "max_soc_pct": tup[17],
                        "battery_cycles": tup[18],
                        "max_cell_delta_mv": tup[19],
                    }
            sorted_days = sorted(items.values(), key=lambda x: x["date_str"], reverse=True)
            return sorted_days[:limit]

    def get_db_stats(self) -> dict[str, Any]:
        size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        wal_path = Path(str(self.db_path) + "-wal")
        if wal_path.exists():
            size_bytes += wal_path.stat().st_size
        with self._lock:
            conn = self._connect()
            try:
                c_1m = conn.execute("SELECT COUNT(*) FROM telemetry_1m").fetchone()[0]
                c_5m = conn.execute("SELECT COUNT(*) FROM telemetry_5m").fetchone()[0]
                c_days = conn.execute("SELECT COUNT(*) FROM daily_summary").fetchone()[0]
            finally:
                conn.close()
        return {
            "db_size_mb": round(size_bytes / (1024 * 1024), 2),
            "rows_1m": c_1m,
            "rows_5m": c_5m,
            "days_recorded": c_days,
            "live_ring_points": len(self._live_ring),
            "total_samples": self.total_samples_recorded,
            "total_disk_flushes": self.total_disk_flushes,
        }

    def backup_to_file(self, dest_path: Path) -> None:
        """Flush RAM buffers and write an atomic online SQLite backup to dest_path."""
        self.flush()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            src = self._connect()
            dst = sqlite3.connect(str(dest_path), timeout=15.0)
            try:
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
                src.close()

    def replace_from_file(self, src_path: Path) -> None:
        """Atomically replace the active database from a validated backup SQLite file."""
        with self._lock:
            self._live_ring.clear()
            self._minute_samples.clear()
            self._five_min_samples.clear()
            self._pending_1m_rows.clear()
            self._pending_5m_rows.clear()
            self._pending_daily_upserts.clear()
            self._current_minute_key = None
            self._current_5m_key = None

            src = sqlite3.connect(str(src_path), timeout=15.0)
            dst = self._connect()
            try:
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
                src.close()
        self._init_schema()
        self._seed_live_ring()

    def _seed_live_ring(self, max_age_s: int = 7200) -> None:
        """Seed in-memory live ring buffer from the last 2 hours of 1-minute SQLite rows."""
        cutoff = int(time.time()) - max_age_s
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT ts, epoch, solar_w, pv1_w, pv2_w, battery_w, home_w, grid_w, ev_w,
                           soc_pct, v_ac1, v_battery, t_inverter, t_battery, cell_delta_mv
                    FROM telemetry_1m
                    WHERE epoch >= ?
                    ORDER BY epoch ASC
                    """,
                    (cutoff,),
                ).fetchall()
            finally:
                conn.close()
            self._live_ring.clear()
            for r in rows:
                ts_str = str(r["ts"] or "")
                self._live_ring.append(
                    {
                        "ts": ts_str,
                        "time": ts_str[11:19] if len(ts_str) >= 19 else ts_str,
                        "epoch": int(r["epoch"]),
                        "solar_w": r["solar_w"],
                        "pv1_w": r["pv1_w"],
                        "pv2_w": r["pv2_w"],
                        "battery_w": r["battery_w"],
                        "home_w": r["home_w"],
                        "grid_w": r["grid_w"],
                        "ev_w": r["ev_w"],
                        "soc_pct": r["soc_pct"],
                        "v_ac1": r["v_ac1"],
                        "v_battery": r["v_battery"],
                        "t_inverter": r["t_inverter"],
                        "t_battery": r["t_battery"],
                        "cell_delta_mv": r["cell_delta_mv"],
                    }
                )
