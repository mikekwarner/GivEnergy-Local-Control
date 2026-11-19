"""Shared application context, path resolution, WebSocket hub, and auth helpers."""

from __future__ import annotations

import asyncio
import hmac
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import WebSocket
from starlette.requests import HTTPConnection

from givenergy_web.config import PropertyConfig
from givenergy_web.database import HistoricDatabase
from givenergy_web.platform_adapter import PlatformAdapter
from givenergy_web.poller import AsyncPlantPoller
from givenergy_web.solar_forecast import (
    SolarForecastConfig,
    SolarForecastStore,
    fetch_open_meteo_forecast,
    save_solar_forecast_config,
)
from givenergy_web.tariffs import (
    TariffConfig,
    TariffRateCache,
    save_tariff_config,
    sync_octopus_tariff,
    sync_price_cap,
)


class WebSocketHub:
    """Manages active browser WebSocket clients and broadcasts live snapshots."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.discard(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self.clients)
        if not targets:
            return
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.clients.discard(ws)


@dataclass(frozen=True)
class AppPaths:
    base_dir: Path
    static_dir: Path
    data_dir: Path
    db_path: Path
    config_path: Path
    tariff_config_path: Path
    solar_forecast_config_path: Path


@dataclass
class AppContext:
    app_version: str
    paths: AppPaths
    db_flush_interval_s: float
    platform: PlatformAdapter
    ws_hub: WebSocketHub
    property_config: PropertyConfig
    tariff_config: TariffConfig
    tariff_cache: TariffRateCache
    solar_forecast_config: SolarForecastConfig
    solar_forecast_store: SolarForecastStore
    db: HistoricDatabase
    poller: AsyncPlantPoller

    def read_system_stats(self) -> dict[str, Any]:
        return self.platform.read_system_stats(
            app_version=self.app_version,
            started_at=self.poller.started_at,
            ws_clients=len(self.ws_hub.clients),
            db_stats=self.db.get_db_stats(),
            data_dir=self.paths.db_path.parent,
        )

    async def sync_configured_octopus_rates(self) -> dict[str, Any]:
        """Sync any configured Octopus API tariffs into the local SQLite rate cache."""
        synced: dict[str, Any] = {}
        tc = self.tariff_config
        if tc.import_tariff.mode == "octopus_api" and tc.import_tariff.octopus_product_code:
            res = await asyncio.to_thread(
                sync_octopus_tariff,
                self.tariff_cache,
                tc.import_tariff.octopus_product_code,
                tc.region,
            )
            tc.import_tariff.octopus_tariff_code = res["tariff_code"]
            tc.import_tariff.default_rate_p = res["current_rate_p"]
            if res.get("standing_charge_p") is not None:
                tc.standing_charge_p = float(res["standing_charge_p"])
                if tc.tariff_history:
                    today_str = time.strftime("%Y-%m-%d")
                    ordered = sorted(tc.tariff_history, key=lambda e: e.effective_from)
                    active_entry = ordered[0]
                    for entry in ordered:
                        if entry.effective_from <= today_str:
                            active_entry = entry
                    active_entry.standing_charge_p = tc.standing_charge_p
            synced["import"] = res
        if tc.export_tariff.mode == "octopus_api" and tc.export_tariff.octopus_product_code:
            res = await asyncio.to_thread(
                sync_octopus_tariff,
                self.tariff_cache,
                tc.export_tariff.octopus_product_code,
                tc.region,
            )
            tc.export_tariff.octopus_tariff_code = res["tariff_code"]
            tc.export_tariff.default_rate_p = res["current_rate_p"]
            synced["export"] = res
        if tc.price_cap_auto_sync:
            try:
                synced["price_cap"] = await asyncio.to_thread(sync_price_cap, tc)
            except Exception:
                pass
        if synced:
            tc.last_octopus_sync = time.strftime("%Y-%m-%dT%H:%M:%S")
            save_tariff_config(self.paths.tariff_config_path, tc)
        return synced

    async def sync_solar_forecast(self) -> dict[str, Any]:
        """Fetch Open-Meteo solar irradiance forecast and store in local SQLite cache."""
        sc = self.solar_forecast_config
        if not sc.is_configured():
            return {"ok": False, "skipped": True, "reason": "Solar forecast not configured"}
        try:
            rows = await asyncio.to_thread(fetch_open_meteo_forecast, sc, 3)
            count = await asyncio.to_thread(self.solar_forecast_store.upsert_hourly_rows, rows)
            sc.last_sync_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            sc.last_sync_error = ""
            save_solar_forecast_config(self.paths.solar_forecast_config_path, sc)
            return {"ok": True, "rows_upserted": count, "last_sync_at": sc.last_sync_at}
        except Exception as exc:
            sc.last_sync_error = str(exc)
            save_solar_forecast_config(self.paths.solar_forecast_config_path, sc)
            return {"ok": False, "error": str(exc)}


def get_ctx(conn: HTTPConnection) -> AppContext:
    """Retrieve the AppContext attached to FastAPI app.state."""
    return conn.app.state.ctx


def is_funnel_request(headers: Any, client_host: str | None) -> bool:
    """Return True when the request arrives via Tailscale Funnel proxy on localhost."""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return False
    lower_keys = {k.lower() for k in headers.keys()}
    return bool(lower_keys & {"x-forwarded-for", "x-forwarded-host", "tailscale-funnel-request"})


def extract_bearer_token(headers: Any, query_params: Any, cookies: Any) -> str:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    q_tok = query_params.get("token", "")
    if q_tok:
        return str(q_tok).strip()
    c_tok = cookies.get("givenergy_token", "")
    return str(c_tok).strip()


def is_authorized(expected_token: str, headers: Any, query_params: Any, cookies: Any) -> bool:
    expected = (expected_token or "").strip()
    if not expected:
        return False
    supplied = extract_bearer_token(headers, query_params, cookies)
    if not supplied:
        return False
    return hmac.compare_digest(supplied, expected)
