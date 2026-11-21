"""FastAPI Web Server, REST API & Real-Time WebSocket Hub for GivEnergy Local Control."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from givenergy_web import __version__ as APP_VERSION
from givenergy_web.config import load_config
from givenergy_web.context import AppContext, AppPaths, WebSocketHub, is_authorized, is_funnel_request
from givenergy_web.database import HistoricDatabase
from givenergy_web.platform_adapter import PlatformAdapter, get_platform_adapter
from givenergy_web.poller import AsyncPlantPoller
from givenergy_web.routes import backup, solar, system, tariffs, telemetry
from givenergy_web.solar_forecast import SolarForecastStore, load_solar_forecast_config
from givenergy_web.tariffs import TariffRateCache, load_tariff_config

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

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


def resolve_app_paths(data_dir: Path | None = None) -> AppPaths:
    """Resolve mutable data and configuration paths from arguments or environment variables."""
    resolved_data = data_dir or Path(os.environ.get("GIVENERGY_DATA_DIR", str(BASE_DIR.parent / "data")))
    db_path = Path(os.environ.get("GIVENERGY_DB_PATH", str(resolved_data / "givenergy_history.db")))
    effective_data = db_path.parent
    return AppPaths(
        base_dir=BASE_DIR,
        static_dir=STATIC_DIR,
        data_dir=effective_data,
        db_path=db_path,
        config_path=Path(os.environ.get("GIVENERGY_CONFIG_PATH", str(effective_data / "property_config.json"))),
        tariff_config_path=Path(
            os.environ.get("GIVENERGY_TARIFF_CONFIG_PATH", str(effective_data / "tariff_config.json"))
        ),
        solar_forecast_config_path=Path(
            os.environ.get("GIVENERGY_SOLAR_FORECAST_CONFIG_PATH", str(effective_data / "solar_forecast_config.json"))
        ),
    )


async def _octopus_periodic_sync_loop(ctx: AppContext) -> None:
    while True:
        try:
            await ctx.sync_configured_octopus_rates()
        except Exception:
            pass
        await asyncio.sleep(6 * 3600)


async def _solar_forecast_periodic_sync_loop(ctx: AppContext) -> None:
    while True:
        try:
            await ctx.sync_solar_forecast()
        except Exception:
            pass
        await asyncio.sleep(2 * 3600)


def create_app(
    *,
    data_dir: Path | None = None,
    db_flush_interval_s: float | None = None,
    platform_adapter: PlatformAdapter | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application without import-time I/O."""
    paths = resolve_app_paths(data_dir)
    flush_s = (
        float(db_flush_interval_s)
        if db_flush_interval_s is not None
        else float(os.environ.get("GIVENERGY_DB_FLUSH_INTERVAL", "60.0"))
    )
    adapter = platform_adapter or get_platform_adapter()

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        ws_hub = WebSocketHub()
        property_config = load_config(paths.config_path)
        tariff_config = load_tariff_config(paths.tariff_config_path)
        tariff_cache = TariffRateCache(paths.db_path)
        solar_forecast_config = load_solar_forecast_config(paths.solar_forecast_config_path)
        solar_forecast_store = SolarForecastStore(paths.db_path)
        db = HistoricDatabase(db_path=paths.db_path, flush_interval_s=flush_s)
        poller = AsyncPlantPoller(
            db=db,
            config=property_config,
            broadcast_cb=ws_hub.broadcast,
            config_path=paths.config_path,
            tariff_config=tariff_config,
        )
        ctx = AppContext(
            app_version=APP_VERSION,
            paths=paths,
            db_flush_interval_s=flush_s,
            platform=adapter,
            ws_hub=ws_hub,
            property_config=property_config,
            tariff_config=tariff_config,
            tariff_cache=tariff_cache,
            solar_forecast_config=solar_forecast_config,
            solar_forecast_store=solar_forecast_store,
            db=db,
            poller=poller,
        )
        fastapi_app.state.ctx = ctx

        await poller.start()
        sync_task = asyncio.create_task(_octopus_periodic_sync_loop(ctx))
        solar_sync_task = asyncio.create_task(_solar_forecast_periodic_sync_loop(ctx))
        try:
            yield
        finally:
            sync_task.cancel()
            solar_sync_task.cancel()
            await poller.stop()

    fastapi_app = FastAPI(
        title="GivEnergy Local Control & Historic Telemetry Service",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    paths.static_dir.mkdir(parents=True, exist_ok=True)
    fastapi_app.mount("/static", StaticFiles(directory=str(paths.static_dir)), name="static")

    @fastapi_app.middleware("http")
    async def funnel_bearer_auth_middleware(request: Request, call_next):
        path = request.url.path
        if path in ("/api/auth/verify", "/manifest.json") or path.startswith("/static/icon-"):
            return await call_next(request)

        client_host = request.client.host if request.client else None
        require_auth = is_funnel_request(request.headers, client_host) or (
            os.environ.get("GIVENERGY_REQUIRE_AUTH", "").strip().lower() in ("1", "true", "yes")
        )
        if require_auth:
            ctx: AppContext = request.app.state.ctx
            if not is_authorized(ctx.property_config.api_token, request.headers, request.query_params, request.cookies):
                if path.startswith("/api/"):
                    return JSONResponse(
                        {"ok": False, "error": "Unauthorized: valid Bearer token required"},
                        status_code=401,
                    )
                return HTMLResponse(_FUNNEL_LOGIN_HTML, status_code=401)
        return await call_next(request)

    fastapi_app.include_router(telemetry.router)
    fastapi_app.include_router(tariffs.router)
    fastapi_app.include_router(solar.router)
    fastapi_app.include_router(system.router)
    fastapi_app.include_router(backup.router)

    return fastapi_app


app = create_app()
