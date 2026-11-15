"""One-Click SFTP & Systemd Deployment Script from Windows PC to Raspberry Pi 5."""

from __future__ import annotations

import getpass
import os
import posixpath
import sys
import time
from pathlib import Path

import paramiko

PI_HOST = os.environ.get("GIVENERGY_PI_HOST", "")
PI_USER = os.environ.get("GIVENERGY_PI_USER", "")
PI_PASS = os.environ.get("GIVENERGY_PI_PASS", "")
REMOTE_ROOT = "/opt/givenergy-web"

LOCAL_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

FILES_TO_DEPLOY = [
    ("givenergy_app/__init__.py", "givenergy_app/__init__.py"),
    ("givenergy_app/models.py", "givenergy_app/models.py"),
    ("givenergy_web/__init__.py", "givenergy_web/__init__.py"),
    ("givenergy_web/config.py", "givenergy_web/config.py"),
    ("givenergy_web/database.py", "givenergy_web/database.py"),
    ("givenergy_web/poller.py", "givenergy_web/poller.py"),
    ("givenergy_web/server.py", "givenergy_web/server.py"),
    ("givenergy_web/tariffs.py", "givenergy_web/tariffs.py"),
    ("givenergy_web/static/index.html", "givenergy_web/static/index.html"),
    ("givenergy_web/static/manifest.json", "givenergy_web/static/manifest.json"),
    ("givenergy_web/static/icon-192.png", "givenergy_web/static/icon-192.png"),
    ("givenergy_web/static/icon-512.png", "givenergy_web/static/icon-512.png"),
]

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
ExecStart=/opt/givenergy-web/.venv/bin/uvicorn givenergy_web.server:app --host 0.0.0.0 --port 80 --no-access-log
Restart=always
RestartSec=5
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
"""


def run_remote(
    ssh: paramiko.SSHClient, cmd: str, sudo: bool = False, password: str | None = None
) -> tuple[int, str, str]:
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


def sftp_mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    parts = remote_dir.strip("/").split("/")
    cur = ""
    for p in parts:
        cur = f"{cur}/{p}"
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            sftp.mkdir(cur)


def main(
    pi_host: str | None = None,
    pi_user: str | None = None,
    pi_pass: str | None = None,
    log_cb=None,
    flush_interval: float = 60.0,
    wipe_existing_data: bool = False,
) -> int:
    host = pi_host or PI_HOST or input("Raspberry Pi IP / Hostname: ").strip()
    user = pi_user or PI_USER or input("Raspberry Pi SSH Username: ").strip()
    pw = (pi_pass if pi_pass is not None else PI_PASS) or getpass.getpass(f"SSH Password for {user}@{host}: ")
    log = log_cb or print

    log(f"=== Deploying GivEnergy Web Service to Raspberry Pi ({user}@{host}) ===")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=pw, timeout=10.0)

    try:
        # 1. Optionally wipe existing data/hardware config, then ensure directory structure
        if wipe_existing_data:
            log("Wiping existing telemetry history & saved hardware configuration...")
            run_remote(ssh, "systemctl stop givenergy-web.service || true", sudo=True, password=pw)
            run_remote(
                ssh,
                f"rm -f {REMOTE_ROOT}/data/givenergy_history.db* {REMOTE_ROOT}/data/property_config.json",
                sudo=True,
                password=pw,
            )
            log("  [Wiped] Existing telemetry database and saved property/hardware config removed.")

        run_remote(ssh, f"mkdir -p {REMOTE_ROOT}/data && chown -R {user}:{user} {REMOTE_ROOT}", sudo=True, password=pw)

        # 2. Upload application files via SFTP
        sftp = ssh.open_sftp()
        try:
            for local_rel, remote_rel in FILES_TO_DEPLOY:
                local_path = LOCAL_ROOT / local_rel
                remote_path = posixpath.join(REMOTE_ROOT, remote_rel)
                remote_dir = posixpath.dirname(remote_path)
                sftp_mkdir_p(sftp, remote_dir)
                sftp.put(str(local_path), remote_path)
                log(f"  [Uploaded] {local_rel} -> {remote_path}")

            # Upload systemd unit file to /tmp and move into /etc/systemd/system
            tmp_unit = "/tmp/givenergy-web.service"
            with sftp.file(tmp_unit, "w") as f:
                f.write(SYSTEMD_UNIT_TEMPLATE.format(user=user, flush_interval=flush_interval))

            # Upload least-privilege sudoers rule for web UI Pi maintenance actions
            tmp_sudo = "/tmp/givenergy-web-ops"
            sudo_rule = (
                f"Defaults:{user} env_keep += \"DEBIAN_FRONTEND\"\n"
                f"{user} ALL=(root) NOPASSWD: "
                "/bin/systemctl reboot, /bin/systemctl poweroff, "
                "/usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, "
                "/sbin/reboot, /sbin/poweroff, /usr/sbin/reboot, /usr/sbin/poweroff, "
                "/usr/bin/apt-get update, /usr/bin/apt-get upgrade -y\n"
            )
            with sftp.file(tmp_sudo, "w") as f:
                f.write(sudo_rule)
        finally:
            sftp.close()

        # 3. Install & enable systemd service + sudoers rule
        log("\n=== Configuring & Starting givenergy-web.service on port 80 ===")
        rc, out, err = run_remote(
            ssh,
            "chown root:root /tmp/givenergy-web-ops && "
            "chmod 440 /tmp/givenergy-web-ops && "
            "visudo -cf /tmp/givenergy-web-ops && "
            "mv /tmp/givenergy-web-ops /etc/sudoers.d/givenergy-web-ops && "
            "mv /tmp/givenergy-web.service /etc/systemd/system/givenergy-web.service && "
            "chmod 644 /etc/systemd/system/givenergy-web.service && "
            "systemctl daemon-reload && "
            "systemctl enable givenergy-web.service && "
            "systemctl restart givenergy-web.service",
            sudo=True,
            password=pw,
        )
        if rc != 0:
            log(f"Systemd setup failed (rc={rc}): {err}")
            return 1

        # 4. Wait a few seconds for first Modbus poll & verify service status
        log("Waiting 6 seconds for initial hardware poll on the Pi...")
        time.sleep(6)

        rc, out, err = run_remote(ssh, "systemctl is-active givenergy-web.service")
        log(f"Service status: {out}")

        rc, out, err = run_remote(
            ssh,
            "curl -s http://127.0.0.1/api/snapshot | head -c 400",
        )
        log(f"Live API verification preview:\n{out}...\n")
        log("=== Deployment Complete! Click Next to view your links and open the site. ===")
        return 0
    finally:
        ssh.close()


if __name__ == "__main__":
    sys.exit(main())
