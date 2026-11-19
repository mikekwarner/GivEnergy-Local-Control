"""Open-Meteo Solar PV Forecasting routes."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from givenergy_web.context import get_ctx
from givenergy_web.solar_forecast import SolarForecastConfig, save_solar_forecast_config

router = APIRouter()


class SolarForecastConfigRequest(BaseModel):
    enabled: bool = True
    latitude: float | None = None
    longitude: float | None = None
    capacity_kwp: float = 5.0
    tilt_deg: float = 35.0
    azimuth_deg: float = 180.0
    array2_enabled: bool = False
    array2_capacity_kwp: float = 0.0
    array2_tilt_deg: float = 35.0
    array2_azimuth_deg: float = 270.0
    base_efficiency: float = 0.85
    forecast_days: int = 7


@router.get("/api/solar-forecast/config")
async def api_get_solar_forecast_config(request: Request):
    ctx = get_ctx(request)
    data = asdict(ctx.solar_forecast_config)
    data["configured"] = ctx.solar_forecast_config.is_configured()
    return JSONResponse(data)


@router.post("/api/solar-forecast/config")
async def api_save_solar_forecast_config(req: SolarForecastConfigRequest, request: Request):
    ctx = get_ctx(request)
    sc = ctx.solar_forecast_config
    if req.enabled:
        if req.latitude is None or not (-90.0 <= req.latitude <= 90.0):
            raise HTTPException(status_code=400, detail="Latitude must be between -90 and 90.")
        if req.longitude is None or not (-180.0 <= req.longitude <= 180.0):
            raise HTTPException(status_code=400, detail="Longitude must be between -180 and 180.")
        if req.capacity_kwp <= 0 or req.capacity_kwp > 500:
            raise HTTPException(status_code=400, detail="Array capacity (kWp) must be greater than 0.")

    geom_changed = (
        sc.latitude != req.latitude
        or sc.longitude != req.longitude
        or sc.capacity_kwp != req.capacity_kwp
        or sc.tilt_deg != req.tilt_deg
        or sc.azimuth_deg != req.azimuth_deg
        or sc.array2_enabled != req.array2_enabled
        or sc.array2_capacity_kwp != req.array2_capacity_kwp
        or sc.array2_tilt_deg != req.array2_tilt_deg
        or sc.array2_azimuth_deg != req.array2_azimuth_deg
    )

    ctx.solar_forecast_config = SolarForecastConfig(
        enabled=bool(req.enabled),
        latitude=round(float(req.latitude), 6) if req.latitude is not None else None,
        longitude=round(float(req.longitude), 6) if req.longitude is not None else None,
        capacity_kwp=max(0.1, float(req.capacity_kwp)),
        tilt_deg=max(0.0, min(90.0, float(req.tilt_deg))),
        azimuth_deg=float(req.azimuth_deg) % 360.0,
        array2_enabled=bool(req.array2_enabled),
        array2_capacity_kwp=max(0.0, float(req.array2_capacity_kwp)),
        array2_tilt_deg=max(0.0, min(90.0, float(req.array2_tilt_deg))),
        array2_azimuth_deg=float(req.array2_azimuth_deg) % 360.0,
        base_efficiency=max(0.25, min(1.25, float(req.base_efficiency))),
        forecast_days=max(1, min(14, int(req.forecast_days))),
        last_sync_at=sc.last_sync_at,
        last_sync_error="",
    )
    save_solar_forecast_config(ctx.paths.solar_forecast_config_path, ctx.solar_forecast_config)
    if geom_changed:
        await asyncio.to_thread(ctx.solar_forecast_store.clear_forecasts)

    sync_res = await ctx.sync_solar_forecast()
    resp = asdict(ctx.solar_forecast_config)
    resp["configured"] = ctx.solar_forecast_config.is_configured()
    resp["sync"] = sync_res
    return JSONResponse(resp)


@router.post("/api/solar-forecast/sync")
async def api_sync_solar_forecast(request: Request):
    ctx = get_ctx(request)
    res = await ctx.sync_solar_forecast()
    if not res.get("ok") and not res.get("skipped"):
        raise HTTPException(status_code=502, detail=res.get("error", "Open-Meteo sync failed"))
    return JSONResponse(res)


@router.get("/api/solar-forecast/data")
async def api_get_solar_forecast_data(
    request: Request,
    past_days: int = Query(default=2, ge=0, le=7),
    forecast_days: int = Query(default=7, ge=1, le=14),
):
    ctx = get_ctx(request)
    data = await asyncio.to_thread(
        ctx.solar_forecast_store.get_forecast_and_actuals,
        ctx.solar_forecast_config,
        past_days,
        forecast_days,
    )
    data["config"] = asdict(ctx.solar_forecast_config)
    return JSONResponse(data)
