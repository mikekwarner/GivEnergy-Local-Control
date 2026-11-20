"""Local Windows Service Installer, IIS Conflict Handler & Settings Uninstaller."""

from __future__ import annotations

import ctypes
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from givenergy_web import __version__ as APP_VERSION

SERVICE_NAME = "GivEnergyLocalControl"
DISPLAY_NAME = "GivEnergy Local Control"
SERVICE_DESC = "Local, cloud-free monitoring, control, and historic telemetry service for GivEnergy Inverters & EV Chargers."
FIREWALL_RULE_NAME = "GivEnergy Local Control"
UNINSTALL_REG_SUBKEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\GivEnergyLocalControl"

LogFn = Callable[[str], None]


def _default_log(msg: str) -> None:
    print(msg, flush=True)


def get_install_dir() -> Path:
    return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GivEnergy Local Control"


def get_data_root() -> Path:
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "GivEnergyLocalControl"


def is_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_port_in_use(port: int) -> bool:
    for host in ("127.0.0.1", "0.0.0.0"):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.4)
        try:
            if s.connect_ex((host if host != "0.0.0.0" else "127.0.0.1", int(port))) == 0:
                return True
        except Exception:
            pass
        finally:
            s.close()
    return False


def detect_iis_status() -> dict[str, Any]:
    """Detect Windows IIS (W3SVC), existing GivEnergyLocalControl service, and port 80 usage."""
    iis_installed = False
    iis_running = False
    giv_running = False
    giv_port: int | None = None
    if sys.platform == "win32":
        try:
            proc = subprocess.run(["sc.exe", "query", "W3SVC"], capture_output=True, text=True, timeout=4)
            if proc.returncode == 0:
                iis_installed = True
                iis_running = "RUNNING" in (proc.stdout or "").upper()
        except Exception:
            pass
        try:
            proc_g = subprocess.run(["sc.exe", "query", SERVICE_NAME], capture_output=True, text=True, timeout=4)
            if proc_g.returncode == 0:
                giv_running = "RUNNING" in (proc_g.stdout or "").upper() or "START_PENDING" in (proc_g.stdout or "").upper()
        except Exception:
            pass
        cfg_file = get_data_root() / "service_config.json"
        if cfg_file.exists():
            try:
                giv_port = int(json.loads(cfg_file.read_text(encoding="utf-8")).get("port", 80))
            except Exception:
                pass
    return {
        "iis_installed": iis_installed,
        "iis_running": iis_running,
        "givenergy_running": giv_running,
        "givenergy_port": giv_port,
        "port_80_in_use": is_port_in_use(80),
    }


def _run_cmd(cmd: list[str], *, check: bool = False) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {err or out or proc.returncode}")
    return proc.returncode, out, err


