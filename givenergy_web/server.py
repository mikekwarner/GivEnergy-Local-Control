"""FastAPI Web Server, REST API & Real-Time WebSocket Hub for GivEnergy Local Control on Raspberry Pi 5."""

from __future__ import annotations

import asyncio
import csv
import hmac
import io
import json
import os
import platform
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from dataclasses import asdict

from pydantic import BaseModel, Field

from givenergy_web import __version__ as APP_VERSION
from givenergy_web.config import PropertyConfig, _detect_local_ipv4, load_config, save_config, scan_lan_for_devices
from givenergy_web.database import HistoricDatabase
from givenergy_web.poller import AsyncPlantPoller
from givenergy_web.updater import check_github_update, get_app_update_status, start_app_update
from givenergy_web.tariffs import (
    UK_GSP_REGIONS,
    UK_TARIFF_PRESETS,
    TariffConfig,
    TariffRateCache,
    TariffSideConfig,
    aggregate_cost_periods,
    calculate_daily_costs,
    calculate_intraday_costs,
    fetch_octopus_product_summary,
    fetch_octopus_products,
    get_current_rates,
    load_tariff_config,
    lookup_gsp_by_postcode,
    save_tariff_config,
    sync_octopus_tariff,
    sync_price_cap,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_DB_PATH = Path(os.environ.get("GIVENERGY_DB_PATH", str(BASE_DIR.parent / "data" / "givenergy_history.db")))
DEFAULT_CONFIG_PATH = Path(
    os.environ.get("GIVENERGY_CONFIG_PATH", str(DEFAULT_DB_PATH.parent / "property_config.json"))
)
DEFAULT_TARIFF_CONFIG_PATH = Path(
    os.environ.get("GIVENERGY_TARIFF_CONFIG_PATH", str(DEFAULT_DB_PATH.parent / "tariff_config.json"))
)
DB_FLUSH_INTERVAL_S = float(os.environ.get("GIVENERGY_DB_FLUSH_INTERVAL", "60.0"))
property_config = load_config(DEFAULT_CONFIG_PATH)
tariff_config = load_tariff_config(DEFAULT_TARIFF_CONFIG_PATH)
tariff_cache = TariffRateCache(DEFAULT_DB_PATH)


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


ws_hub = WebSocketHub()
db = HistoricDatabase(db_path=DEFAULT_DB_PATH, flush_interval_s=DB_FLUSH_INTERVAL_S)
poller = AsyncPlantPoller(
    db=db,
    config=property_config,
    broadcast_cb=ws_hub.broadcast,
    config_path=DEFAULT_CONFIG_PATH,
)


async def _sync_configured_octopus_rates() -> dict[str, Any]:
    """Sync any configured Octopus API tariffs into the local SQLite rate cache."""
    global tariff_config
    synced: dict[str, Any] = {}
    if tariff_config.import_tariff.mode == "octopus_api" and tariff_config.import_tariff.octopus_product_code:
        res = await asyncio.to_thread(
            sync_octopus_tariff,
            tariff_cache,
            tariff_config.import_tariff.octopus_product_code,
            tariff_config.region,
        )
        tariff_config.import_tariff.octopus_tariff_code = res["tariff_code"]
        tariff_config.import_tariff.default_rate_p = res["current_rate_p"]
        if res.get("standing_charge_p") is not None:
            tariff_config.standing_charge_p = float(res["standing_charge_p"])
        synced["import"] = res
    if tariff_config.export_tariff.mode == "octopus_api" and tariff_config.export_tariff.octopus_product_code:
        res = await asyncio.to_thread(
            sync_octopus_tariff,
            tariff_cache,
            tariff_config.export_tariff.octopus_product_code,
            tariff_config.region,
        )
        tariff_config.export_tariff.octopus_tariff_code = res["tariff_code"]
        tariff_config.export_tariff.default_rate_p = res["current_rate_p"]
        synced["export"] = res
    if tariff_config.price_cap_auto_sync:
        try:
            synced["price_cap"] = await asyncio.to_thread(sync_price_cap, tariff_config)
        except Exception:
            pass
    if synced:
        tariff_config.last_octopus_sync = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_tariff_config(DEFAULT_TARIFF_CONFIG_PATH, tariff_config)
    return synced


async def _octopus_periodic_sync_loop() -> None:
    while True:
        try:
            await _sync_configured_octopus_rates()
        except Exception:
            pass
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await poller.start()
    sync_task = asyncio.create_task(_octopus_periodic_sync_loop())
    try:
        yield
    finally:
        sync_task.cancel()
        await poller.stop()


app = FastAPI(
    title="GivEnergy Local Control & Historic Telemetry Service",
    version=APP_VERSION,
    lifespan=lifespan,
)

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_FUNNEL_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>GivEnergy Local Control — Remote Sign In</title>
  <style>
    body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
           background:#0B1120; color:#F1F5F9; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }
    .card { width:min(400px,90vw); background:#111827; border:1px solid #2E3F5E; border-radius:14px; padding:24px; }
    h1 { font-size:18px; margin:0 0 6px; }
    p { font-size:13px; color:#94A3B8; margin:0 0 16px; line-height:1.5; }
    input { width:100%; box-sizing:border-box; padding:10px 12px; border-radius:8px; border:1px solid #2E3F5E;
            background:#090D16; color:#F1F5F9; font-family:monospace; font-size:13px; margin-bottom:12px; }
    button { width:100%; padding:10px; border:none; border-radius:8px; background:#2563EB; color:#fff;
             font-weight:600; font-size:14px; cursor:pointer; }
    .err { color:#F87171; font-size:12.5px; margin-top:10px; min-height:18px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>⚡ GivEnergy Remote Access</h1>
    <p>Enter your Bearer Token (found under <b>⚙ Property Setup</b> when connected on your local network).</p>
    <form onsubmit="return submitToken(event)">
      <input id="tok" type="password" placeholder="Paste Bearer Token…" autocomplete="current-password" required />
      <button type="submit">Unlock Dashboard</button>
    </form>
    <div class="err" id="err"></div>
  </div>
  <script>
    async function submitToken(e) {
      e.preventDefault();
      const err = document.getElementById('err');
      err.textContent = '';
      const token = document.getElementById('tok').value.trim();
      const res = await fetch('/api/auth/verify', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({token})
      });
      if (res.ok) { location.reload(); }
      else { err.textContent = 'Invalid Bearer Token.'; }
      return false;
    }
  </script>
</body>
</html>"""


def _is_funnel_request(headers: Any, client_host: str | None) -> bool:
    """Return True when the request arrives via Tailscale Funnel proxy on localhost."""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return False
    lower_keys = {k.lower() for k in headers.keys()}
    return bool(lower_keys & {"x-forwarded-for", "x-forwarded-host", "tailscale-funnel-request"})


def _extract_bearer_token(headers: Any, query_params: Any, cookies: Any) -> str:
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    q_tok = query_params.get("token", "") if query_params else ""
    if q_tok:
        return str(q_tok).strip()
    c_tok = cookies.get("givenergy_token", "") if cookies else ""
    return str(c_tok).strip()


def _is_authorized(headers: Any, query_params: Any, cookies: Any) -> bool:
    expected = (poller.config.api_token or "").strip()
    if not expected:
        return False
    supplied = _extract_bearer_token(headers, query_params, cookies)
    return bool(supplied) and hmac.compare_digest(supplied, expected)


@app.middleware("http")
async def funnel_bearer_auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in ("/api/auth/verify", "/manifest.json") or path.startswith("/static/icon-"):
        return await call_next(request)

    client_host = request.client.host if request.client else None
    require_auth = _is_funnel_request(request.headers, client_host) or (
        os.environ.get("GIVENERGY_REQUIRE_AUTH", "").strip().lower() in ("1", "true", "yes")
    )
    if require_auth and not _is_authorized(request.headers, request.query_params, request.cookies):
        if path.startswith("/api/"):
            return JSONResponse(
                {"ok": False, "error": "Unauthorized: valid Bearer token required"},
                status_code=401,
            )
        return HTMLResponse(_FUNNEL_LOGIN_HTML, status_code=401)
    return await call_next(request)


class ControlRequest(BaseModel):
    target: str = Field(..., description="'inverter', 'evc', or 'system'")
    action: str = Field(..., description="Action identifier")
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(..., description="Human-readable description of the command")


def _short_hardware_label(model_str: str) -> str:
    """Return a concise board name such as 'Pi 400', 'Pi 5', or 'Pi 4'."""
    if not model_str:
        return "Pi"
    if "Raspberry Pi" in model_str:
        short = model_str.split("Raspberry ", 1)[1]
        for marker in (" Model ", " Rev "):
            if marker in short:
                short = short.split(marker, 1)[0]
        return short.strip() or "Pi"
    return model_str.split()[0][:16]


def _read_pi_system_stats() -> dict[str, Any]:
    """Read Raspberry Pi hardware health metrics (CPU temp, load, RAM, storage)."""
    cpu_temp_c = None
    thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
    if thermal_path.exists():
        try:
            cpu_temp_c = round(int(thermal_path.read_text().strip()) / 1000.0, 1)
        except Exception:
            pass

    load_avg = [0.0, 0.0, 0.0]
    if hasattr(os, "getloadavg"):
        try:
            load_avg = [round(x, 2) for x in os.getloadavg()]
        except Exception:
            pass

    mem_total_mb = 0
    mem_used_mb = 0
    mem_pct = 0.0
    meminfo_path = Path("/proc/meminfo")
    if meminfo_path.exists():
        try:
            info: dict[str, int] = {}
            for line in meminfo_path.read_text().splitlines():
                parts = line.split(":")
                if len(parts) == 2:
                    k = parts[0].strip()
                    v = int(parts[1].strip().split()[0])
                    info[k] = v
            total_kb = info.get("MemTotal", 0)
            avail_kb = info.get("MemAvailable", 0)
            if total_kb > 0:
                mem_total_mb = round(total_kb / 1024)
                mem_used_mb = round((total_kb - avail_kb) / 1024)
                mem_pct = round((total_kb - avail_kb) * 100.0 / total_kb, 1)
        except Exception:
            pass

    disk = shutil.disk_usage(str(DEFAULT_DB_PATH.parent))
    disk_total_gb = round(disk.total / (1024**3), 2)
    disk_used_gb = round(disk.used / (1024**3), 2)
    disk_free_gb = round(disk.free / (1024**3), 2)
    disk_pct = round(disk.used * 100.0 / disk.total, 1) if disk.total else 0.0

    uptime_sec = int(time.time() - poller.started_at)
    hours, rem = divmod(uptime_sec, 3600)
    mins, secs = divmod(rem, 60)

    model_str = platform.machine()
    pi_model_path = Path("/proc/device-tree/model")
    if pi_model_path.exists():
        try:
            model_str = pi_model_path.read_text( errors="ignore").strip("\x00").strip()
        except Exception:
            pass

    reboot_req, reboot_pkgs = _check_reboot_required()
    return {
        "app_version": APP_VERSION,
        "hostname": socket.gethostname(),
        "hardware_model": model_str,
        "hardware_short": _short_hardware_label(model_str),
        "reboot_required": reboot_req,
        "reboot_pkgs": reboot_pkgs,
        "os_release": f"{platform.system()} {platform.release()}",
        "python_version": platform.python_version(),
        "cpu_temp_c": cpu_temp_c,
        "load_avg": load_avg,
        "mem_total_mb": mem_total_mb,
        "mem_used_mb": mem_used_mb,
        "mem_pct": mem_pct,
        "disk_total_gb": disk_total_gb,
        "disk_used_gb": disk_used_gb,
        "disk_free_gb": disk_free_gb,
        "disk_pct": disk_pct,
        "service_uptime_sec": uptime_sec,
        "service_uptime_str": f"{hours}h {mins}m {secs}s",
        "ws_clients": len(ws_hub.clients),
        "database": db.get_db_stats(),
    }


@app.get("/", response_class=FileResponse)
async def serve_index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/manifest.json", response_class=FileResponse)
async def serve_manifest():
    return FileResponse(str(STATIC_DIR / "manifest.json"))


@app.get("/api/snapshot")
async def api_snapshot():
    payload = poller.get_full_payload()
    payload["system"] = _read_pi_system_stats()
    return JSONResponse(payload)


@app.get("/api/history/intraday")
async def api_history_intraday(date: str | None = Query(default=None, description="YYYY-MM-DD")):
    series = await asyncio.to_thread(db.get_intraday_series, date)
    return JSONResponse({"date": date, "count": len(series), "series": series})


@app.get("/api/history/range")
async def api_history_range(
    days: int = Query(default=7, ge=1, le=365),
    end_date: str | None = Query(default=None, description="YYYY-MM-DD"),
):
    series = await asyncio.to_thread(db.get_range_series, days, end_date)
    return JSONResponse({"days": days, "end_date": end_date, "count": len(series), "series": series})


@app.get("/api/history/daily")
async def api_history_daily(limit: int = Query(default=3650, ge=1, le=10000)):
    days = await asyncio.to_thread(db.get_daily_summaries, limit)
    return JSONResponse({"count": len(days), "days": days})


@app.get("/api/history/export.csv")
async def api_export_csv(
    mode: str = Query(default="daily", description="'daily' or 'intraday'"),
    date: str | None = Query(default=None, description="YYYY-MM-DD for intraday export"),
):
    buf = io.StringIO()
    if mode == "intraday":
        rows = await asyncio.to_thread(db.get_intraday_series, date)
        filename = f"givenergy_intraday_{date or 'today'}.csv"
    else:
        rows = await asyncio.to_thread(db.get_daily_summaries, 365)
        filename = "givenergy_daily_ledger.csv"

    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    else:
        buf.write("No records available\n")

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class PropertyConfigRequest(BaseModel):
    property_name: str = "Home"
    inverter_enabled: bool = False
    inverter_ip: str = ""
    inverter_port: int = 8899
    evc_enabled: bool = False
    evc_ip: str = ""
    evc_port: int = 502
    has_solar_pv: str = "auto"
    poll_interval_s: float = 5.0


@app.get("/api/config")
async def api_get_config():
    cfg_dict = asdict(poller.config)
    cfg_dict.pop("api_token", None)
    cfg_dict["api_token_configured"] = bool(poller.config.api_token)
    return JSONResponse(cfg_dict)


@app.post("/api/config")
async def api_save_config(req: PropertyConfigRequest):
    inv_ip = req.inverter_ip.strip()
    evc_ip = req.evc_ip.strip()
    cfg = PropertyConfig(
        property_name=req.property_name.strip() or "Home",
        inverter_enabled=bool(req.inverter_enabled and inv_ip),
        inverter_ip=inv_ip,
        inverter_port=int(req.inverter_port),
        evc_enabled=bool(req.evc_enabled and evc_ip),
        evc_ip=evc_ip,
        evc_port=int(req.evc_port),
        has_solar_pv=req.has_solar_pv if req.has_solar_pv in ("auto", "yes", "no") else "auto",
        poll_interval_s=max(2.0, float(req.poll_interval_s)),
        api_token=poller.config.api_token or secrets.token_urlsafe(32),
        evc_charging_mode=poller.config.evc_charging_mode,
        evc_max_battery_discharge_w=poller.config.evc_max_battery_discharge_w,
        evc_solar_export_split_pct=poller.config.evc_solar_export_split_pct,
        evc_user_charge_limit_a=poller.config.evc_user_charge_limit_a,
    )
    save_config(DEFAULT_CONFIG_PATH, cfg)
    await poller.apply_config(cfg)
    resp_cfg = asdict(cfg)
    resp_cfg.pop("api_token", None)
    return JSONResponse({"ok": True, "config": resp_cfg})


class TariffSideRequest(BaseModel):
    mode: str = "preset"
    preset_id: str = ""
    label: str = "Standard Variable Tariff (Flat)"
    description: str = ""
    octopus_product_code: str = ""
    octopus_tariff_code: str = ""
    default_rate_p: float = 24.5
    windows: list[dict[str, Any]] = Field(default_factory=list)


class TariffConfigRequest(BaseModel):
    currency_symbol: str = "£"
    sub_unit_symbol: str = "p"
    region: str = "H"
    postcode: str = ""
    standing_charge_p: float = 60.0
    import_tariff: TariffSideRequest
    export_tariff: TariffSideRequest
    price_cap_rate_p: float | None = None
    price_cap_standing_charge_p: float | None = None
    price_cap_auto_sync: bool | None = None


@app.get("/api/tariffs/presets")
async def api_tariff_presets():
    return JSONResponse({"regions": UK_GSP_REGIONS, "presets": UK_TARIFF_PRESETS})


@app.get("/api/tariffs/config")
async def api_get_tariff_config():
    data = asdict(tariff_config)
    data["current_rates"] = await asyncio.to_thread(get_current_rates, tariff_config, tariff_cache)
    return JSONResponse(data)


@app.post("/api/tariffs/config")
async def api_save_tariff_config(req: TariffConfigRequest):
    global tariff_config
    region = req.region.strip().upper()
    if region not in UK_GSP_REGIONS:
        region = "H"
    tariff_config = TariffConfig(
        currency_symbol=req.currency_symbol.strip() or "£",
        sub_unit_symbol=req.sub_unit_symbol.strip() or "p",
        region=region,
        postcode=req.postcode.strip().upper(),
        standing_charge_p=float(req.standing_charge_p),
        import_tariff=TariffSideConfig(
            mode=req.import_tariff.mode,
            preset_id=req.import_tariff.preset_id,
            label=req.import_tariff.label.strip() or "Import Tariff",
            description=req.import_tariff.description.strip(),
            octopus_product_code=req.import_tariff.octopus_product_code.strip(),
            octopus_tariff_code=req.import_tariff.octopus_tariff_code.strip(),
            default_rate_p=float(req.import_tariff.default_rate_p),
            windows=req.import_tariff.windows,
        ),
        export_tariff=TariffSideConfig(
            mode=req.export_tariff.mode,
            preset_id=req.export_tariff.preset_id,
            label=req.export_tariff.label.strip() or "Export Tariff",
            description=req.export_tariff.description.strip(),
            octopus_product_code=req.export_tariff.octopus_product_code.strip(),
            octopus_tariff_code=req.export_tariff.octopus_tariff_code.strip(),
            default_rate_p=float(req.export_tariff.default_rate_p),
            windows=req.export_tariff.windows,
        ),
        last_octopus_sync=tariff_config.last_octopus_sync,
        price_cap_rate_p=float(req.price_cap_rate_p) if req.price_cap_rate_p is not None else tariff_config.price_cap_rate_p,
        price_cap_standing_charge_p=float(req.price_cap_standing_charge_p) if req.price_cap_standing_charge_p is not None else tariff_config.price_cap_standing_charge_p,
        price_cap_auto_sync=bool(req.price_cap_auto_sync) if req.price_cap_auto_sync is not None else tariff_config.price_cap_auto_sync,
        price_cap_source="Manual Override" if req.price_cap_auto_sync is False else tariff_config.price_cap_source,
        price_cap_updated_at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()) if req.price_cap_auto_sync is False else tariff_config.price_cap_updated_at,
        comparison_tariffs=list(tariff_config.comparison_tariffs),
        hidden_comparison_presets=list(tariff_config.hidden_comparison_presets),
    )
    save_tariff_config(DEFAULT_TARIFF_CONFIG_PATH, tariff_config)
    sync_info: dict[str, Any] = {}
    sync_error: str | None = None
    try:
        sync_info = await _sync_configured_octopus_rates()
    except Exception as exc:
        sync_error = str(exc)

    resp = asdict(tariff_config)
    resp["current_rates"] = await asyncio.to_thread(get_current_rates, tariff_config, tariff_cache)
    return JSONResponse({"ok": True, "config": resp, "synced": sync_info, "sync_error": sync_error})


@app.post("/api/tariffs/price-cap/sync")
async def api_sync_price_cap():
    try:
        res = await asyncio.to_thread(sync_price_cap, tariff_config)
        save_tariff_config(DEFAULT_TARIFF_CONFIG_PATH, tariff_config)
        return JSONResponse({"ok": True, **res})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.get("/api/tariffs/octopus/products")
async def api_octopus_products(refresh: bool = Query(default=False)):
    try:
        prods = await asyncio.to_thread(fetch_octopus_products, refresh)
        return JSONResponse({"ok": True, **prods})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.get("/api/tariffs/octopus/product-rates")
async def api_octopus_product_rates(
    product_code: str = Query(..., min_length=2),
    region: str = Query(default="H"),
):
    try:
        reg = region.strip().upper()
        if reg not in UK_GSP_REGIONS:
            reg = "H"
        info = await asyncio.to_thread(fetch_octopus_product_summary, product_code.strip(), reg)
        return JSONResponse({"ok": True, **info})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.get("/api/tariffs/octopus/lookup-postcode")
async def api_octopus_lookup_postcode(postcode: str = Query(..., min_length=2)):
    try:
        region = await asyncio.to_thread(lookup_gsp_by_postcode, postcode)
        return JSONResponse({"ok": True, "region": region, "region_name": UK_GSP_REGIONS.get(region, region)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/tariffs/octopus/sync")
async def api_octopus_sync():
    try:
        synced = await _sync_configured_octopus_rates()
        rates = await asyncio.to_thread(get_current_rates, tariff_config, tariff_cache)
        return JSONResponse({"ok": True, "synced": synced, "current_rates": rates, "last_sync": tariff_config.last_octopus_sync})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.get("/api/costs/summary")
async def api_costs_summary(
    days: int = Query(default=30, ge=0, le=36500),
    period: str = Query(default="days", pattern="^(days|weeks|months|years|all)$"),
):
    current_rates = await asyncio.to_thread(get_current_rates, tariff_config, tariff_cache)
    daily = await asyncio.to_thread(calculate_daily_costs, db, tariff_config, tariff_cache, days)
    daily["period"] = period
    daily["rows"] = aggregate_cost_periods(daily["days"], period)
    today_str = time.strftime("%Y-%m-%d")
    today_row = next((d for d in daily["days"] if d["date_str"] == today_str), None)
    if today_row is None:
        intraday = await asyncio.to_thread(calculate_intraday_costs, db, tariff_config, tariff_cache, today_str)
        today_totals = intraday["totals"]
        today_totals["savings_gbp"] = 0.0
    else:
        today_totals = {
            "import_kwh": today_row["import_kwh"],
            "export_kwh": today_row["export_kwh"],
            "import_cost_gbp": today_row["import_cost_gbp"],
            "export_income_gbp": today_row["export_income_gbp"],
            "standing_charge_gbp": today_row["standing_charge_gbp"],
            "net_cost_gbp": today_row["net_cost_gbp"],
            "savings_gbp": today_row["savings_gbp"],
        }
    return JSONResponse({
        "currency_symbol": tariff_config.currency_symbol,
        "sub_unit_symbol": tariff_config.sub_unit_symbol,
        "region": tariff_config.region,
        "current_rates": current_rates,
        "today": today_totals,
        "daily": daily,
    })


@app.get("/api/costs/intraday")
async def api_costs_intraday(date: str | None = Query(default=None, description="YYYY-MM-DD")):
    data = await asyncio.to_thread(calculate_intraday_costs, db, tariff_config, tariff_cache, date)
    data["currency_symbol"] = tariff_config.currency_symbol
    data["sub_unit_symbol"] = tariff_config.sub_unit_symbol
    return JSONResponse(data)


class CompareTariffCandidate(BaseModel):
    name: str = ""
    import_preset_id: str | None = None
    export_preset_id: str | None = None
    standing_charge_p: float | None = None
    custom_import_rate_p: float | None = None
    custom_export_rate_p: float | None = None
    custom_import_windows: list[dict[str, Any]] | None = None
    custom_export_windows: list[dict[str, Any]] | None = None


class TariffCompareRequest(BaseModel):
    days: int = Field(default=30, ge=0, le=36500)
    candidates: list[CompareTariffCandidate] = Field(default_factory=list)


def _side_from_preset(preset: dict[str, Any]) -> TariffSideConfig:
    return TariffSideConfig(
        mode="preset",
        preset_id=str(preset.get("id", "")),
        label=str(preset.get("name", "")),
        description=str(preset.get("notes", "")),
        default_rate_p=float(preset.get("default_rate_p", 24.5)),
        windows=list(preset.get("windows") or []),
    )


def _format_side_summary(side: TariffSideConfig) -> str:
    wins = side.windows or []
    if wins:
        rates = [float(w.get("rate_p", side.default_rate_p)) for w in wins]
        lo = min(float(side.default_rate_p), min(rates))
        hi = max(float(side.default_rate_p), max(rates))
        return f"{lo:.1f}p–{hi:.1f}p"
    return f"{float(side.default_rate_p):.2f}p"


@app.post("/api/tariffs/compare")
async def api_tariffs_compare(req: TariffCompareRequest):
    imp_presets = {p["id"]: p for p in UK_TARIFF_PRESETS.get("import", [])}
    exp_presets = {p["id"]: p for p in UK_TARIFF_PRESETS.get("export", [])}

    def _run_compare() -> dict[str, Any]:
        baseline = calculate_daily_costs(db, tariff_config, tariff_cache, req.days)["totals"]
        days_count = int(baseline.get("days_count", 0) or 0)
        base_net = float(baseline.get("net_cost_gbp", 0.0) or 0.0)

        candidates = req.candidates
        if not candidates:
            hidden = set(tariff_config.hidden_comparison_presets)
            candidates = [
                CompareTariffCandidate(
                    name=f"{p.get('supplier', '')} — {p.get('name', '')}".strip(" —"),
                    import_preset_id=p["id"],
                )
                for p in UK_TARIFF_PRESETS.get("import", [])
                if p["id"] not in hidden
            ]
            for ct in tariff_config.comparison_tariffs:
                candidates.append(
                    CompareTariffCandidate(
                        name=str(ct.get("name", "Custom Tariff")),
                        custom_import_rate_p=float(ct.get("default_rate_p", 24.5)),
                        custom_export_rate_p=float(ct["export_rate_p"]) if ct.get("export_rate_p") is not None else None,
                        standing_charge_p=float(ct["standing_charge_p"]) if ct.get("standing_charge_p") is not None else None,
                        custom_import_windows=list(ct.get("windows") or []),
                    )
                )

        results: list[dict[str, Any]] = []
        for cand in candidates:
            imp_p = imp_presets.get(cand.import_preset_id or "") if cand.import_preset_id else None
            exp_p = exp_presets.get(cand.export_preset_id or "") if cand.export_preset_id else None

            if imp_p is not None:
                imp_side = _side_from_preset(imp_p)
            elif cand.custom_import_rate_p is not None:
                imp_side = TariffSideConfig(
                    mode="custom",
                    label=cand.name or "Custom Import",
                    default_rate_p=float(cand.custom_import_rate_p),
                    windows=list(cand.custom_import_windows or []),
                )
            else:
                imp_side = tariff_config.import_tariff

            if exp_p is not None:
                exp_side = _side_from_preset(exp_p)
            elif cand.custom_export_rate_p is not None:
                exp_side = TariffSideConfig(
                    mode="custom",
                    label=cand.name or "Custom Export",
                    default_rate_p=float(cand.custom_export_rate_p),
                    windows=list(cand.custom_export_windows or []),
                )
            else:
                exp_side = tariff_config.export_tariff

            sc_p = (
                float(cand.standing_charge_p)
                if cand.standing_charge_p is not None
                else float(imp_p.get("standing_charge_p", tariff_config.standing_charge_p))
                if imp_p is not None
                else float(tariff_config.standing_charge_p)
            )

            cand_cfg = TariffConfig(
                currency_symbol=tariff_config.currency_symbol,
                sub_unit_symbol=tariff_config.sub_unit_symbol,
                region=tariff_config.region,
                postcode=tariff_config.postcode,
                standing_charge_p=sc_p,
                import_tariff=imp_side,
                export_tariff=exp_side,
                price_cap_rate_p=tariff_config.price_cap_rate_p,
                price_cap_standing_charge_p=tariff_config.price_cap_standing_charge_p,
            )

            totals = calculate_daily_costs(db, cand_cfg, tariff_cache, req.days)["totals"]
            cand_net = float(totals.get("net_cost_gbp", 0.0) or 0.0)
            diff_gbp = round(cand_net - base_net, 2)
            annual_est_diff = round((diff_gbp / days_count) * 365.0, 2) if days_count > 0 else 0.0

            label = cand.name.strip() or (
                f"{imp_side.label}" + (f" + {exp_side.label}" if exp_p is not None or cand.custom_export_rate_p is not None else "")
            )
            results.append(
                {
                    "name": label,
                    "import_rate_summary": _format_side_summary(imp_side),
                    "export_rate_summary": _format_side_summary(exp_side),
                    "standing_charge_p": round(sc_p, 2),
                    "import_cost_gbp": totals["import_cost_gbp"],
                    "export_income_gbp": totals["export_income_gbp"],
                    "standing_charge_gbp": totals["standing_charge_gbp"],
                    "net_cost_gbp": totals["net_cost_gbp"],
                    "avg_daily_net_gbp": totals["avg_daily_net_gbp"],
                    "diff_vs_current_gbp": diff_gbp,
                    "annual_est_diff_gbp": annual_est_diff,
                }
            )

        results.sort(key=lambda r: r["net_cost_gbp"])
        return {
            "ok": True,
            "currency_symbol": tariff_config.currency_symbol,
            "days": req.days,
            "days_count": days_count,
            "current": {
                "name": f"{tariff_config.import_tariff.label} / {tariff_config.export_tariff.label}",
                "import_rate_summary": _format_side_summary(tariff_config.import_tariff),
                "export_rate_summary": _format_side_summary(tariff_config.export_tariff),
                "standing_charge_p": round(tariff_config.standing_charge_p, 2),
                **baseline,
            },
            "comparisons": results,
        }

    payload = await asyncio.to_thread(_run_compare)
    return JSONResponse(payload)


class SavedComparisonTariffRequest(BaseModel):
    name: str
    default_rate_p: float
    export_rate_p: float | None = None
    standing_charge_p: float | None = None
    windows: list[dict[str, Any]] = Field(default_factory=list)


@app.post("/api/tariffs/compare/saved")
async def api_save_comparison_tariff(req: SavedComparisonTariffRequest):
    name = req.name.strip()
    if not name:
        return JSONResponse({"ok": False, "error": "Tariff name is required."}, status_code=400)
    clean_windows = []
    for w in req.windows:
        if isinstance(w, dict):
            clean_windows.append(
                {
                    "start": str(w.get("start", "00:00"))[:5],
                    "end": str(w.get("end", "07:00"))[:5],
                    "rate_p": float(w.get("rate_p", 0.0)),
                    "label": str(w.get("label", "")).strip() or "Off-Peak",
                }
            )
    item = {
        "id": f"custom_{secrets.token_hex(4)}",
        "name": name,
        "default_rate_p": float(req.default_rate_p),
        "export_rate_p": float(req.export_rate_p) if req.export_rate_p is not None else None,
        "standing_charge_p": float(req.standing_charge_p) if req.standing_charge_p is not None else None,
        "windows": clean_windows,
    }
    tariff_config.comparison_tariffs.append(item)
    save_tariff_config(DEFAULT_TARIFF_CONFIG_PATH, tariff_config)
    return JSONResponse(
        {
            "ok": True,
            "item": item,
            "comparison_tariffs": tariff_config.comparison_tariffs,
            "hidden_comparison_presets": tariff_config.hidden_comparison_presets,
        }
    )


@app.delete("/api/tariffs/compare/saved/{item_id}")
async def api_delete_comparison_tariff(item_id: str):
    before = len(tariff_config.comparison_tariffs)
    tariff_config.comparison_tariffs = [t for t in tariff_config.comparison_tariffs if t.get("id") != item_id]
    if len(tariff_config.comparison_tariffs) == before:
        preset_ids = {p["id"] for p in UK_TARIFF_PRESETS.get("import", [])}
        if item_id in preset_ids and item_id not in tariff_config.hidden_comparison_presets:
            tariff_config.hidden_comparison_presets.append(item_id)
    save_tariff_config(DEFAULT_TARIFF_CONFIG_PATH, tariff_config)
    return JSONResponse(
        {
            "ok": True,
            "comparison_tariffs": tariff_config.comparison_tariffs,
            "hidden_comparison_presets": tariff_config.hidden_comparison_presets,
        }
    )


@app.post("/api/tariffs/compare/reset-presets")
async def api_reset_comparison_presets():
    tariff_config.hidden_comparison_presets = []
    save_tariff_config(DEFAULT_TARIFF_CONFIG_PATH, tariff_config)
    return JSONResponse(
        {
            "ok": True,
            "comparison_tariffs": tariff_config.comparison_tariffs,
            "hidden_comparison_presets": tariff_config.hidden_comparison_presets,
        }
    )


class TokenVerifyRequest(BaseModel):
    token: str = ""


def _read_tailscale_funnel_info() -> dict[str, str]:
    info = {"tailscale_ip": "", "tailscale_dns": "", "funnel_url": ""}
    try:
        proc = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=3.0)
        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout)
            self_node = data.get("Self") or {}
            ips = self_node.get("TailscaleIPs") or []
            info["tailscale_ip"] = next((ip for ip in ips if "." in ip), "")
            dns = (self_node.get("DNSName") or "").rstrip(".")
            info["tailscale_dns"] = dns
            if dns:
                info["funnel_url"] = f"https://{dns}"
    except Exception:
        pass
    return info


@app.post("/api/auth/verify")
async def api_auth_verify(req: TokenVerifyRequest, request: Request):
    expected = (poller.config.api_token or "").strip()
    supplied = (req.token or "").strip()
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        return JSONResponse({"ok": False, "error": "Invalid token"}, status_code=401)
    resp = JSONResponse({"ok": True})
    is_https = request.headers.get("x-forwarded-proto", "").lower() == "https"
    resp.set_cookie(
        key="givenergy_token",
        value=expected,
        httponly=True,
        samesite="lax",
        secure=is_https,
        max_age=60 * 60 * 24 * 30,
    )
    return resp


@app.get("/api/auth/pairing")
async def api_auth_pairing(request: Request):
    client_host = request.client.host if request.client else None
    if _is_funnel_request(request.headers, client_host):
        return JSONResponse({"ok": False, "error": "Pairing info is only available on the local network"}, status_code=403)
    ts_info = await asyncio.to_thread(_read_tailscale_funnel_info)
    return JSONResponse({
        "ok": True,
        "hostname": socket.gethostname(),
        "api_token": poller.config.api_token,
        **ts_info,
    })


@app.post("/api/auth/regenerate-token")
async def api_auth_regenerate_token(request: Request):
    client_host = request.client.host if request.client else None
    if _is_funnel_request(request.headers, client_host):
        return JSONResponse({"ok": False, "error": "Token regeneration is only available on the local network"}, status_code=403)
    new_token = secrets.token_urlsafe(32)
    poller.config.api_token = new_token
    save_config(DEFAULT_CONFIG_PATH, poller.config)
    return JSONResponse({"ok": True, "api_token": new_token})


@app.get("/api/auth/pairing/qr.svg")
async def api_auth_pairing_qr(request: Request):
    client_host = request.client.host if request.client else None
    if _is_funnel_request(request.headers, client_host):
        return JSONResponse({"ok": False, "error": "QR pairing is only available on the local network"}, status_code=403)
    import qrcode
    import qrcode.image.svg

    ts_info = await asyncio.to_thread(_read_tailscale_funnel_info)
    lan_ip = await asyncio.to_thread(_detect_local_ipv4)
    payload = json.dumps(
        {
            "url": ts_info.get("funnel_url") or "",
            "lan_url": f"http://{lan_ip}" if lan_ip else "",
            "token": poller.config.api_token,
            "name": poller.config.property_name,
        },
        separators=(",", ":"),
    )
    img = qrcode.make(payload, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@app.post("/api/scan")
async def api_scan_network():
    results = await scan_lan_for_devices()
    return JSONResponse(results)


@app.get("/api/system/stats")
async def api_system_stats():
    stats = await asyncio.to_thread(_read_pi_system_stats)
    return JSONResponse(stats)


def _run_sudo_power_action(action: str) -> tuple[bool, str]:
    """Trigger reboot or poweroff via passwordless sudo after a short delay."""
    cmd_arg = "reboot" if action == "reboot" else "poweroff"
    check = subprocess.run(["sudo", "-n", "-l", "systemctl", cmd_arg], capture_output=True, text=True, timeout=5)
    if check.returncode != 0:
        return False, "Passwordless sudo is not configured for this service user. Re-deploy with the installer to enable."
    subprocess.Popen(
        ["bash", "-c", f"sleep 1 && sudo -n systemctl {cmd_arg}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True, f"Raspberry Pi {cmd_arg} scheduled."


def _check_reboot_required() -> tuple[bool, list[str]]:
    """Return (reboot_required, package_list) from Debian/Raspberry Pi OS marker files."""
    req_path = Path("/var/run/reboot-required")
    if not req_path.exists():
        return False, []
    pkgs: list[str] = []
    pkgs_path = Path("/var/run/reboot-required.pkgs")
    if pkgs_path.exists():
        try:
            pkgs = [ln.strip() for ln in pkgs_path.read_text(errors="ignore").splitlines() if ln.strip()]
        except Exception:
            pass
    return True, pkgs


_update_lock = threading.Lock()
_update_state: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "status_text": "Idle",
    "percent": 0,
    "ok": None,
    "reboot_required": False,
    "reboot_pkgs": [],
    "output": "",
}


def _stream_apt_cmd(cmd: list[str], env: dict[str, str], base_pct: int, max_pct: int) -> int:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    pct = base_pct
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.rstrip("\r\n")
        if line.startswith(("debconf:", "sudo:  is owned by")):
            continue
        if any(k in line for k in ("Get:", "Hit:", "Unpacking ", "Setting up ", "Processing triggers")):
            pct = min(max_pct, pct + 2)
        with _update_lock:
            _update_state["percent"] = pct
            _update_state["output"] = (_update_state["output"] + "\n" + line).lstrip("\n")[-8000:]
    return proc.wait()


def _run_system_updates_bg() -> None:
    """Run apt-get update && apt-get upgrade -y in background with live progress."""
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    try:
        with _update_lock:
            _update_state.update({
                "running": True,
                "phase": "update",
                "status_text": "Checking for Raspberry Pi OS package updates (apt-get update)...",
                "percent": 10,
                "ok": None,
                "output": "=== Step 1/2: Running apt-get update ===",
            })
        rc1 = _stream_apt_cmd(["sudo", "-n", "apt-get", "update"], env, 12, 35)
        if rc1 != 0:
            with _update_lock:
                _update_state.update({
                    "running": False,
                    "phase": "error",
                    "status_text": f"Software update failed during apt-get update (exit code {rc1}).",
                    "percent": 100,
                    "ok": False,
                })
            return

        with _update_lock:
            _update_state.update({
                "phase": "upgrade",
                "status_text": "Installing available OS software updates (apt-get upgrade -y)...",
                "percent": 40,
                "output": (_update_state["output"] + "\n\n=== Step 2/2: Running apt-get upgrade -y ===")[-8000:],
            })
        rc2 = _stream_apt_cmd(["sudo", "-n", "apt-get", "upgrade", "-y"], env, 42, 96)
        reboot_req, reboot_pkgs = _check_reboot_required()
        with _update_lock:
            if rc2 != 0:
                status_msg = f"❌ Software update failed (exit code {rc2})."
            elif reboot_req:
                status_msg = "✅ Software updates complete — reboot required to apply updates."
            else:
                status_msg = "✅ Software updates complete! System is up to date (no reboot required)."
            _update_state.update({
                "running": False,
                "phase": "done" if rc2 == 0 else "error",
                "status_text": status_msg,
                "percent": 100,
                "ok": rc2 == 0,
                "reboot_required": reboot_req,
                "reboot_pkgs": reboot_pkgs,
                "output": (_update_state["output"] + "\n\n=== Update process finished ===")[-8000:],
            })
    except Exception as exc:
        with _update_lock:
            _update_state.update({
                "running": False,
                "phase": "error",
                "status_text": f"❌ Software update error: {exc}",
                "percent": 100,
                "ok": False,
                "output": (_update_state["output"] + f"\n[ERROR] {exc}")[-8000:],
            })


@app.post("/api/system/reboot")
async def api_system_reboot():
    ok, msg = await asyncio.to_thread(_run_sudo_power_action, "reboot")
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 500)


@app.post("/api/system/shutdown")
async def api_system_shutdown():
    ok, msg = await asyncio.to_thread(_run_sudo_power_action, "poweroff")
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 500)


@app.post("/api/system/update")
async def api_system_update():
    with _update_lock:
        if _update_state["running"]:
            return JSONResponse({"ok": True, "started": False, **_update_state})
        t = threading.Thread(target=_run_system_updates_bg, daemon=True)
        t.start()
    return JSONResponse({"ok": True, "started": True})


@app.get("/api/system/update/status")
async def api_system_update_status():
    with _update_lock:
        return JSONResponse(dict(_update_state))


@app.get("/api/system/app-update/check")
async def api_system_app_update_check():
    info = await asyncio.to_thread(check_github_update, APP_VERSION)
    return JSONResponse(info, status_code=200 if info.get("ok") else 502)


@app.post("/api/system/app-update")
async def api_system_app_update():
    res = start_app_update(BASE_DIR, APP_VERSION)
    return JSONResponse(res)


@app.get("/api/system/app-update/status")
async def api_system_app_update_status():
    return JSONResponse(get_app_update_status())


@app.post("/api/control")
async def api_control(req: ControlRequest):
    if req.target not in ("inverter", "evc", "system"):
        return JSONResponse({"ok": False, "error": "Invalid target"}, status_code=400)
    await poller.enqueue_command(
        target=req.target,
        action=req.action,
        params=req.params,
        description=req.description,
    )
    return JSONResponse({"ok": True, "queued": req.description})


@app.post("/api/poll-now")
async def api_poll_now():
    poller.trigger_poll_now()
    return JSONResponse({"ok": True})


@app.post("/api/flush-db")
async def api_flush_db():
    await asyncio.to_thread(db.flush)
    return JSONResponse({"ok": True, "stats": db.get_db_stats()})


@app.get("/api/backup/export")
async def api_backup_export(
    include_config: bool = Query(True),
    include_history: bool = Query(True),
):
    if not include_config and not include_history:
        return JSONResponse({"ok": False, "error": "Select configuration and/or historic data to export."}, status_code=400)

    def _build_backup_zip() -> tuple[bytes, str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            zip_buf = io.BytesIO()
            stats = db.get_db_stats() if include_history else {}
            manifest = {
                "format_version": 1,
                "app_version": APP_VERSION,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "hostname": socket.gethostname(),
                "property_name": poller.config.property_name,
                "includes": {
                    "config": bool(include_config),
                    "history": bool(include_history),
                },
                "stats": stats,
            }

            with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))
                if include_config:
                    zf.writestr("property_config.json", json.dumps(asdict(poller.config), indent=2))
                    zf.writestr("tariff_config.json", json.dumps(asdict(tariff_config), indent=2))
                if include_history:
                    tmp_db = tmp_root / "givenergy_history.db"
                    db.backup_to_file(tmp_db)
                    zf.write(tmp_db, arcname="givenergy_history.db")

            slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (poller.config.property_name or "home").strip()).strip("-") or "home"
            stamp = time.strftime("%Y%m%d-%H%M%S")
            return zip_buf.getvalue(), f"givenergy-backup-{slug}-{stamp}.zip"

    payload_bytes, filename = await asyncio.to_thread(_build_backup_zip)
    return Response(
        content=payload_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/backup/import")
async def api_backup_import(
    request: Request,
    restore_config: bool = Query(True),
    restore_history: bool = Query(True),
    preserve_api_token: bool = Query(False),
):
    global tariff_config
    if not restore_config and not restore_history:
        return JSONResponse({"ok": False, "error": "Select configuration and/or historic data to restore."}, status_code=400)

    body = await request.body()
    max_bytes = 512 * 1024 * 1024
    if not body:
        return JSONResponse({"ok": False, "error": "Empty backup file uploaded."}, status_code=400)
    if len(body) > max_bytes:
        return JSONResponse({"ok": False, "error": "Backup file exceeds 512 MB limit."}, status_code=413)

    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        return JSONResponse({"ok": False, "error": "Uploaded file is not a valid .zip backup archive."}, status_code=400)

    with zf, tempfile.TemporaryDirectory() as tmpdir:
        names = set(zf.namelist())
        manifest: dict[str, Any] = {}
        if "manifest.json" in names:
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                if not isinstance(manifest, dict):
                    return JSONResponse({"ok": False, "error": "Invalid manifest.json in backup."}, status_code=400)
            except Exception as exc:
                return JSONResponse({"ok": False, "error": f"Corrupt manifest.json: {exc}"}, status_code=400)

        prop_raw: dict[str, Any] | None = None
        tariff_raw: dict[str, Any] | None = None
        if restore_config:
            if "property_config.json" not in names and "tariff_config.json" not in names:
                return JSONResponse(
                    {"ok": False, "error": "Backup archive does not contain configuration files."},
                    status_code=400,
                )
            if "property_config.json" in names:
                try:
                    parsed = json.loads(zf.read("property_config.json").decode("utf-8"))
                    if not isinstance(parsed, dict):
                        raise ValueError("property_config.json must be a JSON object")
                    prop_raw = parsed
                except Exception as exc:
                    return JSONResponse({"ok": False, "error": f"Invalid property_config.json: {exc}"}, status_code=400)
            if "tariff_config.json" in names:
                try:
                    parsed = json.loads(zf.read("tariff_config.json").decode("utf-8"))
                    if not isinstance(parsed, dict):
                        raise ValueError("tariff_config.json must be a JSON object")
                    tariff_raw = parsed
                except Exception as exc:
                    return JSONResponse({"ok": False, "error": f"Invalid tariff_config.json: {exc}"}, status_code=400)

        tmp_db_path: Path | None = None
        if restore_history:
            if "givenergy_history.db" not in names:
                return JSONResponse(
                    {"ok": False, "error": "Backup archive does not contain historic database (givenergy_history.db)."},
                    status_code=400,
                )
            info = zf.getinfo("givenergy_history.db")
            if info.file_size > max_bytes:
                return JSONResponse({"ok": False, "error": "Database inside backup exceeds 512 MB limit."}, status_code=413)
            tmp_db_path = Path(tmpdir) / "givenergy_history.db"
            with zf.open(info) as src_f, tmp_db_path.open("wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f)

            def _validate_sqlite(path: Path) -> str | None:
                conn = sqlite3.connect(str(path), timeout=10.0)
                try:
                    chk = conn.execute("PRAGMA integrity_check;").fetchone()
                    if not chk or str(chk[0]).lower() != "ok":
                        return f"SQLite integrity check failed: {chk[0] if chk else 'unknown'}"
                    tables = {
                        row[0]
                        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                    }
                    required = {"daily_summary", "telemetry_1m", "telemetry_5m"}
                    missing = required - tables
                    if missing:
                        return f"Backup database missing required tables: {', '.join(sorted(missing))}"
                    return None
                except Exception as exc:
                    return f"Invalid SQLite database: {exc}"
                finally:
                    conn.close()

            err = await asyncio.to_thread(_validate_sqlite, tmp_db_path)
            if err:
                return JSONResponse({"ok": False, "error": err}, status_code=400)

        # Apply validated restore
        if restore_history and tmp_db_path is not None:
            await asyncio.to_thread(db.replace_from_file, tmp_db_path)
            await asyncio.to_thread(tariff_cache._init_table)

        restored_cfg = False
        restored_tariff = False
        if restore_config:
            if prop_raw is not None:
                valid_fields = set(PropertyConfig.__dataclass_fields__.keys())
                filtered = {k: v for k, v in prop_raw.items() if k in valid_fields}
                new_cfg = PropertyConfig(**filtered)
                if preserve_api_token and poller.config.api_token:
                    new_cfg.api_token = poller.config.api_token
                elif not new_cfg.api_token:
                    new_cfg.api_token = poller.config.api_token or secrets.token_urlsafe(32)
                save_config(DEFAULT_CONFIG_PATH, new_cfg)
                await poller.apply_config(new_cfg)
                restored_cfg = True

            if tariff_raw is not None:
                DEFAULT_TARIFF_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
                DEFAULT_TARIFF_CONFIG_PATH.write_text(json.dumps(tariff_raw, indent=2), encoding="utf-8")
                tariff_config = load_tariff_config(DEFAULT_TARIFF_CONFIG_PATH)
                restored_tariff = True

    stats = db.get_db_stats()
    return JSONResponse(
        {
            "ok": True,
            "restored_config": restored_cfg,
            "restored_tariff": restored_tariff,
            "restored_history": bool(restore_history),
            "stats": stats,
            "manifest": manifest,
        }
    )


@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    client_host = ws.client.host if ws.client else None
    require_auth = _is_funnel_request(ws.headers, client_host) or (
        os.environ.get("GIVENERGY_REQUIRE_AUTH", "").strip().lower() in ("1", "true", "yes")
    )
    if require_auth and not _is_authorized(ws.headers, ws.query_params, ws.cookies):
        await ws.close(code=1008)
        return
    await ws_hub.connect(ws)
    try:
        # Immediately push current state upon connection
        initial = poller.get_full_payload()
        initial["system"] = _read_pi_system_stats()
        await ws.send_json(initial)
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
            elif msg == "poll":
                poller.trigger_poll_now()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await ws_hub.disconnect(ws)
