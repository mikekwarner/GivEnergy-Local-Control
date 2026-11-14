"""FastAPI Web Server, REST API & Real-Time WebSocket Hub for GivEnergy Local Control on Raspberry Pi 5."""

from __future__ import annotations

import asyncio
import csv
import hmac
import io
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import threading
import time
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

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_DB_PATH = Path(os.environ.get("GIVENERGY_DB_PATH", str(BASE_DIR.parent / "data" / "givenergy_history.db")))
DEFAULT_CONFIG_PATH = Path(
    os.environ.get("GIVENERGY_CONFIG_PATH", str(DEFAULT_DB_PATH.parent / "property_config.json"))
)
DB_FLUSH_INTERVAL_S = float(os.environ.get("GIVENERGY_DB_FLUSH_INTERVAL", "60.0"))
property_config = load_config(DEFAULT_CONFIG_PATH)


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
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await poller.start()
    try:
        yield
    finally:
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
async def api_history_daily(limit: int = Query(default=90, ge=1, le=1000)):
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
    )
    save_config(DEFAULT_CONFIG_PATH, cfg)
    await poller.apply_config(cfg)
    resp_cfg = asdict(cfg)
    resp_cfg.pop("api_token", None)
    return JSONResponse({"ok": True, "config": resp_cfg})


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
