"""Authentication, QR pairing, host system stats, OS updates, and app updates routes."""

from __future__ import annotations

import asyncio
import hmac
import io
import json
import secrets
import socket

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from givenergy_web.config import _detect_local_ipv4, save_config
from givenergy_web.context import get_ctx, is_funnel_request
from givenergy_web.updater import check_github_update, get_app_update_status, start_app_update

router = APIRouter()


class TokenVerifyRequest(BaseModel):
    token: str = ""


@router.post("/api/auth/verify")
async def api_auth_verify(req: TokenVerifyRequest, request: Request):
    ctx = get_ctx(request)
    expected = (ctx.poller.config.api_token or "").strip()
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


@router.get("/api/auth/pairing")
async def api_auth_pairing(request: Request):
    ctx = get_ctx(request)
    client_host = request.client.host if request.client else None
    if is_funnel_request(request.headers, client_host):
        return JSONResponse({"ok": False, "error": "Pairing info is only available on the local network"}, status_code=403)
    ts_info = await asyncio.to_thread(ctx.platform.read_tailscale_funnel_info)
    return JSONResponse({
        "ok": True,
        "hostname": socket.gethostname(),
        "api_token": ctx.poller.config.api_token,
        **ts_info,
    })


@router.post("/api/auth/regenerate-token")
async def api_auth_regenerate_token(request: Request):
    ctx = get_ctx(request)
    client_host = request.client.host if request.client else None
    if is_funnel_request(request.headers, client_host):
        return JSONResponse({"ok": False, "error": "Token regeneration is only available on the local network"}, status_code=403)
    new_token = secrets.token_urlsafe(32)
    ctx.poller.config.api_token = new_token
    ctx.property_config.api_token = new_token
    save_config(ctx.paths.config_path, ctx.poller.config)
    return JSONResponse({"ok": True, "api_token": new_token})


@router.get("/api/auth/pairing/qr.svg")
async def api_auth_pairing_qr(request: Request):
    ctx = get_ctx(request)
    client_host = request.client.host if request.client else None
    if is_funnel_request(request.headers, client_host):
        return JSONResponse({"ok": False, "error": "QR pairing is only available on the local network"}, status_code=403)
    import qrcode
    import qrcode.image.svg

    ts_info = await asyncio.to_thread(ctx.platform.read_tailscale_funnel_info)
    lan_ip = await asyncio.to_thread(_detect_local_ipv4)
    payload = json.dumps(
        {
            "url": ts_info.get("funnel_url") or "",
            "lan_url": f"http://{lan_ip}" if lan_ip else "",
            "token": ctx.poller.config.api_token,
            "name": ctx.poller.config.property_name,
        },
        separators=(",", ":"),
    )
    img = qrcode.make(payload, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.get("/api/system/stats")
async def api_system_stats(request: Request):
    ctx = get_ctx(request)
    stats = await asyncio.to_thread(ctx.read_system_stats)
    return JSONResponse(stats)


@router.post("/api/system/reboot")
async def api_system_reboot(request: Request):
    ctx = get_ctx(request)
    ok, msg = await asyncio.to_thread(ctx.platform.run_power_action, "reboot")
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 500)


@router.post("/api/system/shutdown")
async def api_system_shutdown(request: Request):
    ctx = get_ctx(request)
    ok, msg = await asyncio.to_thread(ctx.platform.run_power_action, "poweroff")
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 500)


@router.post("/api/system/update")
async def api_system_update(request: Request):
    ctx = get_ctx(request)
    return JSONResponse(ctx.platform.start_system_updates())


@router.get("/api/system/update/status")
async def api_system_update_status(request: Request):
    ctx = get_ctx(request)
    return JSONResponse(ctx.platform.get_system_update_status())


@router.get("/api/system/app-update/check")
async def api_system_app_update_check(request: Request):
    ctx = get_ctx(request)
    info = await asyncio.to_thread(check_github_update, ctx.app_version)
    return JSONResponse(info, status_code=200 if info.get("ok") else 502)


@router.post("/api/system/app-update")
async def api_system_app_update(request: Request):
    ctx = get_ctx(request)
    res = start_app_update(
        ctx.paths.base_dir,
        ctx.app_version,
        restart_cb=ctx.platform.trigger_service_restart,
    )
    return JSONResponse(res)


@router.get("/api/system/app-update/status")
async def api_system_app_update_status():
    return JSONResponse(get_app_update_status())