def _wait_service_stopped(name: str, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        rc, out, _ = _run_cmd(["sc.exe", "query", name])
        if rc != 0 or "STOPPED" in out.upper():
            return
        time.sleep(0.4)
    if name == SERVICE_NAME:
        for proc_name in ("GivEnergyService.exe", "GivEnergyLocalControlService.exe"):
            _run_cmd(["taskkill.exe", "/F", "/IM", proc_name])
        time.sleep(0.6)


def _wait_service_running(name: str, timeout_s: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        rc, out, _ = _run_cmd(["sc.exe", "query", name])
        if rc == 0 and "RUNNING" in out.upper():
            return True
        if rc == 0 and "STOPPED" in out.upper():
            time.sleep(0.4)
            continue
        time.sleep(0.5)
    return False


def backup_from_windows(dest_zip: Path, *, port: int | None = None, log_cb: LogFn = _default_log) -> Path:
    """Export a full backup .zip from the local Windows service (HTTP API first, data dir fallback)."""
    dest_zip = Path(dest_zip)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    data_root = get_data_root()
    if port is None:
        cfg_file = data_root / "service_config.json"
        port = 80
        if cfg_file.exists():
            try:
                port = int(json.loads(cfg_file.read_text(encoding="utf-8")).get("port", 80))
            except Exception:
                pass

    export_url = f"http://127.0.0.1:{port}/api/backup/export?include_config=true&include_history=true"
    try:
        log_cb(f"Requesting live backup from {export_url} ...")
        req = urllib.request.Request(export_url, headers={"User-Agent": "GivEnergy-Windows-Uninstaller/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read()
        if payload and zipfile.is_zipfile(io.BytesIO(payload)):
            dest_zip.write_bytes(payload)
            log_cb(f"[OK] Live backup saved to {dest_zip} ({len(payload) // 1024} KB).")
            return dest_zip
    except Exception as exc:
        log_cb(f"Live HTTP backup unavailable ({exc}); copying data files directly...")

    data_dir = data_root / "data"
    candidates = [
        "property_config.json",
        "tariff_config.json",
        "solar_forecast_config.json",
        "givenergy_history.db",
    ]
    found = [f for f in candidates if (data_dir / f).is_file()]
    if not found:
        raise RuntimeError(f"No configuration or database files found in {data_dir}.")

    manifest = {
        "format_version": 1,
        "source": "windows_uninstaller_fallback",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": found,
    }
    with zipfile.ZipFile(dest_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for fname in found:
            zf.write(data_dir / fname, arcname=fname)
    log_cb(f"[OK] Backup saved to {dest_zip} ({len(found)} file(s)).")
    return dest_zip


def install_windows_service(
    *,
    port: int = 80,
    replace_iis: bool = False,
    add_firewall_rule: bool = True,
    wipe_existing_data: bool = False,
    tariff_config_dict: dict[str, Any] | None = None,
    solar_forecast_config_dict: dict[str, Any] | None = None,
    log_cb: LogFn = _default_log,
    progress_cb: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    """Install GivEnergy Local Control as a Windows Service and register it in Windows Settings."""
    if not is_admin():
        raise PermissionError("Administrator privileges are required to install the Windows Service.")

    def _prog(pct: int) -> None:
        if progress_cb:
            progress_cb(pct)

    install_dir = get_install_dir()
    data_root = get_data_root()
    data_dir = data_root / "data"
    log_dir = data_root / "logs"

    # 1. Stop existing service if present
    _prog(10)
    log_cb("[1/5] Checking existing Windows Service & port configuration...")
    rc, _, _ = _run_cmd(["sc.exe", "query", SERVICE_NAME])
    if rc == 0:
        log_cb(f"  Stopping existing {SERVICE_NAME} service...")
        _run_cmd(["sc.exe", "stop", SERVICE_NAME])
        _wait_service_stopped(SERVICE_NAME)

    if replace_iis:
        log_cb("  Stopping and disabling IIS (W3SVC) to free port 80...")
        _run_cmd(["sc.exe", "stop", "W3SVC"])
        _wait_service_stopped("W3SVC")
        _run_cmd(["sc.exe", "config", "W3SVC", "start=", "disabled"])
        port = 80

    # 2. Prepare directories & data
    _prog(30)
    log_cb(f"[2/5] Preparing install ({install_dir}) and data ({data_root}) directories...")
    install_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    if wipe_existing_data:
        log_cb("  Wiping existing telemetry database and property configuration...")
        for f in data_dir.glob("givenergy_history.db*"):
            f.unlink(missing_ok=True)
        (data_dir / "property_config.json").unlink(missing_ok=True)

    service_cfg = {
        "host": "0.0.0.0",
        "port": int(port),
        "data_dir": str(data_dir),
        "flush_interval_s": 15.0,
    }
    (data_root / "service_config.json").write_text(json.dumps(service_cfg, indent=2), encoding="utf-8")

    if tariff_config_dict is not None:
        (data_dir / "tariff_config.json").write_text(json.dumps(tariff_config_dict, indent=2), encoding="utf-8")
        log_cb("  [OK] Pre-configured Energy Tariff written.")
    if solar_forecast_config_dict is not None:
        (data_dir / "solar_forecast_config.json").write_text(
            json.dumps(solar_forecast_config_dict, indent=2), encoding="utf-8"
        )
        log_cb("  [OK] Pre-configured Solar Forecast written.")

    # 3. Install service binary
    _prog(55)
    log_cb("[3/5] Installing service executable...")
    is_frozen = bool(getattr(sys, "frozen", False))
    meipass = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    bundled_svc_dir = meipass / "GivEnergyService"
    if not bundled_svc_dir.is_dir():
        bundled_svc_dir = Path(__file__).resolve().parent / "build" / "service_dist" / "GivEnergyService"

    if is_frozen or bundled_svc_dir.is_dir():
        svc_dest_dir = install_dir / "service"
        if svc_dest_dir.exists():
            shutil.rmtree(svc_dest_dir)
        shutil.copytree(bundled_svc_dir, svc_dest_dir)
        svc_exe = svc_dest_dir / "GivEnergyService.exe"
        bin_path = f'"{svc_exe}" --service'

        uninst_exe = install_dir / "GivEnergyLocalControlUninstaller.exe"
        if is_frozen:
            shutil.copy2(sys.executable, uninst_exe)
            uninstall_cmd = f'"{uninst_exe}" --uninstall-windows'
            icon_path = str(uninst_exe)
        else:
            uninst_script = Path(__file__).resolve().parent / "wizard.py"
            uninstall_cmd = f'"{sys.executable}" "{uninst_script}" --uninstall-windows'
            icon_path = str(svc_exe)
    else:
        svc_script = Path(__file__).resolve().parent / "windows_service.py"
        uninst_script = Path(__file__).resolve().parent / "wizard.py"
        bin_path = f'"{sys.executable}" "{svc_script}"'
        uninstall_cmd = f'"{sys.executable}" "{uninst_script}" --uninstall-windows'
        icon_path = str(Path(__file__).resolve().parent / "givenergy_web" / "static" / "icon.ico")

    # 4. Register Windows Service & Firewall rule
    _prog(75)
    log_cb(f"[4/5] Registering Windows Service '{SERVICE_NAME}' on port {port}...")
    _run_cmd(["sc.exe", "delete", SERVICE_NAME])
    time.sleep(0.6)
    _run_cmd(
        [
            "sc.exe",
            "create",
            SERVICE_NAME,
            "binPath=",
            bin_path,
            "start=",
            "delayed-auto",
            "DisplayName=",
            DISPLAY_NAME,
        ],
        check=True,
    )
    _run_cmd(["sc.exe", "description", SERVICE_NAME, SERVICE_DESC])
    _run_cmd(
        [
            "sc.exe",
            "failure",
            SERVICE_NAME,
            "reset=",
            "86400",
            "actions=",
            "restart/5000/restart/5000/restart/5000",
        ]
    )

    if add_firewall_rule:
        _run_cmd(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE_NAME}"])
        _run_cmd(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                f"name={FIREWALL_RULE_NAME}",
                "dir=in",
                "action=allow",
                "protocol=TCP",
                f"localport={int(port)}",
            ]
        )
        log_cb(f"  [OK] Windows Firewall rule added for TCP port {port}.")

    # Register in Windows Settings -> Apps -> Installed apps
    import winreg

    with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_REG_SUBKEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, DISPLAY_NAME)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "GivEnergy Local Control")
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, icon_path)
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, uninstall_cmd)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
    log_cb("  [OK] Registered in Windows Settings -> Apps -> Installed apps.")

    # 5. Start Service
    _prog(90)
    log_cb("[5/5] Starting Windows Service...")
    _run_cmd(["sc.exe", "start", SERVICE_NAME], check=True)
    if not _wait_service_running(SERVICE_NAME, timeout_s=20.0):
        raise RuntimeError(f"Service {SERVICE_NAME} did not reach RUNNING state. Check {log_dir / 'service.log'}.")

    _prog(100)
    local_url = "http://localhost" if int(port) == 80 else f"http://localhost:{int(port)}"
    log_cb(f"[OK] Service '{SERVICE_NAME}' is RUNNING! Dashboard: {local_url}")
    return {"local_url": local_url, "port": int(port)}


def uninstall_windows_service(*, remove_data: bool = True, log_cb: LogFn = _default_log) -> None:
    """Stop and remove the Windows Service, Firewall rule, Installed Apps entry, and files."""
    if not is_admin():
        raise PermissionError("Administrator privileges are required to uninstall the Windows Service.")

    log_cb(f"[1/4] Stopping and removing Windows Service '{SERVICE_NAME}'...")
    _run_cmd(["sc.exe", "stop", SERVICE_NAME])
    _wait_service_stopped(SERVICE_NAME)
    _run_cmd(["sc.exe", "delete", SERVICE_NAME])

    log_cb("[2/4] Removing Windows Firewall rule...")
    _run_cmd(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE_NAME}"])

    log_cb("[3/4] Removing Windows Installed Apps registry entry...")
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_REG_SUBKEY)
    except FileNotFoundError:
        pass

    log_cb("[4/4] Cleaning up installed files...")
    data_root = get_data_root()
    if remove_data and data_root.exists():
        shutil.rmtree(data_root, ignore_errors=True)

    install_dir = get_install_dir()
    if install_dir.exists():
        try:
            shutil.rmtree(install_dir)
        except PermissionError:
            # Running executable is inside install_dir; schedule deferred deletion after exit
            subprocess.Popen(
                ["cmd.exe", "/c", f'ping 127.0.0.1 -n 3 >nul & rmdir /s /q "{install_dir}"'],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    log_cb("[OK] GivEnergy Local Control has been uninstalled from Windows.")
