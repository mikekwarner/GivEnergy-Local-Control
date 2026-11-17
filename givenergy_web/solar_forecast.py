"""Open-Meteo Solar PV Forecasting & Adaptive Efficiency Calibration Engine."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass
class SolarForecastConfig:
    enabled: bool = False
    latitude: float | None = None
    longitude: float | None = None
    capacity_kwp: float = 5.0
    tilt_deg: float = 35.0
    azimuth_deg: float = 180.0  # Compass bearing: 0=N, 90=E, 180=S, 270=W
    array2_enabled: bool = False
    array2_capacity_kwp: float = 0.0
    array2_tilt_deg: float = 35.0
    array2_azimuth_deg: float = 270.0
    base_efficiency: float = 0.85
    forecast_days: int = 7
    last_sync_at: str = ""
    last_sync_error: str = ""

    def is_configured(self) -> bool:
        return (
            self.enabled
            and self.latitude is not None
            and self.longitude is not None
            and self.capacity_kwp > 0
        )


def compass_to_open_meteo_azimuth(compass_deg: float) -> float:
    """Convert compass bearing (0=N, 90=E, 180=S, 270=W) to Open-Meteo azimuth (0=S, -90=E, 90=W)."""
    return round(((float(compass_deg) % 360.0) - 180.0), 2)


def load_solar_forecast_config(config_path: Path) -> SolarForecastConfig:
    cfg = SolarForecastConfig()
    if config_path.exists():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
        except Exception:
            pass
    return cfg


def save_solar_forecast_config(config_path: Path, cfg: SolarForecastConfig) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")


def _http_get_json(url: str, timeout: float = 15.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "GivEnergy-Local-Control/2.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_array_series(
    latitude: float,
    longitude: float,
    tilt_deg: float,
    compass_azimuth_deg: float,
    past_days: int,
    forecast_days: int,
) -> dict[str, Any]:
    om_az = compass_to_open_meteo_azimuth(compass_azimuth_deg)
    params = urllib.parse.urlencode(
        {
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "hourly": "global_tilted_irradiance,cloud_cover,temperature_2m",
            "tilt": f"{max(0.0, min(90.0, tilt_deg)):.1f}",
            "azimuth": f"{om_az:.1f}",
            "timezone": "auto",
            "past_days": max(0, min(7, int(past_days))),
            "forecast_days": max(1, min(14, int(forecast_days))),
        }
    )
    url = f"{OPEN_METEO_FORECAST_URL}?{params}"
    return _http_get_json(url)


def fetch_open_meteo_forecast(
    cfg: SolarForecastConfig,
    past_days: int = 3,
) -> list[tuple[str, str, float, float, float, float, float, str]]:
    """Fetch hourly GTI, cloud cover, and temperature from Open-Meteo and compute nominal raw kW."""
    if cfg.latitude is None or cfg.longitude is None or cfg.capacity_kwp <= 0:
        raise ValueError("Latitude, longitude, and array capacity (kWp) are required.")

    forecast_days = max(1, min(14, int(cfg.forecast_days or 7)))
    primary = _fetch_array_series(
        cfg.latitude,
        cfg.longitude,
        cfg.tilt_deg,
        cfg.azimuth_deg,
        past_days=past_days,
        forecast_days=forecast_days,
    )
    hourly = primary.get("hourly") or {}
    times: list[str] = hourly.get("time") or []
    gti_list: list[float | None] = hourly.get("global_tilted_irradiance") or []
    cloud_list: list[float | None] = hourly.get("cloud_cover") or []
    temp_list: list[float | None] = hourly.get("temperature_2m") or []

    gti2_by_ts: dict[str, float] = {}
    if cfg.array2_enabled and cfg.array2_capacity_kwp > 0:
        sec = _fetch_array_series(
            cfg.latitude,
            cfg.longitude,
            cfg.array2_tilt_deg,
            cfg.array2_azimuth_deg,
            past_days=past_days,
            forecast_days=forecast_days,
        )
        sec_h = sec.get("hourly") or {}
        sec_times = sec_h.get("time") or []
        sec_gti = sec_h.get("global_tilted_irradiance") or []
        for idx, t in enumerate(sec_times):
            val = sec_gti[idx] if idx < len(sec_gti) else 0.0
            gti2_by_ts[t] = float(val or 0.0)

    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    rows: list[tuple[str, str, float, float, float, float, float, str]] = []
    cap1 = float(cfg.capacity_kwp)
    cap2 = float(cfg.array2_capacity_kwp) if (cfg.array2_enabled and cfg.array2_capacity_kwp > 0) else 0.0
    total_cap = cap1 + cap2

    for i, t_str in enumerate(times):
        ts = t_str if len(t_str) == 19 else f"{t_str}:00"
        date_str = ts[:10]
        gti1 = max(0.0, float((gti_list[i] if i < len(gti_list) else 0.0) or 0.0))
        cloud = max(0.0, min(100.0, float((cloud_list[i] if i < len(cloud_list) else 0.0) or 0.0)))
        temp_c = float((temp_list[i] if i < len(temp_list) else 15.0) or 15.0)

        t_cell1 = temp_c + 0.025 * gti1
        tf1 = max(0.75, min(1.05, 1.0 - 0.0035 * (t_cell1 - 25.0)))
        raw_kw = cap1 * (gti1 / 1000.0) * tf1

        eff_gti = gti1
        if cap2 > 0:
            gti2 = max(0.0, gti2_by_ts.get(t_str, 0.0))
            t_cell2 = temp_c + 0.025 * gti2
            tf2 = max(0.75, min(1.05, 1.0 - 0.0035 * (t_cell2 - 25.0)))
            raw_kw += cap2 * (gti2 / 1000.0) * tf2
            if total_cap > 0:
                eff_gti = (gti1 * cap1 + gti2 * cap2) / total_cap

        raw_kw = round(raw_kw, 4)
        rows.append(
            (
                ts,
                date_str,
                round(eff_gti, 1),
                round(cloud, 1),
                round(temp_c, 1),
                raw_kw,
                raw_kw,  # 1-hour slot -> kW == kWh
                now_iso,
            )
        )
    return rows


class SolarForecastStore:
    """SQLite cache for Open-Meteo hourly forecasts and adaptive efficiency calibration."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS solar_forecast_hourly (
                        ts TEXT PRIMARY KEY,
                        date_str TEXT NOT NULL,
                        gti_wm2 REAL NOT NULL,
                        cloud_cover_pct REAL NOT NULL,
                        temp_c REAL NOT NULL,
                        raw_kw REAL NOT NULL,
                        raw_kwh REAL NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_sf_date ON solar_forecast_hourly(date_str);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def upsert_hourly_rows(
        self, rows: list[tuple[str, str, float, float, float, float, float, str]]
    ) -> int:
        if not rows:
            return 0
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    """
                    INSERT INTO solar_forecast_hourly (
                        ts, date_str, gti_wm2, cloud_cover_pct, temp_c, raw_kw, raw_kwh, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ts) DO UPDATE SET
                        date_str=excluded.date_str,
                        gti_wm2=excluded.gti_wm2,
                        cloud_cover_pct=excluded.cloud_cover_pct,
                        temp_c=excluded.temp_c,
                        raw_kw=excluded.raw_kw,
                        raw_kwh=excluded.raw_kwh,
                        updated_at=excluded.updated_at
                    """,
                    rows,
                )
                conn.commit()
                return len(rows)
            finally:
                conn.close()

    def clear_forecasts(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM solar_forecast_hourly")
                conn.commit()
            finally:
                conn.close()

    def _query_actual_hourly(
        self, conn: sqlite3.Connection, start_date: str, end_date: str
    ) -> dict[str, tuple[float, int]]:
        """Return {hour_iso: (actual_avg_kw, sample_count)} from telemetry_1m, falling back to telemetry_5m."""
        actuals: dict[str, tuple[float, int]] = {}
        try:
            rows_1m = conn.execute(
                """
                SELECT substr(ts, 1, 13) || ':00:00' AS hour_ts,
                       AVG(solar_w) / 1000.0 AS avg_kw,
                       COUNT(*) AS cnt
                FROM telemetry_1m
                WHERE date_str >= ? AND date_str <= ?
                GROUP BY hour_ts
                """,
                (start_date, end_date),
            ).fetchall()
            for r in rows_1m:
                actuals[r["hour_ts"]] = (max(0.0, float(r["avg_kw"] or 0.0)), int(r["cnt"]))
        except sqlite3.OperationalError:
            pass

        try:
            rows_5m = conn.execute(
                """
                SELECT substr(ts, 1, 13) || ':00:00' AS hour_ts,
                       AVG(solar_w_avg) / 1000.0 AS avg_kw,
                       COUNT(*) AS cnt
                FROM telemetry_5m
                WHERE date_str >= ? AND date_str <= ?
                GROUP BY hour_ts
                """,
                (start_date, end_date),
            ).fetchall()
            for r in rows_5m:
                h_ts = r["hour_ts"]
                if h_ts not in actuals:
                    # Normalize 5m count to approximate 1m density for threshold check
                    actuals[h_ts] = (max(0.0, float(r["avg_kw"] or 0.0)), int(r["cnt"]) * 5)
        except sqlite3.OperationalError:
            pass

        return actuals

    def compute_efficiency_stats(
        self, base_efficiency: float = 0.85, lookback_days: int = 30
    ) -> dict[str, Any]:
        """Learn dynamic solar efficiency factor by comparing elapsed forecast hours vs actual PV."""
        now = datetime.now()
        current_hour_iso = now.strftime("%Y-%m-%dT%H:00:00")
        start_date = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        today_str = now.strftime("%Y-%m-%d")

        with self._lock:
            conn = self._connect()
            try:
                fc_rows = conn.execute(
                    """
                    SELECT ts, date_str, raw_kwh
                    FROM solar_forecast_hourly
                    WHERE date_str >= ? AND ts < ?
                    ORDER BY ts ASC
                    """,
                    (start_date, current_hour_iso),
                ).fetchall()
                actuals = self._query_actual_hourly(conn, start_date, today_str)
            finally:
                conn.close()

        weighted_actual = 0.0
        weighted_raw = 0.0
        total_actual_kwh = 0.0
        total_raw_kwh = 0.0
        matched_hours: list[tuple[float, float]] = []
        matched_days: set[str] = set()

        for r in fc_rows:
            ts = r["ts"]
            raw_kwh = float(r["raw_kwh"] or 0.0)
            if raw_kwh < 0.15:
                continue
            act_entry = actuals.get(ts)
            if not act_entry:
                continue
            act_kwh, sample_cnt = act_entry
            if sample_cnt < 15:
                continue

            try:
                age_days = max(0, (now.date() - datetime.strptime(r["date_str"], "%Y-%m-%d").date()).days)
            except ValueError:
                age_days = 0
            weight = 0.92 ** age_days

            weighted_actual += act_kwh * weight
            weighted_raw += raw_kwh * weight
            total_actual_kwh += act_kwh
            total_raw_kwh += raw_kwh
            matched_hours.append((raw_kwh, act_kwh))
            matched_days.add(r["date_str"])

        sample_hours = len(matched_hours)
        base_eff = max(0.25, min(2.50, float(base_efficiency or 0.85)))

        if sample_hours == 0 or weighted_raw <= 0:
            return {
                "efficiency_factor": round(base_eff, 4),
                "efficiency_pct": round(base_eff * 100.0, 1),
                "base_efficiency_pct": round(base_eff * 100.0, 1),
                "learned_raw_pct": None,
                "sample_hours": 0,
                "sample_days": 0,
                "mae_kw": None,
                "total_actual_kwh": 0.0,
                "total_raw_kwh": 0.0,
                "confidence": "Baseline (awaiting actuals)",
            }

        learned_eff = weighted_actual / weighted_raw
        learned_eff_clamped = max(0.25, min(2.50, learned_eff))
        alpha = min(1.0, sample_hours / 12.0)
        eff = (1.0 - alpha) * base_eff + alpha * learned_eff_clamped
        eff = max(0.25, min(2.50, eff))

        abs_errors = [abs(raw_k * eff - act_k) for raw_k, act_k in matched_hours]
        mae_kw = sum(abs_errors) / len(abs_errors)

        if sample_hours < 12:
            confidence = f"Calibrating ({sample_hours}/12 hrs)"
        else:
            confidence = f"Calibrated ({len(matched_days)}d / {sample_hours}h)"

        return {
            "efficiency_factor": round(eff, 4),
            "efficiency_pct": round(eff * 100.0, 1),
            "base_efficiency_pct": round(base_eff * 100.0, 1),
            "learned_raw_pct": round(learned_eff_clamped * 100.0, 1),
            "sample_hours": sample_hours,
            "sample_days": len(matched_days),
            "mae_kw": round(mae_kw, 3),
            "total_actual_kwh": round(total_actual_kwh, 2),
            "total_raw_kwh": round(total_raw_kwh, 2),
            "confidence": confidence,
        }

    def get_forecast_and_actuals(
        self,
        cfg: SolarForecastConfig,
        past_days: int = 2,
        forecast_days: int = 7,
    ) -> dict[str, Any]:
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        current_hour_iso = now.strftime("%Y-%m-%dT%H:00:00")
        start_date = (now - timedelta(days=past_days)).strftime("%Y-%m-%d")
        end_date = (now + timedelta(days=forecast_days - 1)).strftime("%Y-%m-%d")

        eff_stats = self.compute_efficiency_stats(cfg.base_efficiency)
        eff = eff_stats["efficiency_factor"]
        base_eff = max(0.25, min(2.50, float(cfg.base_efficiency or 0.85)))

        with self._lock:
            conn = self._connect()
            try:
                fc_rows = conn.execute(
                    """
                    SELECT ts, date_str, gti_wm2, cloud_cover_pct, temp_c, raw_kw, raw_kwh, updated_at
                    FROM solar_forecast_hourly
                    WHERE date_str >= ? AND date_str <= ?
                    ORDER BY ts ASC
                    """,
                    (start_date, end_date),
                ).fetchall()
                actuals = self._query_actual_hourly(conn, start_date, today_str)
                daily_actual_rows: dict[str, float] = {}
                try:
                    d_rows = conn.execute(
                        """
                        SELECT date_str, pv_kwh
                        FROM daily_summary
                        WHERE date_str >= ? AND date_str <= ?
                        """,
                        (start_date, today_str),
                    ).fetchall()
                    for dr in d_rows:
                        daily_actual_rows[dr["date_str"]] = float(dr["pv_kwh"] or 0.0)
                except sqlite3.OperationalError:
                    pass
            finally:
                conn.close()

        hourly_out: list[dict[str, Any]] = []
        daily_map: dict[str, dict[str, Any]] = {}

        for r in fc_rows:
            ts = r["ts"]
            d_str = r["date_str"]
            raw_kw = float(r["raw_kw"] or 0.0)
            fc_kw = round(raw_kw * eff, 3)
            base_kw = round(raw_kw * base_eff, 3)

            act_entry = actuals.get(ts)
            act_kw = round(act_entry[0], 3) if (act_entry is not None and ts <= current_hour_iso) else None

            hourly_out.append(
                {
                    "ts": ts,
                    "date_str": d_str,
                    "hour_str": ts[11:16],
                    "gti_wm2": float(r["gti_wm2"] or 0.0),
                    "cloud_cover_pct": float(r["cloud_cover_pct"] or 0.0),
                    "temp_c": float(r["temp_c"] or 0.0),
                    "raw_kw": round(raw_kw, 3),
                    "baseline_kw": base_kw,
                    "forecast_kw": fc_kw,
                    "actual_kw": act_kw,
                    "is_past": ts < current_hour_iso,
                }
            )

            d_entry = daily_map.setdefault(
                d_str,
                {
                    "date_str": d_str,
                    "raw_kwh": 0.0,
                    "baseline_kwh": 0.0,
                    "forecast_kwh": 0.0,
                    "hourly_actual_sum_kwh": 0.0,
                    "has_hourly_actual": False,
                    "cloud_sum": 0.0,
                    "hours_cnt": 0,
                    "peak_forecast_kw": 0.0,
                    "peak_actual_kw": 0.0,
                },
            )
            d_entry["raw_kwh"] += raw_kw
            d_entry["baseline_kwh"] += base_kw
            d_entry["forecast_kwh"] += fc_kw
            d_entry["cloud_sum"] += float(r["cloud_cover_pct"] or 0.0)
            d_entry["hours_cnt"] += 1
            if fc_kw > d_entry["peak_forecast_kw"]:
                d_entry["peak_forecast_kw"] = fc_kw
            if act_kw is not None:
                d_entry["hourly_actual_sum_kwh"] += act_kw
                d_entry["has_hourly_actual"] = True
                if act_kw > d_entry["peak_actual_kw"]:
                    d_entry["peak_actual_kw"] = act_kw

        daily_out: list[dict[str, Any]] = []
        for d_str in sorted(daily_map.keys()):
            d = daily_map[d_str]
            is_today = d_str == today_str
            is_future = d_str > today_str
            if is_future:
                actual_kwh = None
            elif d_str in daily_actual_rows:
                actual_kwh = round(daily_actual_rows[d_str], 2)
            elif d["has_hourly_actual"]:
                actual_kwh = round(d["hourly_actual_sum_kwh"], 2)
            else:
                actual_kwh = None

            cnt = max(1, d["hours_cnt"])
            daily_out.append(
                {
                    "date_str": d_str,
                    "raw_kwh": round(d["raw_kwh"], 2),
                    "baseline_kwh": round(d["baseline_kwh"], 2),
                    "forecast_kwh": round(d["forecast_kwh"], 2),
                    "actual_kwh": actual_kwh,
                    "cloud_cover_avg_pct": round(d["cloud_sum"] / cnt, 0),
                    "peak_forecast_kw": round(d["peak_forecast_kw"], 2),
                    "peak_actual_kw": round(d["peak_actual_kw"], 2) if not is_future else None,
                    "is_today": is_today,
                    "is_future": is_future,
                }
            )

        # Today's summary KPIs
        today_fc_total = 0.0
        today_remaining = 0.0
        today_peak_fc = 0.0
        for h in hourly_out:
            if h["date_str"] == today_str:
                today_fc_total += h["forecast_kw"]
                if h["ts"] >= current_hour_iso:
                    today_remaining += h["forecast_kw"]
                if h["forecast_kw"] > today_peak_fc:
                    today_peak_fc = h["forecast_kw"]

        today_actual = daily_actual_rows.get(today_str)
        if today_actual is None:
            today_actual_vals = [h["actual_kw"] for h in hourly_out if h["date_str"] == today_str and h["actual_kw"] is not None]
            today_actual = round(sum(today_actual_vals), 2) if today_actual_vals else 0.0

        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        tomorrow_fc = next((d["forecast_kwh"] for d in daily_out if d["date_str"] == tomorrow_str), None)

        return {
            "configured": cfg.is_configured(),
            "last_sync_at": cfg.last_sync_at,
            "last_sync_error": cfg.last_sync_error,
            "efficiency": eff_stats,
            "today_summary": {
                "forecast_total_kwh": round(today_fc_total, 2),
                "actual_so_far_kwh": round(today_actual, 2),
                "remaining_forecast_kwh": round(today_remaining, 2),
                "peak_forecast_kw": round(today_peak_fc, 2),
                "tomorrow_forecast_kwh": tomorrow_fc,
            },
            "hourly": hourly_out,
            "daily": daily_out,
        }
