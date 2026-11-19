"""Clean Raspberry Pi Uninstaller for GivEnergy Local Control (with Backup)."""

from __future__ import annotations

import argparse
import getpass
import io
import json
import os
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

import paramiko

from deploy_to_pi import REMOTE_ROOT, run_remote


def _connect_ssh(host: str, user: str, password: str) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=10.0)
    return ssh

LogFn = Callable[[str], None]


def _default_log(msg: str) -> None:
    print(msg, flush=True)


def check_pi_installation(host: str, user: str, password: str) -> dict[str, bool]:
    """Inspect the Pi via SSH to see if GivEnergy Local Control and any data files exist."""
    ssh = _connect_ssh(host, user, password)
    try:
        rc, out, _ = run_remote(
            ssh,
            f"test -d '{REMOTE_ROOT}' -o -f /etc/systemd/system/givenergy-web.service && echo INSTALLED || echo NONE; "
            f"ls -A '{REMOTE_ROOT}/data' 2>/dev/null | head -n 1 || true",
        )
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        installed = bool(lines and lines[0] == "INSTALLED")
        has_data = len(lines) > 1
        return {"installed": installed, "has_data": has_data}
    finally:
        ssh.close()


def backup_from_pi(
    host: str,
    user: str,
    password: str,
    dest_zip: Path,
    *,
    log_cb: LogFn = _default_log,
) -> Path:
    """Download a full backup .zip from the Pi (HTTP API first, SFTP fallback)."""
    dest_zip = Path(dest_zip)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)

    # 1. Prefer live HTTP export so RAM-buffered telemetry is flushed into SQLite
    export_url = f"http://{host}/api/backup/export?include_config=true&include_history=true"
    try:
        log_cb(f"Requesting live backup from {export_url} ...")
        req = urllib.request.Request(export_url, headers={"User-Agent": "GivEnergy-Uninstaller/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
        if payload and zipfile.is_zipfile(io.BytesIO(payload)):
            dest_zip.write_bytes(payload)
            log_cb(f"[OK] Live backup saved to {dest_zip} ({len(payload) // 1024} KB).")
            return dest_zip
    except Exception as exc:
        log_cb(f"Live HTTP backup unavailable ({exc}); falling back to SFTP...")

    # 2. Fallback: pull data files directly from /opt/givenergy-web/data via SFTP
    ssh = _connect_ssh(host, user, password)
    try:
        sftp = ssh.open_sftp()
        try:
            remote_data = f"{REMOTE_ROOT}/data"
            with tempfile.TemporaryDirectory(prefix="givenergy_pi_backup_") as tmp_str:
                tmp_dir = Path(tmp_str)
                files_to_pull = [
                    "property_config.json",
                    "tariff_config.json",
                    "solar_forecast_config.json",
                    "givenergy_history.db",
                ]
                pulled: list[str] = []
                for fname in files_to_pull:
                    rpath = f"{remote_data}/{fname}"
                    lpath = tmp_dir / fname
                    try:
                        sftp.get(rpath, str(lpath))
                        pulled.append(fname)
                    except Exception:
                        pass

                if not pulled:
                    raise RuntimeError(f"No configuration or database files found in {remote_data}.")

                manifest = {
                    "format_version": 1,
                    "source": "uninstall_pi_sftp_fallback",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "host": host,
                    "files": pulled,
                }
                with zipfile.ZipFile(dest_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr("manifest.json", json.dumps(manifest, indent=2))
                    for fname in pulled:
                        zf.write(tmp_dir / fname, arcname=fname)

                log_cb(f"[OK] SFTP backup saved to {dest_zip} ({len(pulled)} file(s)).")
                return dest_zip
        finally:
            sftp.close()
    finally:
        ssh.close()


def uninstall_from_pi(
    host: str,
    user: str,
    password: str,
    *,
    reset_tailscale_funnel: bool = False,
    log_cb: LogFn = _default_log,
) -> None:
    """Stop services, remove systemd/sudoers/journald config, and delete /opt/givenergy-web."""
    log_cb(f"Connecting to {user}@{host} via SSH...")
    ssh = _connect_ssh(host, user, password)
    try:
        log_cb("[1/5] Stopping and disabling givenergy-web.service...")
        run_remote(ssh, "systemctl stop givenergy-web.service 2>/dev/null || true", sudo=True, password=password)
        run_remote(ssh, "systemctl disable givenergy-web.service 2>/dev/null || true", sudo=True, password=password)

        log_cb("[2/5] Removing systemd unit and sudoers rule...")
        rc, _, err = run_remote(
            ssh,
            "rm -f /etc/systemd/system/givenergy-web.service /etc/sudoers.d/givenergy-web-ops && systemctl daemon-reload",
            sudo=True,
            password=password,
        )
        if rc != 0:
            raise RuntimeError(f"Failed to remove systemd/sudoers files: {err}")

        log_cb("[3/5] Cleaning up volatile journald ramlog config (if present)...")
        run_remote(
            ssh,
            "if [ -f /etc/systemd/journald.conf.d/ramlog.conf ]; then "
            "rm -f /etc/systemd/journald.conf.d/ramlog.conf && systemctl restart systemd-journald; fi",
            sudo=True,
            password=password,
        )

        if reset_tailscale_funnel:
            log_cb("[4/5] Resetting Tailscale Funnel configuration...")
            run_remote(
                ssh,
                "if command -v tailscale >/dev/null 2>&1; then tailscale funnel reset 2>/dev/null || true; fi",
                sudo=True,
                password=password,
            )
        else:
            log_cb("[4/5] Keeping Tailscale configuration untouched.")

        log_cb(f"[5/5] Removing {REMOTE_ROOT} ...")
        rc, _, err = run_remote(ssh, f"rm -rf '{REMOTE_ROOT}'", sudo=True, password=password)
        if rc != 0:
            raise RuntimeError(f"Failed to remove {REMOTE_ROOT}: {err}")

        log_cb("[OK] GivEnergy Local Control has been completely removed from the Raspberry Pi.")
    finally:
        ssh.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Uninstall GivEnergy Local Control from a Raspberry Pi.")
    parser.add_argument("--host", default=os.environ.get("PI_HOST", ""), help="Raspberry Pi IP or hostname")
    parser.add_argument("--user", default=os.environ.get("PI_USER", "pi"), help="SSH username")
    parser.add_argument("--password", default=os.environ.get("PI_PASSWORD", ""), help="SSH password")
    parser.add_argument("--backup-path", default="", help="Destination .zip path for pre-uninstall backup")
    parser.add_argument("--no-backup", action="store_true", help="Skip pre-uninstall backup")
    parser.add_argument("--reset-tailscale", action="store_true", help="Reset Tailscale Funnel rules on the Pi")
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation prompt")
    args = parser.parse_args()

    host = args.host.strip() or input("Raspberry Pi IP / Hostname: ").strip()
    if not host:
        print("Error: host is required.")
        return 1

    user = args.user.strip() or "pi"
    password = args.password or getpass.getpass(f"SSH password for {user}@{host}: ")

    # Step 1: Confirmation
    if not args.yes:
        ans = input(
            f"\nAre you sure you want to uninstall GivEnergy Local Control from {host}?\n"
            f"This will stop the service and delete {REMOTE_ROOT}. [y/N]: "
        ).strip().lower()
        if ans not in ("y", "yes"):
            print("Uninstall cancelled.")
            return 0

    # Step 2: Backup
    if not args.no_backup:
        take_backup = bool(args.backup_path)
        if not take_backup:
            b_ans = input("\nTake a backup of config & historic database before removing? [Y/n]: ").strip().lower()
            take_backup = b_ans in ("", "y", "yes")
        if take_backup:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dest = Path(args.backup_path) if args.backup_path else Path(f"givenergy-backup-{host}-{stamp}.zip")
            try:
                backup_from_pi(host, user, password, dest)
            except Exception as exc:
                print(f"\n[ERROR] Backup failed: {exc}")
                cont = input("Continue with uninstall WITHOUT a backup? [y/N]: ").strip().lower()
                if cont not in ("y", "yes"):
                    print("Uninstall aborted.")
                    return 1

    # Step 3: Remove software
    try:
        uninstall_from_pi(host, user, password, reset_tailscale_funnel=args.reset_tailscale)
        return 0
    except Exception as exc:
        print(f"\n[ERROR] Uninstall failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
