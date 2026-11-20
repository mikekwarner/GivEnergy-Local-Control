#!/usr/bin/env python3
"""Local Linux Installer & Uninstaller for GivEnergy Local Control (Ubuntu / Debian / Raspberry Pi OS)."""

from __future__ import annotations

import argparse
import getpass
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

REMOTE_ROOT = Path("/opt/givenergy-web")
SERVICE_NAME = "givenergy-web.service"
SRC_ROOT = Path(__file__).resolve().parent

PIP_PACKAGES = [
    "givenergy-modbus>=2.13.0",
    "pymodbus>=3.10.0",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "websockets>=13.0",
    "qrcode>=8.0",
]

RAMLOG_CONF = """[Journal]
Storage=volatile
RuntimeMaxUse=32M
RuntimeMaxFileSize=8M
ForwardToSyslog=no
"""

SSDLOG_CONF = """[Journal]
Storage=persistent
Compress=yes
SystemMaxUse=128M
"""

SYSTEMD_UNIT_TEMPLATE = """[Unit]
Description=GivEnergy Local Control & Historic Telemetry Web Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={user}
WorkingDirectory=/opt/givenergy-web
Environment="GIVENERGY_DB_PATH=/opt/givenergy-web/data/givenergy_history.db"
Environment="GIVENERGY_DB_FLUSH_INTERVAL={flush_interval}"
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=/opt/givenergy-web/.venv/bin/uvicorn givenergy_web.server:app --host 0.0.0.0 --port {port} --no-access-log
Restart=always
RestartSec=5
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
"""


def _run(cmd: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, env=env)


