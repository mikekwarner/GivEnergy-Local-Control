"""Full Idempotent Raspberry Pi 5 Provisioning, Tailscale Setup & App Deployment Script.

Run this from your Windows PC to configure a fresh Raspberry Pi OS Lite installation
(or re-provision after wiping the SD card), install Tailscale for remote access,
and deploy the GivEnergy Local Control web service.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import paramiko

import deploy_to_pi

PI_HOST = os.environ.get("GIVENERGY_PI_HOST", deploy_to_pi.PI_HOST)
PI_USER = os.environ.get("GIVENERGY_PI_USER", deploy_to_pi.PI_USER)
PI_PASS = os.environ.get("GIVENERGY_PI_PASS", deploy_to_pi.PI_PASS)
REMOTE_ROOT = deploy_to_pi.REMOTE_ROOT

RAMLOG_CONF = """[Journal]
Storage=volatile
Compress=yes
RuntimeMaxUse=32M
RuntimeMaxFileSize=8M
"""

PI_PACKAGES = [
    "givenergy-modbus",
    "pymodbus",
    "fastapi",
    "uvicorn[standard]",
    "websockets",
    "qrcode",
]


def run_remote(ssh: paramiko.SSHClient, cmd: str, sudo: bool = False, password: str | None = None) -> tuple[int, str, str]:
    pw = PI_PASS if password is None else password
    if sudo:
        escaped = cmd.replace("'", "'\"'\"'")
        full_cmd = f"sudo -S -p '' bash -c '{escaped}'"
        stdin, stdout, stderr = ssh.exec_command(full_cmd)
        stdin.write(pw + "\n")
        stdin.flush()
    else:
        stdin, stdout, stderr = ssh.exec_command(cmd)
    rc = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    return rc, out, err


def configure_ramlog(ssh: paramiko.SSHClient) -> None:
    print("\n[1/5] Configuring SD-card-safe volatile RAM journald logging...")
    sftp = ssh.open_sftp()
    try:
        with sftp.file("/tmp/ramlog.conf", "w") as f:
            f.write(RAMLOG_CONF)
    finally:
        sftp.close()

    rc, _, err = run_remote(
        ssh,
        "mkdir -p /etc/systemd/journald.conf.d && "
        "mv /tmp/ramlog.conf /etc/systemd/journald.conf.d/ramlog.conf && "
        "chmod 644 /etc/systemd/journald.conf.d/ramlog.conf && "
        "systemctl restart systemd-journald",
        sudo=True,
    )
    if rc != 0:
        raise RuntimeError(f"Failed to configure ramlog: {err}")
    print("  [OK] systemd-journald set to volatile RAM (32M max).")


def configure_python_env(ssh: paramiko.SSHClient) -> None:
    print("\n[2/5] Ensuring system prerequisites & Python virtual environment...")
    rc, _, _ = run_remote(ssh, "which curl >/dev/null 2>&1 && python3 -m venv --help >/dev/null 2>&1")
    if rc != 0:
        print("  Installing curl / python3-venv via apt...")
        rc, _, err = run_remote(
            ssh,
            "DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl python3-venv",
            sudo=True,
        )
        if rc != 0:
            raise RuntimeError(f"apt-get install failed: {err}")

    rc, _, err = run_remote(
        ssh,
        f"mkdir -p {REMOTE_ROOT}/data && chown -R {PI_USER}:{PI_USER} {REMOTE_ROOT}",
        sudo=True,
    )
    if rc != 0:
        raise RuntimeError(f"Failed to create {REMOTE_ROOT}: {err}")

    rc, _, _ = run_remote(ssh, f"test -x {REMOTE_ROOT}/.venv/bin/python")
    if rc != 0:
        print(f"  Creating venv at {REMOTE_ROOT}/.venv ...")
        rc, _, err = run_remote(ssh, f"python3 -m venv {REMOTE_ROOT}/.venv")
        if rc != 0:
            raise RuntimeError(f"Failed to create venv: {err}")

    pkg_str = " ".join(f'"{p}"' for p in PI_PACKAGES)
    print(f"  Installing/verifying Python packages: {', '.join(PI_PACKAGES)} ...")
    rc, _, err = run_remote(ssh, f"{REMOTE_ROOT}/.venv/bin/python -m pip install -q --upgrade {pkg_str}")
    if rc != 0:
        raise RuntimeError(f"pip install failed: {err}")
    print("  [OK] Python environment ready.")


def get_tailscale_info(ssh: paramiko.SSHClient) -> tuple[str, str, str]:
    """Return (backend_state, ipv4, dns_name)."""
    rc, out, _ = run_remote(ssh, "tailscale status --json 2>/dev/null || true")
    if not out:
        return "Unknown", "", ""
    try:
        data = json.loads(out)
        state = data.get("BackendState", "Unknown")
        self_node = data.get("Self") or {}
        ips = self_node.get("TailscaleIPs") or []
        ipv4 = next((ip for ip in ips if "." in ip), "")
        dns_name = (self_node.get("DNSName") or "").rstrip(".")
        return state, ipv4, dns_name
    except Exception:
        return "Unknown", "", ""


def enable_tailscale_funnel(
    ssh: paramiko.SSHClient,
    local_port: int = 80,
    password: str | None = None,
) -> tuple[bool, str, str]:
    """Enable persistent Tailscale Funnel for local_port and return (ok, funnel_url, err_msg)."""
    state, ipv4, dns_name = get_tailscale_info(ssh)
    if state != "Running" or not ipv4:
        return False, "", "Tailscale is not connected yet."
    rc, out, err = run_remote(ssh, f"tailscale funnel --bg {int(local_port)}", sudo=True, password=password)
    if rc != 0:
        return False, "", err or out or f"tailscale funnel exited with {rc}"
    funnel_url = f"https://{dns_name}" if dns_name else ""
    return True, funnel_url, ""


def configure_tailscale(ssh: paramiko.SSHClient, wait_for_auth: bool = True) -> tuple[str, str]:
    print("\n[3/5] Installing & configuring Tailscale for secure remote access...")
    rc, _, _ = run_remote(ssh, "which tailscale >/dev/null 2>&1")
    if rc != 0:
        print("  Installing Tailscale via official repository...")
        rc, _, err = run_remote(ssh, "curl -fsSL https://tailscale.com/install.sh | sh", sudo=True)
        if rc != 0:
            raise RuntimeError(f"Tailscale installation failed: {err}")

    run_remote(ssh, "systemctl enable --now tailscaled", sudo=True)

    state, ipv4, dns_name = get_tailscale_info(ssh)
    if state == "Running" and ipv4:
        print(f"  [OK] Tailscale already connected: {ipv4} ({dns_name or 'MagicDNS'})")
        ok_f, f_url, f_err = enable_tailscale_funnel(ssh)
        if ok_f:
            print(f"  [OK] Tailscale Funnel enabled: {f_url or '(active on :443 -> :80)'}")
        else:
            print(f"  [WARN] Could not enable Tailscale Funnel: {f_err}")
        return ipv4, dns_name

    authkey = os.environ.get("TAILSCALE_AUTHKEY", "").strip()
    if authkey:
        print("  Authenticating Tailscale using TAILSCALE_AUTHKEY...")
        rc, out, err = run_remote(ssh, f"tailscale up --authkey={authkey}", sudo=True)
        if rc != 0:
            raise RuntimeError(f"tailscale up failed: {err or out}")
    else:
        print("  Starting Tailscale authentication...")
        run_remote(
            ssh,
            "systemctl stop givenergy-ts-up 2>/dev/null || true; "
            "rm -f /tmp/ts_up.log && "
            "systemd-run --unit=givenergy-ts-up --collect "
            "-p StandardOutput=file:/tmp/ts_up.log -p StandardError=inherit "
            "/usr/bin/tailscale up",
            sudo=True,
        )
        auth_url = ""
        for _ in range(15):
            time.sleep(1)
            _, log_txt, _ = run_remote(ssh, "cat /tmp/ts_up.log 2>/dev/null || true; tailscale status 2>&1 || true")
            m = re.search(r"https://login\.tailscale\.com/\S+", log_txt)
            if m:
                auth_url = m.group(0)
                break
            state, ipv4, dns_name = get_tailscale_info(ssh)
            if state == "Running" and ipv4:
                break

        if auth_url:
            print("\n  ============================================================")
            print("  TAILSCALE LOGIN REQUIRED:")
            print(f"  Open this URL in your browser to link your Pi 5:\n  {auth_url}")
            print("  ============================================================\n")
            if wait_for_auth:
                print("  Waiting up to 180 seconds for you to complete login...")
                for _ in range(90):
                    time.sleep(2)
                    state, ipv4, dns_name = get_tailscale_info(ssh)
                    if state == "Running" and ipv4:
                        print(f"  [OK] Tailscale authenticated! IP: {ipv4}")
                        return ipv4, dns_name
                print("  [WARN] Timed out waiting for Tailscale login; continuing with app deployment.")
                print(f"         You can still finish login at: {auth_url}")
            return "", ""

    state, ipv4, dns_name = get_tailscale_info(ssh)
    if ipv4:
        print(f"  [OK] Tailscale connected: {ipv4} ({dns_name})")
        ok_f, f_url, f_err = enable_tailscale_funnel(ssh)
        if ok_f:
            print(f"  [OK] Tailscale Funnel enabled: {f_url or '(active on :443 -> :80)'}")
        else:
            print(f"  [WARN] Could not enable Tailscale Funnel: {f_err}")
    return ipv4, dns_name


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision Raspberry Pi 5 + Tailscale + GivEnergy Web Service")
    parser.add_argument(
        "--no-wait-tailscale",
        action="store_true",
        help="Start tailscale up and print the login URL without blocking until sign-in completes.",
    )
    args = parser.parse_args()

    print(f"=== Full Raspberry Pi 5 Setup ({PI_USER}@{PI_HOST}) ===")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(PI_HOST, username=PI_USER, password=PI_PASS, timeout=15.0)

    try:
        configure_ramlog(ssh)
        configure_python_env(ssh)
        ts_ip, ts_dns = configure_tailscale(ssh, wait_for_auth=not args.no_wait_tailscale)
    finally:
        ssh.close()

    print("\n[4/5] Deploying GivEnergy Web Service & systemd unit...")
    rc = deploy_to_pi.main()
    if rc != 0:
        return rc

    print("\n[5/5] Summary:")
    print(f"  Local LAN URL : http://{PI_HOST}  (or http://givenergy-pi.local)")
    if ts_ip:
        print(f"  Tailscale IP  : http://{ts_ip}")
    if ts_dns:
        print(f"  MagicDNS URL  : http://{ts_dns}")
        print(f"  Funnel HTTPS  : https://{ts_dns}  (Bearer Token auth enabled)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
