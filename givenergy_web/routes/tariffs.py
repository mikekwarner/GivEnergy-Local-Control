"""UK Energy Tariff, Octopus API, Cost Summary, and What-If Comparison routes."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from givenergy_web.context import get_ctx
from givenergy_web.tariffs import (
    UK_GSP_REGIONS,
    UK_TARIFF_PRESETS,
    TariffConfig,
    TariffSideConfig,
    aggregate_cost_periods,
    calculate_daily_costs,
    calculate_intraday_costs,
    fetch_octopus_product_summary,
    fetch_octopus_products,
    get_current_rates,
    lookup_gsp_by_postcode,
    remove_tariff_history_entry,
    save_tariff_config,
    sync_octopus_tariff,
    sync_price_cap,
    upsert_tariff_history_entry,
)

router = APIRouter()


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
    effective_from: str | None = None


class TariffCheckRequest(TariffConfigRequest):
    days: int = Field(default=30, ge=0, le=36500)


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


class SavedComparisonTariffRequest(BaseModel):
    name: str
    default_rate_p: float
    export_rate_p: float | None = None
    standing_charge_p: float | None = None
    windows: list[dict[str, Any]] = Field(default_factory=list)


def _side_from_request(side_req: TariffSideRequest, fallback_label: str) -> TariffSideConfig:
    desc = side_req.description.strip()
    lbl = side_req.label.strip()
    if desc and lbl in ("", "Custom Tariff", "Custom Export"):
        lbl = desc
    return TariffSideConfig(
        mode=side_req.mode,
        preset_id=side_req.preset_id,
        label=lbl or fallback_label,
        description=desc,
        octopus_product_code=side_req.octopus_product_code.strip(),
        octopus_tariff_code=side_req.octopus_tariff_code.strip(),
        default_rate_p=float(side_req.default_rate_p),
        windows=side_req.windows,
    )


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


@router.get("/api/tariffs/presets")
async def api_tariff_presets():
    return JSONResponse({"regions": UK_GSP_REGIONS, "presets": UK_TARIFF_PRESETS})


@router.get("/api/tariffs/config")
async def api_get_tariff_config(request: Request):
    ctx = get_ctx(request)
    data = asdict(ctx.tariff_config)
    data["current_rates"] = await asyncio.to_thread(get_current_rates, ctx.tariff_config, ctx.tariff_cache)
    return JSONResponse(data)


@router.post("/api/tariffs/check")
async def api_check_tariff(req: TariffCheckRequest, request: Request):
    ctx = get_ctx(request)
    region = req.region.strip().upper()
    if region not in UK_GSP_REGIONS:
        region = "H"
    imp_side = _side_from_request(req.import_tariff, "Import Tariff")
    exp_side = _side_from_request(req.export_tariff, "Export Tariff")
    sc_p = float(req.standing_charge_p)

    if imp_side.mode == "octopus_api" and imp_side.octopus_product_code:
        try:
            res = await asyncio.to_thread(sync_octopus_tariff, ctx.tariff_cache, imp_side.octopus_product_code, region)
            imp_side.octopus_tariff_code = res["tariff_code"]
            imp_side.default_rate_p = float(res["current_rate_p"])
            if res.get("standing_charge_p") is not None:
                sc_p = float(res["standing_charge_p"])
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"Octopus import lookup failed: {exc}"}, status_code=502)

    if exp_side.mode == "octopus_api" and exp_side.octopus_product_code:
        try:
            res = await asyncio.to_thread(sync_octopus_tariff, ctx.tariff_cache, exp_side.octopus_product_code, region)
            exp_side.octopus_tariff_code = res["tariff_code"]
            exp_side.default_rate_p = float(res["current_rate_p"])
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"Octopus export lookup failed: {exc}"}, status_code=502)

    cand_cfg = TariffConfig(
        currency_symbol=req.currency_symbol.strip() or "£",
        sub_unit_symbol=req.sub_unit_symbol.strip() or "p",
        region=region,
        postcode=req.postcode.strip().upper(),
        standing_charge_p=sc_p,
        import_tariff=imp_side,
        export_tariff=exp_side,
        price_cap_rate_p=float(req.price_cap_rate_p) if req.price_cap_rate_p is not None else ctx.tariff_config.price_cap_rate_p,
        price_cap_standing_charge_p=float(req.price_cap_standing_charge_p) if req.price_cap_standing_charge_p is not None else ctx.tariff_config.price_cap_standing_charge_p,
    )

    def _run_check() -> dict[str, Any]:
        baseline = calculate_daily_costs(ctx.db, ctx.tariff_config, ctx.tariff_cache, req.days)["totals"]
        candidate = calculate_daily_costs(ctx.db, cand_cfg, ctx.tariff_cache, req.days)["totals"]
        today_str = time.strftime("%Y-%m-%d")
        today_cand = calculate_intraday_costs(ctx.db, cand_cfg, ctx.tariff_cache, today_str)["totals"]
        days_count = int(baseline.get("days_count", 0) or 0)
        diff_gbp = round(float(candidate.get("net_cost_gbp", 0.0) or 0.0) - float(baseline.get("net_cost_gbp", 0.0) or 0.0), 2)
        annual_est_diff = round((diff_gbp / days_count) * 365.0, 2) if days_count > 0 else 0.0
        return {
            "ok": True,
            "days": req.days,
            "days_count": days_count,
            "currency_symbol": cand_cfg.currency_symbol,
            "sub_unit_symbol": cand_cfg.sub_unit_symbol,
            "standing_charge_p": round(sc_p, 2),
            "import_label": imp_side.label,
            "export_label": exp_side.label,
            "baseline": baseline,
            "candidate": candidate,
            "today_candidate": today_cand,
            "diff_gbp": diff_gbp,
            "annual_est_diff_gbp": annual_est_diff,
        }

    result = await asyncio.to_thread(_run_check)
    return JSONResponse(result)


@router.post("/api/tariffs/config")
async def api_save_tariff_config(req: TariffConfigRequest, request: Request):
    ctx = get_ctx(request)
    tc = ctx.tariff_config
    region = req.region.strip().upper()
    if region not in UK_GSP_REGIONS:
        region = "H"

    effective_from = (req.effective_from or time.strftime("%Y-%m-%d")).strip()[:10]
    try:
        datetime.strptime(effective_from, "%Y-%m-%d")
    except ValueError:
        return JSONResponse({"ok": False, "error": "Invalid effective_from date (expected YYYY-MM-DD)"}, status_code=400)

    imp_side = _side_from_request(req.import_tariff, "Import Tariff")
    exp_side = _side_from_request(req.export_tariff, "Export Tariff")

    tc.currency_symbol = req.currency_symbol.strip() or "£"
    tc.sub_unit_symbol = req.sub_unit_symbol.strip() or "p"
    tc.region = region
    tc.postcode = req.postcode.strip().upper()
    if req.price_cap_rate_p is not None:
        tc.price_cap_rate_p = float(req.price_cap_rate_p)
    if req.price_cap_standing_charge_p is not None:
        tc.price_cap_standing_charge_p = float(req.price_cap_standing_charge_p)
    if req.price_cap_auto_sync is not None:
        tc.price_cap_auto_sync = bool(req.price_cap_auto_sync)
        if req.price_cap_auto_sync is False:
            tc.price_cap_source = "Manual Override"
            tc.price_cap_updated_at = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    upsert_tariff_history_entry(
        tc,
        effective_from=effective_from,
        standing_charge_p=float(req.standing_charge_p),
        import_tariff=imp_side,
        export_tariff=exp_side,
    )
    save_tariff_config(ctx.paths.tariff_config_path, tc)
    sync_info: dict[str, Any] = {}
    sync_error: str | None = None
    try:
        sync_info = await ctx.sync_configured_octopus_rates()
    except Exception as exc:
        sync_error = str(exc)

    resp = asdict(tc)
    resp["current_rates"] = await asyncio.to_thread(get_current_rates, tc, ctx.tariff_cache)
    return JSONResponse({"ok": True, "config": resp, "synced": sync_info, "sync_error": sync_error})


@router.delete("/api/tariffs/history/{entry_id}")
async def api_delete_tariff_history(entry_id: str, request: Request):
    ctx = get_ctx(request)
    tc = ctx.tariff_config
    if tc.tariff_history:
        ordered = sorted(tc.tariff_history, key=lambda e: e.effective_from)
        if ordered[0].id == entry_id:
            return JSONResponse({"ok": False, "error": "Cannot remove the original/first tariff"}, status_code=400)
    if not remove_tariff_history_entry(tc, entry_id):
        return JSONResponse({"ok": False, "error": "Tariff history entry not found"}, status_code=404)
    save_tariff_config(ctx.paths.tariff_config_path, tc)
    resp = asdict(tc)
    resp["current_rates"] = await asyncio.to_thread(get_current_rates, tc, ctx.tariff_cache)
    return JSONResponse({"ok": True, "config": resp})


@router.post("/api/tariffs/price-cap/sync")
async def api_sync_price_cap(request: Request):
    ctx = get_ctx(request)
    try:
        res = await asyncio.to_thread(sync_price_cap, ctx.tariff_config)
        save_tariff_config(ctx.paths.tariff_config_path, ctx.tariff_config)
        return JSONResponse({"ok": True, **res})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@router.get("/api/tariffs/octopus/products")
async def api_octopus_products(refresh: bool = Query(default=False)):
    try:
        prods = await asyncio.to_thread(fetch_octopus_products, refresh)
        return JSONResponse({"ok": True, **prods})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@router.get("/api/tariffs/octopus/product-rates")
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


@router.get("/api/tariffs/octopus/lookup-postcode")
async def api_octopus_lookup_postcode(postcode: str = Query(..., min_length=2)):
    try:
        region = await asyncio.to_thread(lookup_gsp_by_postcode, postcode)
        return JSONResponse({"ok": True, "region": region, "region_name": UK_GSP_REGIONS.get(region, region)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/tariffs/octopus/sync")
async def api_octopus_sync(request: Request):
    ctx = get_ctx(request)
    try:
        synced = await ctx.sync_configured_octopus_rates()
        rates = await asyncio.to_thread(get_current_rates, ctx.tariff_config, ctx.tariff_cache)
        return JSONResponse({"ok": True, "synced": synced, "current_rates": rates, "last_sync": ctx.tariff_config.last_octopus_sync})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@router.get("/api/costs/summary")
async def api_costs_summary(
    request: Request,
    days: int = Query(default=30, ge=0, le=36500),
    period: str = Query(default="days", pattern="^(days|weeks|months|years|all)$"),
):
    ctx = get_ctx(request)
    tc = ctx.tariff_config
    current_rates = await asyncio.to_thread(get_current_rates, tc, ctx.tariff_cache)
    daily = await asyncio.to_thread(calculate_daily_costs, ctx.db, tc, ctx.tariff_cache, days)
    daily["period"] = period
    daily["rows"] = aggregate_cost_periods(daily["days"], period)
    today_str = time.strftime("%Y-%m-%d")
    today_row = next((d for d in daily["days"] if d["date_str"] == today_str), None)
    if today_row is None:
        intraday = await asyncio.to_thread(calculate_intraday_costs, ctx.db, tc, ctx.tariff_cache, today_str)
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
        "currency_symbol": tc.currency_symbol,
        "sub_unit_symbol": tc.sub_unit_symbol,
        "region": tc.region,
        "current_rates": current_rates,
        "today": today_totals,
        "daily": daily,
    })


@router.get("/api/costs/intraday")
async def api_costs_intraday(
    request: Request,
    date: str | None = Query(default=None, description="YYYY-MM-DD"),
):
    ctx = get_ctx(request)
    data = await asyncio.to_thread(calculate_intraday_costs, ctx.db, ctx.tariff_config, ctx.tariff_cache, date)
    data["currency_symbol"] = ctx.tariff_config.currency_symbol
    data["sub_unit_symbol"] = ctx.tariff_config.sub_unit_symbol
    return JSONResponse(data)


@router.post("/api/tariffs/compare")
async def api_tariffs_compare(req: TariffCompareRequest, request: Request):
    ctx = get_ctx(request)
    tc = ctx.tariff_config
    imp_presets = {p["id"]: p for p in UK_TARIFF_PRESETS.get("import", [])}
    exp_presets = {p["id"]: p for p in UK_TARIFF_PRESETS.get("export", [])}

    def _run_compare() -> dict[str, Any]:
        baseline = calculate_daily_costs(ctx.db, tc, ctx.tariff_cache, req.days)["totals"]
        days_count = int(baseline.get("days_count", 0) or 0)
        base_net = float(baseline.get("net_cost_gbp", 0.0) or 0.0)

        candidates = req.candidates
        if not candidates:
            hidden = set(tc.hidden_comparison_presets)
            candidates = [
                CompareTariffCandidate(
                    name=f"{p.get('supplier', '')} — {p.get('name', '')}".strip(" —"),
                    import_preset_id=p["id"],
                )
                for p in UK_TARIFF_PRESETS.get("import", [])
                if p["id"] not in hidden
            ]
            for ct in tc.comparison_tariffs:
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
                imp_side = tc.import_tariff

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
                exp_side = tc.export_tariff

            sc_p = (
                float(cand.standing_charge_p)
                if cand.standing_charge_p is not None
                else float(imp_p.get("standing_charge_p", tc.standing_charge_p))
                if imp_p is not None
                else float(tc.standing_charge_p)
            )

            cand_cfg = TariffConfig(
                currency_symbol=tc.currency_symbol,
                sub_unit_symbol=tc.sub_unit_symbol,
                region=tc.region,
                postcode=tc.postcode,
                standing_charge_p=sc_p,
                import_tariff=imp_side,
                export_tariff=exp_side,
                price_cap_rate_p=tc.price_cap_rate_p,
                price_cap_standing_charge_p=tc.price_cap_standing_charge_p,
            )

            totals = calculate_daily_costs(ctx.db, cand_cfg, ctx.tariff_cache, req.days)["totals"]
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
            "currency_symbol": tc.currency_symbol,
            "days": req.days,
            "days_count": days_count,
            "current": {
                "name": f"{tc.import_tariff.label} / {tc.export_tariff.label}",
                "import_rate_summary": _format_side_summary(tc.import_tariff),
                "export_rate_summary": _format_side_summary(tc.export_tariff),
                "standing_charge_p": round(tc.standing_charge_p, 2),
                **baseline,
            },
            "comparisons": results,
        }

    payload = await asyncio.to_thread(_run_compare)
    return JSONResponse(payload)


@router.post("/api/tariffs/compare/saved")
async def api_save_comparison_tariff(req: SavedComparisonTariffRequest, request: Request):
    ctx = get_ctx(request)
    tc = ctx.tariff_config
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
    tc.comparison_tariffs.append(item)
    save_tariff_config(ctx.paths.tariff_config_path, tc)
    return JSONResponse(
        {
            "ok": True,
            "item": item,
            "comparison_tariffs": tc.comparison_tariffs,
            "hidden_comparison_presets": tc.hidden_comparison_presets,
        }
    )


@router.delete("/api/tariffs/compare/saved/{item_id}")
async def api_delete_comparison_tariff(item_id: str, request: Request):
    ctx = get_ctx(request)
    tc = ctx.tariff_config
    before = len(tc.comparison_tariffs)
    tc.comparison_tariffs = [t for t in tc.comparison_tariffs if t.get("id") != item_id]
    if len(tc.comparison_tariffs) == before:
        preset_ids = {p["id"] for p in UK_TARIFF_PRESETS.get("import", [])}
        if item_id in preset_ids and item_id not in tc.hidden_comparison_presets:
            tc.hidden_comparison_presets.append(item_id)
    save_tariff_config(ctx.paths.tariff_config_path, tc)
    return JSONResponse(
        {
            "ok": True,
            "comparison_tariffs": tc.comparison_tariffs,
            "hidden_comparison_presets": tc.hidden_comparison_presets,
        }
    )


@router.post("/api/tariffs/compare/reset-presets")
async def api_reset_comparison_presets(request: Request):
    ctx = get_ctx(request)
    tc = ctx.tariff_config
    tc.hidden_comparison_presets = []
    save_tariff_config(ctx.paths.tariff_config_path, tc)
    return JSONResponse(
        {
            "ok": True,
            "comparison_tariffs": tc.comparison_tariffs,
            "hidden_comparison_presets": tc.hidden_comparison_presets,
        }
    )