def _is_port_in_use(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False
    finally:
        s.close()


def _detect_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _check_prerequisites() -> None:
    if sys.platform != "linux":
        raise RuntimeError(f"This installer is for Linux only (detected: {sys.platform}).")
    if sys.version_info < (3, 10):
        raise RuntimeError(f"Python 3.10+ is required (found {sys.version.split()[0]}).")
    if not shutil.which("systemctl"):
        raise RuntimeError("systemd (systemctl) is required.")
    if not shutil.which("apt-get"):
        raise RuntimeError("apt-get was not found (Debian / Ubuntu / Raspberry Pi OS required).")
    if os.geteuid() != 0:
        raise RuntimeError("Please run as root (e.g. 'sudo python3 install_linux.py').")


def _backup_local_data(dest_zip: Path, port: int = 80) -> Path:
    dest_zip = Path(dest_zip)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    export_url = f"http://127.0.0.1:{port}/api/backup/export?include_config=true&include_history=true"
    try:
        req = urllib.request.Request(export_url, headers={"User-Agent": "GivEnergy-Linux-Uninstaller/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read()
        if payload and zipfile.is_zipfile(io.BytesIO(payload)):
            dest_zip.write_bytes(payload)
            print(f"[OK] Live backup saved to {dest_zip} ({len(payload) // 1024} KB).")
            return dest_zip
    except Exception as exc:
        print(f"Live HTTP backup unavailable ({exc}); archiving {REMOTE_ROOT / 'data'} directly...")

    data_dir = REMOTE_ROOT / "data"
    candidates = ["property_config.json", "tariff_config.json", "solar_forecast_config.json", "givenergy_history.db"]
    found = [f for f in candidates if (data_dir / f).is_file()]
    if not found:
        raise RuntimeError(f"No backupable files found in {data_dir}.")

    manifest = {
        "format_version": 1,
        "source": "install_linux_uninstaller",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": found,
    }
    with zipfile.ZipFile(dest_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for fname in found:
            zf.write(data_dir / fname, arcname=fname)
    print(f"[OK] Backup saved to {dest_zip} ({len(found)} file(s)).")
    return dest_zip


def uninstall_local(args: argparse.Namespace) -> int:
    _check_prerequisites()
    if not args.yes:
        ans = input(
            f"\nAre you sure you want to uninstall GivEnergy Local Control?\n"
            f"This will stop {SERVICE_NAME} and delete {REMOTE_ROOT}. [y/N]: "
        ).strip().lower()
        if ans not in ("y", "yes"):
            print("Uninstall cancelled.")
            return 0

    data_dir = REMOTE_ROOT / "data"
    has_data = data_dir.is_dir() and any(data_dir.iterdir())
    if has_data and not args.no_backup:
        take_backup = bool(args.backup_path)
        if not take_backup:
            b_ans = input("\nTake a backup (.zip) of config & historic database first? [Y/n]: ").strip().lower()
            take_backup = b_ans in ("", "y", "yes")
        if take_backup:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dest = Path(args.backup_path) if args.backup_path else Path(f"givenergy-backup-linux-{stamp}.zip")
            try:
                _backup_local_data(dest, port=args.port)
            except Exception as exc:
                print(f"[ERROR] Backup failed: {exc}")
                cont = input("Continue uninstall WITHOUT backup? [y/N]: ").strip().lower()
                if cont not in ("y", "yes"):
                    print("Uninstall aborted.")
                    return 1

    print(f"[1/4] Stopping and disabling {SERVICE_NAME}...")
    _run(["systemctl", "stop", SERVICE_NAME], check=False)
    _run(["systemctl", "disable", SERVICE_NAME], check=False)

    print("[2/4] Removing systemd unit, sudoers rule, and journald config...")
    Path(f"/etc/systemd/system/{SERVICE_NAME}").unlink(missing_ok=True)
    Path("/etc/sudoers.d/givenergy-web-ops").unlink(missing_ok=True)
    for jconf in ("/etc/systemd/journald.conf.d/ramlog.conf", "/etc/systemd/journald.conf.d/ssdlog.conf"):
        if Path(jconf).exists():
            Path(jconf).unlink(missing_ok=True)
            _run(["systemctl", "restart", "systemd-journald"], check=False)
    _run(["systemctl", "daemon-reload"], check=False)

    if args.reset_tailscale and shutil.which("tailscale"):
        print("[3/4] Resetting Tailscale Funnel configuration...")
        _run(["tailscale", "funnel", "reset"], check=False)
    else:
        print("[3/4] Keeping Tailscale configuration untouched.")

    print(f"[4/4] Removing {REMOTE_ROOT}...")
    shutil.rmtree(REMOTE_ROOT, ignore_errors=True)
    print("[OK] GivEnergy Local Control has been uninstalled.")
    return 0


def install_local(args: argparse.Namespace) -> int:
    _check_prerequisites()
    pkg_src = SRC_ROOT / "givenergy_web"
    if not pkg_src.is_dir():
        raise RuntimeError(f"Could not find givenergy_web/ directory alongside {__file__}.")

    service_user = args.user or os.environ.get("SUDO_USER") or getpass.getuser()
    if service_user == "root" and not args.user and not args.yes:
        entered = input("Service Linux username [root]: ").strip()
        if entered:
            service_user = entered

    port = int(args.port)
    if port == 80 and _is_port_in_use(80):
        svc_active = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE_NAME]).returncode == 0
        if not svc_active and not args.yes:
            print("\n[WARN] Port 80 is currently in use by another web server.")
            alt = input("Enter HTTP port to use instead [8080] (or press Enter for 8080): ").strip()
            port = int(alt) if alt else 8080

    storage_mode = args.storage
    if not storage_mode:
        if args.yes:
            storage_mode = "ssd"
        else:
            ans = input("\nStorage mode: [1] SSD / NVMe / Server (15s flush, recommended) or [2] MicroSD (60s flush) [1]: ").strip()
            storage_mode = "sd" if ans == "2" else "ssd"

    is_ssd = storage_mode == "ssd"
    flush_interval = 15.0 if is_ssd else 60.0

    print(f"\n=== Installing GivEnergy Local Control (user={service_user}, port={port}, storage={storage_mode}) ===")

    # 1. Storage & journald tuning
    print("[1/5] Configuring systemd-journald...")
    jdir = Path("/etc/systemd/journald.conf.d")
    jdir.mkdir(parents=True, exist_ok=True)
    conf_name = "ssdlog.conf" if is_ssd else "ramlog.conf"
    other_name = "ramlog.conf" if is_ssd else "ssdlog.conf"
    (jdir / other_name).unlink(missing_ok=True)
    (jdir / conf_name).write_text(SSDLOG_CONF if is_ssd else RAMLOG_CONF, encoding="utf-8")
    _run(["systemctl", "restart", "systemd-journald"], check=False)
    if is_ssd:
        _run(["systemctl", "enable", "--now", "fstrim.timer"], check=False)

    # 2. System prerequisites & Python venv
    print("[2/5] Ensuring curl, python3-venv, and Python virtual environment...")
    venv_check = subprocess.run([sys.executable, "-m", "venv", "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not shutil.which("curl") or venv_check.returncode != 0:
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        _run(["apt-get", "update", "-qq"], env=env)
        _run(["apt-get", "install", "-y", "-qq", "curl", "python3-venv"], env=env)

    _run(["systemctl", "stop", SERVICE_NAME], check=False)
    data_dir = REMOTE_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    if args.wipe_data:
        for f in data_dir.glob("givenergy_history.db*"):
            f.unlink(missing_ok=True)
        (data_dir / "property_config.json").unlink(missing_ok=True)

    # 3. Copy package files & install Python dependencies
    print(f"[3/5] Installing application files into {REMOTE_ROOT}...")
    target_pkg = REMOTE_ROOT / "givenergy_web"
    if target_pkg.exists():
        shutil.rmtree(target_pkg)
    shutil.copytree(pkg_src, target_pkg, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))

    venv_dir = REMOTE_ROOT / ".venv"
    if not (venv_dir / "bin" / "uvicorn").exists():
        _run([sys.executable, "-m", "venv", str(venv_dir)])
    pip_exe = str(venv_dir / "bin" / "pip")
    _run([pip_exe, "install", "--upgrade", "pip", "-q"])
    _run([pip_exe, "install", *PIP_PACKAGES, "-q"])
    _run(["chown", "-R", f"{service_user}:{service_user}", str(REMOTE_ROOT)])

    # 4. Systemd unit & sudoers rule
    print(f"[4/5] Configuring {SERVICE_NAME} and sudoers permissions...")
    unit_path = Path(f"/etc/systemd/system/{SERVICE_NAME}")
    unit_path.write_text(
        SYSTEMD_UNIT_TEMPLATE.format(user=service_user, flush_interval=flush_interval, port=port),
        encoding="utf-8",
    )
    os.chmod(unit_path, 0o644)

    sudo_rule = (
        f'Defaults:{service_user} env_keep += "DEBIAN_FRONTEND"\n'
        f"{service_user} ALL=(root) NOPASSWD: "
        "/bin/systemctl reboot, /bin/systemctl poweroff, /bin/systemctl restart givenergy-web.service, "
        "/usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, /usr/bin/systemctl restart givenergy-web.service, "
        "/sbin/reboot, /sbin/poweroff, /usr/sbin/reboot, /usr/sbin/poweroff, "
        "/usr/bin/apt-get update, /usr/bin/apt-get upgrade -y\n"
    )
    sudo_path = Path("/etc/sudoers.d/givenergy-web-ops")
    sudo_path.write_text(sudo_rule, encoding="utf-8")
    os.chmod(sudo_path, 0o440)

    _run(["systemctl", "daemon-reload"])
    _run(["systemctl", "enable", SERVICE_NAME])
    _run(["systemctl", "restart", SERVICE_NAME])

    # 5. Optional Tailscale
    if args.tailscale:
        print("[5/5] Installing / enabling Tailscale...")
        if not shutil.which("tailscale"):
            _run(["bash", "-c", "curl -fsSL https://tailscale.com/install.sh | sh"])
        _run(["systemctl", "enable", "--now", "tailscaled"], check=False)
        _run(["tailscale", "up"], check=False)
        _run(["tailscale", "funnel", "--bg", str(port)], check=False)
    else:
        print("[5/5] Skipping Tailscale setup (use --tailscale to enable).")

    local_ip = _detect_local_ip()
    url = f"http://{local_ip}" if port == 80 else f"http://{local_ip}:{port}"
    print(f"\n[OK] GivEnergy Local Control is installed and running!")
    print(f"     Dashboard URL : {url}")
    print(f"     Service Status: systemctl status {SERVICE_NAME}")
    print(f"     Uninstall     : sudo python3 {Path(__file__).name} --uninstall")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Linux Installer & Uninstaller for GivEnergy Local Control.")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall GivEnergy Local Control from this machine")
    parser.add_argument("--user", default="", help="Linux user account to run the systemd service")
    parser.add_argument("--port", type=int, default=80, help="HTTP port for the web dashboard (default: 80)")
    parser.add_argument("--storage", choices=("ssd", "sd"), default="", help="Storage mode: ssd (15s) or sd (60s)")
    parser.add_argument("--tailscale", action="store_true", help="Install Tailscale and configure Funnel")
    parser.add_argument("--wipe-data", action="store_true", help="Wipe existing database & property config")
    parser.add_argument("--backup-path", default="", help="Backup .zip path when uninstalling")
    parser.add_argument("--no-backup", action="store_true", help="Skip pre-uninstall backup")
    parser.add_argument("--reset-tailscale", action="store_true", help="Reset Tailscale Funnel when uninstalling")
    parser.add_argument("--yes", action="store_true", help="Run non-interactively using defaults/flags")
    args = parser.parse_args()

    try:
        if args.uninstall:
            return uninstall_local(args)
        return install_local(args)
    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
