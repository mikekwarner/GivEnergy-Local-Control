"""Telemetry, history, property config, hardware control, and WebSocket routes."""

from __future__ import annotations

import asyncio
import csv
import io
import os
import secrets
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from givenergy_web.config import PropertyConfig, save_config, scan_lan_for_devices
from givenergy_web.context import get_ctx, is_authorized, is_funnel_request

router = APIRouter()


class ControlRequest(BaseModel):
    target: str = Field(..., description="'inverter', 'evc', or 'system'")
    action: str = Field(..., description="Action identifier")
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(..., description="Human-readable description of the command")


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


@router.get("/", response_class=FileResponse)
async def serve_index(request: Request):
    ctx = get_ctx(request)
    return FileResponse(str(ctx.paths.static_dir / "index.html"))


@router.get("/manifest.json", response_class=FileResponse)
async def serve_manifest(request: Request):
    ctx = get_ctx(request)
    return FileResponse(str(ctx.paths.static_dir / "manifest.json"))


@router.get("/api/snapshot")
async def api_snapshot(request: Request):
    ctx = get_ctx(request)
    payload = ctx.poller.get_full_payload()
    payload["system"] = ctx.read_system_stats()
    return JSONResponse(payload)


@router.get("/api/history/intraday")
async def api_history_intraday(
    request: Request,
    date: str | None = Query(default=None, description="YYYY-MM-DD"),
):
    ctx = get_ctx(request)
    series = await asyncio.to_thread(ctx.db.get_intraday_series, date)
    return JSONResponse({"date": date, "count": len(series), "series": series})


@router.get("/api/history/range")
async def api_history_range(
    request: Request,
    days: int = Query(default=7, ge=1, le=365),
    end_date: str | None = Query(default=None, description="YYYY-MM-DD"),
):
    ctx = get_ctx(request)
    series = await asyncio.to_thread(ctx.db.get_range_series, days, end_date)
    return JSONResponse({"days": days, "end_date": end_date, "count": len(series), "series": series})


@router.get("/api/history/daily")
async def api_history_daily(
    request: Request,
    limit: int = Query(default=3650, ge=1, le=10000),
):
    ctx = get_ctx(request)
    days = await asyncio.to_thread(ctx.db.get_daily_summaries, limit)
    return JSONResponse({"count": len(days), "days": days})


@router.get("/api/history/export.csv")
async def api_export_csv(
    request: Request,
    mode: str = Query(default="daily", description="'daily' or 'intraday'"),
    date: str | None = Query(default=None, description="YYYY-MM-DD for intraday export"),
):
    ctx = get_ctx(request)
    buf = io.StringIO()
    if mode == "intraday":
        rows = await asyncio.to_thread(ctx.db.get_intraday_series, date)
        filename = f"givenergy_intraday_{date or 'today'}.csv"
    else:
        rows = await asyncio.to_thread(ctx.db.get_daily_summaries, 365)
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


@router.get("/api/config")
async def api_get_config(request: Request):
    ctx = get_ctx(request)
    cfg_dict = asdict(ctx.poller.config)
    cfg_dict.pop("api_token", None)
    cfg_dict["api_token_configured"] = bool(ctx.poller.config.api_token)
    return JSONResponse(cfg_dict)


@router.post("/api/config")
async def api_save_config(req: PropertyConfigRequest, request: Request):
    ctx = get_ctx(request)
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
        api_token=ctx.poller.config.api_token or secrets.token_urlsafe(32),
        evc_charging_mode=ctx.poller.config.evc_charging_mode,
        evc_max_battery_discharge_w=ctx.poller.config.evc_max_battery_discharge_w,
        evc_solar_export_split_pct=ctx.poller.config.evc_solar_export_split_pct,
        evc_user_charge_limit_a=ctx.poller.config.evc_user_charge_limit_a,
        evc_schedule_enabled=ctx.poller.config.evc_schedule_enabled,
        evc_schedule_use_tariff=ctx.poller.config.evc_schedule_use_tariff,
        evc_schedule_start=ctx.poller.config.evc_schedule_start,
        evc_schedule_end=ctx.poller.config.evc_schedule_end,
        low_soc_discharge_guard=ctx.poller.config.low_soc_discharge_guard,
    )
    save_config(ctx.paths.config_path, cfg)
    ctx.property_config = cfg
    await ctx.poller.apply_config(cfg)
    resp_cfg = asdict(cfg)
    resp_cfg.pop("api_token", None)
    return JSONResponse({"ok": True, "config": resp_cfg})


@router.post("/api/scan")
async def api_scan_network():
    results = await scan_lan_for_devices()
    return JSONResponse(results)


@router.post("/api/control")
async def api_control(req: ControlRequest, request: Request):
    ctx = get_ctx(request)
    if req.target not in ("inverter", "evc", "system"):
        return JSONResponse({"ok": False, "error": "Invalid target"}, status_code=400)
    await ctx.poller.enqueue_command(
        target=req.target,
        action=req.action,
        params=req.params,
        description=req.description,
    )
    return JSONResponse({"ok": True, "queued": req.description})


@router.post("/api/poll-now")
async def api_poll_now(request: Request):
    ctx = get_ctx(request)
    ctx.poller.trigger_poll_now()
    return JSONResponse({"ok": True})


@router.post("/api/flush-db")
async def api_flush_db(request: Request):
    ctx = get_ctx(request)
    await asyncio.to_thread(ctx.db.flush)
    return JSONResponse({"ok": True, "stats": ctx.db.get_db_stats()})


@router.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    ctx = get_ctx(ws)
    client_host = ws.client.host if ws.client else None
    require_auth = is_funnel_request(ws.headers, client_host) or (
        os.environ.get("GIVENERGY_REQUIRE_AUTH", "").strip().lower() in ("1", "true", "yes")
    )
    if require_auth and not is_authorized(ctx.property_config.api_token, ws.headers, ws.query_params, ws.cookies):
        await ws.close(code=1008)
        return
    await ctx.ws_hub.connect(ws)
    try:
        initial = ctx.poller.get_full_payload()
        initial["system"] = ctx.read_system_stats()
        await ws.send_json(initial)
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
            elif msg == "poll":
                ctx.poller.trigger_poll_now()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await ctx.ws_hub.disconnect(ws)
