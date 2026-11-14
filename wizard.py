"""Windows Deployment Wizard for GivEnergy Local Control & Telemetry (Raspberry Pi)."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import paramiko
from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

import deploy_to_pi
import setup_pi
from givenergy_web import __version__ as APP_VERSION

# Official Raspberry Pi Foundation MAC OUI prefixes
RPI_MAC_PREFIXES = (
    "b8-27-eb",
    "dc-a6-32",
    "e4-5f-01",
    "d8-3a-dd",
    "2c-cf-67",
    "88-a2-9e",
    "80-69-1a",
)

WIZARD_STYLESHEET = """
QWizard, QWizardPage, QWidget {
    background-color: #0B101B;
    color: #F1F5F9;
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}
QGroupBox {
    background-color: #111827;
    border: 1px solid #1F2A40;
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px;
    font-weight: 700;
    color: #93C5FD;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
}
QLineEdit, QComboBox {
    background-color: #090D16;
    color: #F8FAFC;
    border: 1px solid #2E3F5E;
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 22px;
    font-size: 13px;
}
QLineEdit:focus, QComboBox:focus {
    border-color: #3B82F6;
}
QPushButton {
    background-color: #192234;
    color: #F1F5F9;
    border: 1px solid #2E3F5E;
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #24314A;
    border-color: #47608A;
}
QPushButton#primaryBtn {
    background-color: #1D4ED8;
    border-color: #3B82F6;
    color: #FFFFFF;
}
QPushButton#primaryBtn:hover {
    background-color: #2563EB;
}
QPushButton#successBtn {
    background-color: #065F46;
    border-color: #10B981;
    color: #ECFDF5;
}
QPushButton#successBtn:hover {
    background-color: #047857;
}
QTableWidget {
    background-color: #090D16;
    border: 1px solid #1F2A40;
    border-radius: 6px;
    gridline-color: #1F2A40;
    selection-background-color: #1E3A8A;
}
QHeaderView::section {
    background-color: #162033;
    color: #94A3B8;
    padding: 6px;
    border: none;
    border-bottom: 1px solid #2E3F5E;
    font-weight: 700;
}
QTextEdit {
    background-color: #080B12;
    color: #CBD5E1;
    border: 1px solid #1F2A40;
    border-radius: 6px;
    font-family: 'Cascadia Code', Consolas, monospace;
    font-size: 12px;
}
QProgressBar {
    background-color: #090D16;
    border: 1px solid #1F2A40;
    border-radius: 6px;
    text-align: center;
    height: 20px;
}
QProgressBar::chunk {
    background-color: #10B981;
    border-radius: 5px;
}
"""


def _detect_pc_ipv4() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "192.168.0.1"
    finally:
        s.close()


def _parse_windows_arp() -> dict[str, str]:
    """Return {ip: mac} from Windows `arp -a` output."""
    mapping: dict[str, str] = {}
    try:
        out = subprocess.check_output(
            ["arp", "-a"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in out.splitlines():
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})", line)
            if m:
                mapping[m.group(1)] = m.group(2).lower()
    except Exception:
        pass
    return mapping


async def _probe_tcp(ip: str, port: int, sem: asyncio.Semaphore, timeout: float = 0.35) -> bool:
    async with sem:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False


def _parse_semver(ver: str | None) -> tuple[int, ...]:
    if not ver:
        return (0, 0, 0)
    parts = re.findall(r"\d+", ver)
    return tuple(int(p) for p in parts[:3]) if parts else (0, 0, 0)


async def _scan_lan_for_pis() -> tuple[str, list[dict]]:
    local_ip = _detect_pc_ipv4()
    net = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    hosts = [str(ip) for ip in net.hosts()][:254]
    sem = asyncio.Semaphore(90)

    ssh_tasks = [_probe_tcp(ip, 22, sem) for ip in hosts]
    http_tasks = [_probe_tcp(ip, 80, sem) for ip in hosts]
    ssh_res, http_res = await asyncio.gather(asyncio.gather(*ssh_tasks), asyncio.gather(*http_tasks))

    arp_map = _parse_windows_arp()
    candidates: list[dict] = []

    for ip, has_ssh, has_http in zip(hosts, ssh_res, http_res):
        mac = arp_map.get(ip, "")
        is_rpi_mac = any(mac.startswith(prefix) for prefix in RPI_MAC_PREFIXES)
        if not (has_ssh or is_rpi_mac):
            continue

        info_str = ""
        is_givenergy = False
        installed_version = ""
        upgrade_needed = False
        if has_http:
            try:
                with urllib.request.urlopen(f"http://{ip}/api/system/stats", timeout=0.8) as resp:
                    if resp.status == 200:
                        js = json.loads(resp.read().decode("utf-8"))
                        if "hardware_model" in js:
                            is_givenergy = True
                            installed_version = str(js.get("app_version") or "").strip()
                            info_str = f"{js.get('hostname', 'pi')} ({js.get('hardware_model', 'Raspberry Pi')})"
            except Exception:
                pass

        if not info_str and is_rpi_mac:
            info_str = "Raspberry Pi (MAC match)"
        elif not info_str:
            try:
                hostname = socket.gethostbyaddr(ip)[0]
                info_str = hostname
            except Exception:
                info_str = "SSH Host"

        if is_givenergy:
            if not installed_version:
                upgrade_needed = True
                status = f"Installed: < 1.0.0 (Upgrade to v{APP_VERSION} recommended)"
            elif _parse_semver(installed_version) < _parse_semver(APP_VERSION):
                upgrade_needed = True
                status = f"Installed: v{installed_version} (Upgrade to v{APP_VERSION} available)"
            else:
                status = f"Up to date (v{installed_version})"
        else:
            status = "Ready for Setup (SSH)" if has_ssh else "Pi Detected (No SSH)"
        candidates.append(
            {
                "ip": ip,
                "mac": mac or "—",
                "is_rpi": is_rpi_mac or is_givenergy,
                "has_ssh": has_ssh,
                "is_givenergy": is_givenergy,
                "installed_version": installed_version,
                "upgrade_needed": upgrade_needed,
                "info": info_str,
                "status": status,
            }
        )

    # Sort: GivEnergy / RPi MAC matches first, then by IP
    candidates.sort(key=lambda c: (not c["is_rpi"], not c["has_ssh"], ipaddress.IPv4Address(c["ip"])))
    return str(net), candidates


class NetworkScanWorker(QThread):
    finished_scan = Signal(str, list)
    failed_scan = Signal(str)

    def run(self) -> None:
        try:
            subnet, results = asyncio.run(_scan_lan_for_pis())
            self.finished_scan.emit(subnet, results)
        except Exception as exc:
            self.failed_scan.emit(str(exc))


class DeployWorker(QThread):
    log_line = Signal(str)
    progress = Signal(int)
    tailscale_url = Signal(str)
    finished_deploy = Signal(bool, str, str, str)  # ok, local_url, ts_ip, ts_dns

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        install_tailscale: bool,
        tailscale_authkey: str,
        storage_mode: str = "sd",
        wipe_existing_data: bool = False,
    ) -> None:
        super().__init__()
        self.host = host
        self.user = user
        self.password = password
        self.install_tailscale = install_tailscale
        self.tailscale_authkey = tailscale_authkey
        self.storage_mode = storage_mode
        self.wipe_existing_data = wipe_existing_data

    def _run_remote(self, ssh: paramiko.SSHClient, cmd: str, sudo: bool = False) -> tuple[int, str, str]:
        return deploy_to_pi.run_remote(ssh, cmd, sudo=sudo, password=self.password)

    def run(self) -> None:
        ts_ip = ""
        ts_dns = ""
        try:
            self.log_line.emit(f"Connecting via SSH to {self.user}@{self.host}...")
            self.progress.emit(10)

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(self.host, username=self.user, password=self.password, timeout=15.0)

            try:
                # 1. Storage & Filesystem Optimization
                is_ssd = self.storage_mode == "ssd"
                flush_interval = 15.0 if is_ssd else 60.0
                if is_ssd:
                    self.log_line.emit("\n[1/4] Optimizing filesystem & logging for NVMe / USB SSD...")
                    journal_conf = "[Journal]\nStorage=persistent\nCompress=yes\nSystemMaxUse=128M\n"
                    conf_name = "ssdlog.conf"
                    other_conf = "ramlog.conf"
                else:
                    self.log_line.emit("\n[1/4] Optimizing filesystem & logging for MicroSD Card (wear-protection)...")
                    journal_conf = setup_pi.RAMLOG_CONF
                    conf_name = "ramlog.conf"
                    other_conf = "ssdlog.conf"

                sftp = ssh.open_sftp()
                try:
                    with sftp.file(f"/tmp/{conf_name}", "w") as f:
                        f.write(journal_conf)
                finally:
                    sftp.close()

                fs_cmds = (
                    f"mkdir -p /etc/systemd/journald.conf.d && "
                    f"rm -f /etc/systemd/journald.conf.d/{other_conf} && "
                    f"mv /tmp/{conf_name} /etc/systemd/journald.conf.d/{conf_name} && "
                    f"chmod 644 /etc/systemd/journald.conf.d/{conf_name} && "
                    f"systemctl restart systemd-journald && "
                    f"mount -o remount,noatime / 2>/dev/null || true; "
                    f"python3 -c \"from pathlib import Path; p=Path('/etc/fstab'); "
                    f"lines=p.read_text().splitlines(); "
                    f"out=[]; "
                    f"exec('for l in lines:\\n parts=l.split()\\n if len(parts)>=4 and parts[1]==\\\"/\\\" and \\\"noatime\\\" not in parts[3]:\\n  parts[3]+=\\\",noatime\\\"; l=\\\" \\\\t\\\".join(parts)\\n out.append(l)'); "
                    f"p.write_text('\\\\n'.join(out)+'\\\\n')\""
                )
                if is_ssd:
                    fs_cmds += " && systemctl enable --now fstrim.timer 2>/dev/null || true"

                rc, _, err = self._run_remote(ssh, fs_cmds, sudo=True)
                if rc != 0:
                    raise RuntimeError(f"Filesystem setup failed: {err}")
                if is_ssd:
                    self.log_line.emit("  [OK] SSD mode: persistent 128M journald, fstrim.timer, noatime, 15s DB flushes.")
                else:
                    self.log_line.emit("  [OK] MicroSD mode: volatile 32M RAM journald, noatime, 60s DB batching.")
                self.progress.emit(25)

                # 2. System prerequisites & Python venv
                self.log_line.emit("\n[2/4] Ensuring Python 3 venv & installing GivEnergy dependencies...")
                rc, _, _ = self._run_remote(ssh, "which curl >/dev/null 2>&1 && python3 -m venv --help >/dev/null 2>&1")
                if rc != 0:
                    self.log_line.emit("  Installing curl and python3-venv via apt-get...")
                    rc, _, err = self._run_remote(
                        ssh,
                        "DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
                        "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl python3-venv",
                        sudo=True,
                    )
                    if rc != 0:
                        raise RuntimeError(f"apt-get failed: {err}")

                rc, _, err = self._run_remote(
                    ssh,
                    f"mkdir -p {deploy_to_pi.REMOTE_ROOT}/data && "
                    f"chown -R {self.user}:{self.user} {deploy_to_pi.REMOTE_ROOT}",
                    sudo=True,
                )
                if rc != 0:
                    raise RuntimeError(f"Directory creation failed: {err}")

                rc, _, _ = self._run_remote(ssh, f"test -x {deploy_to_pi.REMOTE_ROOT}/.venv/bin/python")
                if rc != 0:
                    self.log_line.emit(f"  Creating virtual environment at {deploy_to_pi.REMOTE_ROOT}/.venv ...")
                    rc, _, err = self._run_remote(ssh, f"python3 -m venv {deploy_to_pi.REMOTE_ROOT}/.venv")
                    if rc != 0:
                        raise RuntimeError(f"venv creation failed: {err}")

                pkg_str = " ".join(f'"{p}"' for p in setup_pi.PI_PACKAGES)
                self.log_line.emit(f"  Installing/updating: {', '.join(setup_pi.PI_PACKAGES)} ...")
                rc, _, err = self._run_remote(
                    ssh, f"{deploy_to_pi.REMOTE_ROOT}/.venv/bin/python -m pip install -q --upgrade {pkg_str}"
                )
                if rc != 0:
                    raise RuntimeError(f"pip install failed: {err}")
                self.log_line.emit("  [OK] Python virtual environment ready.")
                self.progress.emit(55)

                # 3. Tailscale
                if self.install_tailscale:
                    self.log_line.emit("\n[3/4] Installing & configuring Tailscale for remote access...")
                    rc, _, _ = self._run_remote(ssh, "which tailscale >/dev/null 2>&1")
                    if rc != 0:
                        self.log_line.emit("  Downloading & installing Tailscale...")
                        rc, _, err = self._run_remote(
                            ssh, "curl -fsSL https://tailscale.com/install.sh | sh", sudo=True
                        )
                        if rc != 0:
                            raise RuntimeError(f"Tailscale install failed: {err}")
                    self._run_remote(ssh, "systemctl enable --now tailscaled", sudo=True)

                    state, ts_ip, ts_dns = setup_pi.get_tailscale_info(ssh)
                    if state == "Running" and ts_ip:
                        self.log_line.emit(f"  [OK] Tailscale already connected: {ts_ip} ({ts_dns})")
                        ok_f, f_url, f_err = setup_pi.enable_tailscale_funnel(ssh, password=self.password)
                        if ok_f:
                            self.log_line.emit(f"  [OK] Tailscale Funnel enabled: {f_url or 'https://' + ts_dns}")
                        else:
                            self.log_line.emit(f"  [WARN] Tailscale Funnel not enabled: {f_err}")
                    elif self.tailscale_authkey:
                        self.log_line.emit("  Authenticating Tailscale with Auth Key...")
                        rc, out, err = self._run_remote(
                            ssh, f"tailscale up --authkey={self.tailscale_authkey}", sudo=True
                        )
                        if rc != 0:
                            raise RuntimeError(f"tailscale up failed: {err or out}")
                        _, ts_ip, ts_dns = setup_pi.get_tailscale_info(ssh)
                        self.log_line.emit(f"  [OK] Tailscale connected: {ts_ip} ({ts_dns})")
                        ok_f, f_url, f_err = setup_pi.enable_tailscale_funnel(ssh, password=self.password)
                        if ok_f:
                            self.log_line.emit(f"  [OK] Tailscale Funnel enabled: {f_url or 'https://' + ts_dns}")
                        else:
                            self.log_line.emit(f"  [WARN] Tailscale Funnel not enabled: {f_err}")
                    else:
                        self.log_line.emit("  Starting Tailscale browser authentication...")
                        self._run_remote(
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
                            _, log_txt, _ = self._run_remote(
                                ssh, "cat /tmp/ts_up.log 2>/dev/null || true; tailscale status 2>&1 || true"
                            )
                            m = re.search(r"https://login\.tailscale\.com/\S+", log_txt)
                            if m:
                                auth_url = m.group(0)
                                break
                            state, ts_ip, ts_dns = setup_pi.get_tailscale_info(ssh)
                            if state == "Running" and ts_ip:
                                break
                        if auth_url:
                            self.log_line.emit(f"  [ACTION] Tailscale Login URL: {auth_url}")
                            self.tailscale_url.emit(auth_url)
                else:
                    self.log_line.emit("\n[3/4] Skipping Tailscale installation (unchecked).")
                self.progress.emit(75)
            finally:
                ssh.close()

            # 4. Deploy application & systemd unit
            self.log_line.emit("\n[4/4] Uploading GivEnergy Web Service & starting systemd service...")
            rc = deploy_to_pi.main(
                pi_host=self.host,
                pi_user=self.user,
                pi_pass=self.password,
                log_cb=self.log_line.emit,
                flush_interval=flush_interval,
                wipe_existing_data=self.wipe_existing_data,
            )
            if rc != 0:
                raise RuntimeError("Application deployment returned non-zero exit code.")

            # Re-check Tailscale IP in case user clicked the login link during step 4
            if self.install_tailscale and not ts_ip:
                try:
                    ssh2 = paramiko.SSHClient()
                    ssh2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh2.connect(self.host, username=self.user, password=self.password, timeout=6.0)
                    _, ts_ip, ts_dns = setup_pi.get_tailscale_info(ssh2)
                    if ts_ip:
                        ok_f, f_url, f_err = setup_pi.enable_tailscale_funnel(ssh2, password=self.password)
                        if ok_f:
                            self.log_line.emit(f"  [OK] Tailscale Funnel enabled: {f_url or 'https://' + ts_dns}")
                    ssh2.close()
                except Exception:
                    pass

            self.progress.emit(100)
            self.finished_deploy.emit(True, f"http://{self.host}", ts_ip, ts_dns)
        except Exception as exc:
            self.log_line.emit(f"\n[ERROR] {exc}")
            self.finished_deploy.emit(False, "", "", "")


def _find_rpi_imager() -> str | None:
    """Detect Raspberry Pi Imager executable on this Windows PC."""
    which = shutil.which("rpi-imager") or shutil.which("rpi-imager.exe")
    if which:
        return which

    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
    ]
    rel_paths = [
        Path("Raspberry Pi Ltd") / "Imager" / "rpi-imager.exe",
        Path("Raspberry Pi Imager") / "rpi-imager.exe",
    ]
    for root in roots:
        for rel in rel_paths:
            candidate = root / rel
            if candidate.is_file():
                return str(candidate)

    # Fallback: check Windows Uninstall registry keys
    try:
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for subkey in (
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            ):
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            sub_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, sub_name) as app_key:
                                try:
                                    name, _ = winreg.QueryValueEx(app_key, "DisplayName")
                                    if "Raspberry Pi Imager" in str(name):
                                        loc, _ = winreg.QueryValueEx(app_key, "InstallLocation")
                                        exe = Path(str(loc).strip('"')) / "rpi-imager.exe"
                                        if exe.is_file():
                                            return str(exe)
                                except OSError:
                                    continue
                except OSError:
                    continue
    except Exception:
        pass
    return None


class OSPrepPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Step 1: Prepare Raspberry Pi OS Lite")
        self.setSubTitle("Flash a fresh MicroSD card or SSD before running network discovery.")
        self._imager_path: str | None = None

        layout = QVBoxLayout(self)

        grp_dl = QGroupBox("1. Raspberry Pi Imager")
        dl_layout = QVBoxLayout(grp_dl)
        lbl_dl = QLabel(
            "Recommended OS: <b>Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)</b> "
            "(works on Pi 3B+, Pi 4, Pi 400, Pi 5, and Pi Zero 2 W; use 32-bit for Pi 2B)."
        )
        lbl_dl.setWordWrap(True)
        self.lbl_imager_status = QLabel("")
        self.lbl_imager_status.setWordWrap(True)

        btn_row = QHBoxLayout()
        self.btn_launch_imager = QPushButton("🚀 Launch Raspberry Pi Imager")
        self.btn_launch_imager.setObjectName("successBtn")
        self.btn_launch_imager.clicked.connect(self._launch_imager)

        self.btn_download_imager = QPushButton("🌐 Download Raspberry Pi Imager")
        self.btn_download_imager.setObjectName("primaryBtn")
        self.btn_download_imager.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://www.raspberrypi.com/software/"))
        )

        self.btn_recheck_imager = QPushButton("↻ Re-check")
        self.btn_recheck_imager.clicked.connect(self._refresh_imager_status)

        btn_row.addWidget(self.btn_launch_imager)
        btn_row.addWidget(self.btn_download_imager)
        btn_row.addWidget(self.btn_recheck_imager)
        btn_row.addStretch()

        dl_layout.addWidget(lbl_dl)
        dl_layout.addWidget(self.lbl_imager_status)
        dl_layout.addLayout(btn_row)
        layout.addWidget(grp_dl)

        self._refresh_imager_status()

        grp_cfg = QGroupBox("2. Required OS Customisation Settings in Raspberry Pi Imager")
        cfg_layout = QVBoxLayout(grp_cfg)
        lbl_cfg = QLabel(
            "In Raspberry Pi Imager's OS customisation steps, configure:<br><br>"
            "• <b>Hostname:</b> <code>givenergy-pi</code> (or unique name like <code>givenergy-annex</code>)<br>"
            "• <b>Username &amp; Password:</b> Set your Pi login (e.g. <code>pi</code> or your preferred username)<br>"
            "• <b>Wireless LAN:</b> Configure Wi-Fi only if not plugging in an Ethernet cable<br>"
            "• <b>Enable SSH:</b> Turn SSH on and select <b>Use password authentication</b><br><br>"
            "Insert the card/drive into the Pi, power it on, and wait ~45 seconds for first boot before clicking <b>Next</b>."
        )
        lbl_cfg.setWordWrap(True)
        cfg_layout.addWidget(lbl_cfg)
        layout.addWidget(grp_cfg)
        layout.addStretch()

    def _refresh_imager_status(self) -> None:
        self._imager_path = _find_rpi_imager()
        if self._imager_path:
            self.lbl_imager_status.setStyleSheet("color: #34D399;")
            self.lbl_imager_status.setText(f"✓ Detected on this PC: <code>{self._imager_path}</code>")
            self.btn_launch_imager.setVisible(True)
            self.btn_download_imager.setObjectName("")
            self.btn_download_imager.setText("🌐 Download Update")
        else:
            self.lbl_imager_status.setStyleSheet("color: #FBBF24;")
            self.lbl_imager_status.setText("Raspberry Pi Imager was not detected on this PC. Download and install it first:")
            self.btn_launch_imager.setVisible(False)
            self.btn_download_imager.setObjectName("primaryBtn")
            self.btn_download_imager.setText("🌐 Download Raspberry Pi Imager (raspberrypi.com/software)")

    def _launch_imager(self) -> None:
        if self._imager_path:
            try:
                import ctypes

                rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", self._imager_path, None, None, 1)
                if rc <= 32:
                    raise OSError(f"ShellExecuteW failed with code {rc}")
            except Exception as exc:
                QMessageBox.warning(self, "Launch Failed", f"Could not launch Raspberry Pi Imager:\n{exc}")


class DiscoverPiPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Step 2: Auto-Detect Raspberry Pis on Local Network")
        self.setSubTitle("Select a detected Raspberry Pi below, or click Next to enter an IP address manually.")
        self._worker: NetworkScanWorker | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        top_row = QHBoxLayout()
        self.lbl_status = QLabel("Ready to scan local network.")
        self.btn_scan = QPushButton("🔍 Scan Network for Pis")
        self.btn_scan.setObjectName("primaryBtn")
        self.btn_scan.setFixedHeight(34)
        self.btn_scan.clicked.connect(self.start_scan)
        top_row.addWidget(self.lbl_status, 1)
        top_row.addWidget(self.btn_scan)
        layout.addLayout(top_row)

        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setFixedHeight(8)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.hide()
        layout.addWidget(self.scan_progress)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["IP Address", "MAC Address", "Device Info", "SSH (:22)", "Status"])
        hdr = self.table.horizontalHeader()
        hdr.setMinimumSectionSize(75)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_select)
        layout.addWidget(self.table)

        self.selected_ip_edit = QLineEdit()
        self.selected_ip_edit.setFixedHeight(34)
        self.selected_ip_edit.setPlaceholderText("Selected IP (or leave blank to enter manually on next step)")
        self.registerField("detected_ip", self.selected_ip_edit)
        row_sel = QHBoxLayout()
        row_sel.addWidget(QLabel("Selected Target IP:"))
        row_sel.addWidget(self.selected_ip_edit, 1)
        layout.addLayout(row_sel)

    def initializePage(self) -> None:
        if self.table.rowCount() == 0:
            self.start_scan()

    def start_scan(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self.btn_scan.setEnabled(False)
        self.btn_scan.setText("⏳ Scanning...")
        self.scan_progress.show()
        self.lbl_status.setText("⏳ Scanning local /24 subnet for Raspberry Pis (SSH :22, Web :80, RPi MACs)...")
        if self.table.rowCount() == 0:
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem("Scanning..."))
            self.table.setItem(0, 1, QTableWidgetItem("—"))
            self.table.setItem(0, 2, QTableWidgetItem("Searching local network for Raspberry Pis..."))
            self.table.setItem(0, 3, QTableWidgetItem("—"))
            self.table.setItem(0, 4, QTableWidgetItem("In Progress"))
        self._worker = NetworkScanWorker()
        self._worker.finished_scan.connect(self._on_scan_done)
        self._worker.failed_scan.connect(self._on_scan_failed)
        self._worker.start()

    def _on_scan_done(self, subnet: str, results: list[dict]) -> None:
        self.scan_progress.hide()
        self.btn_scan.setText("🔍 Scan Network for Pis")
        self.btn_scan.setEnabled(True)
        self.table.setRowCount(len(results))
        for r, item in enumerate(results):
            vals = [
                item["ip"],
                item["mac"],
                item["info"],
                "Open" if item["has_ssh"] else "Closed",
                item["status"],
            ]
            for c, txt in enumerate(vals):
                cell = QTableWidgetItem(txt)
                if item["is_givenergy"]:
                    cell.setForeground(QColor("#FBBF24" if item.get("upgrade_needed") else "#34D399"))
                elif item["is_rpi"] and item["has_ssh"]:
                    cell.setForeground(QColor("#60A5FA"))
                self.table.setItem(r, c, cell)
        self.lbl_status.setText(f"Scanned {subnet} — found {len(results)} candidate device(s).")
        if results and not self.selected_ip_edit.text().strip():
            self.table.selectRow(0)

    def _on_scan_failed(self, err: str) -> None:
        self.scan_progress.hide()
        self.btn_scan.setText("🔍 Scan Network for Pis")
        self.btn_scan.setEnabled(True)
        if self.table.rowCount() == 1 and self.table.item(0, 0) and self.table.item(0, 0).text() == "Scanning...":
            self.table.setRowCount(0)
        self.lbl_status.setText(f"Scan failed: {err}")

    def _on_select(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            ip = self.table.item(rows[0].row(), 0).text()
            if ip != "Scanning...":
                self.selected_ip_edit.setText(ip)


class CredentialsPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Step 3: Pi Credentials & Remote Access")
        self.setSubTitle("Confirm the target Raspberry Pi SSH login and optional Tailscale settings.")

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        row_h = 32

        grp_ssh = QGroupBox("Raspberry Pi SSH Connection")
        form = QGridLayout(grp_ssh)
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)
        form.setContentsMargins(14, 18, 14, 12)
        form.setColumnStretch(1, 1)

        self.inp_host = QLineEdit(os.environ.get("GIVENERGY_PI_HOST", ""))
        self.inp_host.setPlaceholderText("e.g. 192.168.0.243 or givenergy-pi.local")
        self.inp_host.setFixedHeight(row_h)
        self.inp_user = QLineEdit(os.environ.get("GIVENERGY_PI_USER", ""))
        self.inp_user.setPlaceholderText("Enter Raspberry Pi SSH username")
        self.inp_user.setFixedHeight(row_h)
        self.inp_pass = QLineEdit(os.environ.get("GIVENERGY_PI_PASS", ""))
        self.inp_pass.setPlaceholderText("Enter Raspberry Pi SSH password")
        self.inp_pass.setEchoMode(QLineEdit.Password)
        self.inp_pass.setFixedHeight(row_h)

        btn_show = QPushButton("Show")
        btn_show.setCheckable(True)
        btn_show.setFixedHeight(row_h)
        btn_show.toggled.connect(
            lambda chk: self.inp_pass.setEchoMode(QLineEdit.Normal if chk else QLineEdit.Password)
        )
        pw_row = QHBoxLayout()
        pw_row.setSpacing(8)
        pw_row.setContentsMargins(0, 0, 0, 0)
        pw_row.addWidget(self.inp_pass, 1)
        pw_row.addWidget(btn_show)

        self.btn_test = QPushButton("🔌 Test SSH Connection")
        self.btn_test.setFixedHeight(row_h)
        self.btn_test.clicked.connect(self._test_ssh)
        self.lbl_test = QLabel("")
        self.lbl_test.setWordWrap(True)
        self.lbl_test.setMinimumHeight(row_h)
        self.lbl_test.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        test_row = QHBoxLayout()
        test_row.setSpacing(10)
        test_row.setContentsMargins(0, 0, 0, 0)
        test_row.addWidget(self.btn_test)
        test_row.addWidget(self.lbl_test, 1)

        form.addWidget(QLabel("Pi IP / Hostname:"), 0, 0)
        form.addWidget(self.inp_host, 0, 1)
        form.addWidget(QLabel("SSH Username:"), 1, 0)
        form.addWidget(self.inp_user, 1, 1)
        form.addWidget(QLabel("SSH Password:"), 2, 0)
        form.addLayout(pw_row, 2, 1)
        form.addLayout(test_row, 3, 0, 1, 2)
        layout.addWidget(grp_ssh)

        grp_ts = QGroupBox("Tailscale Remote Access (Recommended for Phone && Off-Site Access)")
        ts_layout = QGridLayout(grp_ts)
        ts_layout.setVerticalSpacing(12)
        ts_layout.setHorizontalSpacing(12)
        ts_layout.setContentsMargins(14, 20, 14, 14)
        ts_layout.setColumnStretch(1, 1)

        self.chk_tailscale = QCheckBox("Install && configure Tailscale on this Pi")
        self.chk_tailscale.setChecked(True)

        lbl_ts_info = QLabel(
            "<b>What Tailscale is used for:</b> Enables <b>Tailscale Funnel</b> (HTTPS + Bearer Token) for remote access "
            "via browser or the <b>GivEnergy Control Android app</b> (<code>givenergy-local-control.apk</code>) without "
            "router port-forwarding or Tailscale on your phone.<br>"
            "Step 4 provides a <b>login.tailscale.com</b> link; pair the Android app via the QR code in <b>⚙ Property Setup</b>."
        )
        lbl_ts_info.setWordWrap(True)
        lbl_ts_info.setStyleSheet("color: #94A3B8; font-size: 11.5px;")

        ts_layout.addWidget(self.chk_tailscale, 0, 0, 1, 2)
        ts_layout.addWidget(lbl_ts_info, 1, 0, 1, 2)
        layout.addWidget(grp_ts)

        grp_store = QGroupBox("Storage Media && Filesystem Optimization")
        store_layout = QGridLayout(grp_store)
        store_layout.setVerticalSpacing(10)
        store_layout.setHorizontalSpacing(12)
        store_layout.setContentsMargins(14, 20, 14, 14)
        store_layout.setColumnStretch(1, 1)

        self.cmb_storage = QComboBox()
        self.cmb_storage.setFixedHeight(row_h)
        self.cmb_storage.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_storage.setMinimumContentsLength(28)
        self.cmb_storage.addItem("MicroSD Card (Wear-Protection: RAM logs, 60s DB batch, noatime)", "sd")
        self.cmb_storage.addItem("NVMe / USB SSD (High-Durability: Disk logs, 15s DB flush, fstrim, noatime)", "ssd")
        self.lbl_storage_hint = QLabel(
            "<b>Why this matters:</b> <b>MicroSD Card</b> keeps logs in RAM (60s DB batch) to reduce card wear; "
            "<b>NVMe / USB SSD</b> enables disk logs, 15s flush, and <code>fstrim</code>. "
            "Click <i>Test SSH Connection</i> above to auto-detect."
        )
        self.lbl_storage_hint.setStyleSheet("color: #94A3B8; font-size: 11.5px;")
        self.lbl_storage_hint.setWordWrap(True)
        self.chk_wipe_data = QCheckBox("Wipe existing telemetry history && saved hardware configuration on this Pi")
        self.chk_wipe_data.setChecked(False)
        store_layout.addWidget(QLabel("Boot Storage Type:"), 0, 0)
        store_layout.addWidget(self.cmb_storage, 0, 1)
        store_layout.addWidget(self.lbl_storage_hint, 1, 0, 1, 2)
        store_layout.addWidget(self.chk_wipe_data, 2, 0, 1, 2)
        layout.addWidget(grp_store)

        self.registerField("pi_host*", self.inp_host)
        self.registerField("pi_user*", self.inp_user)
        self.registerField("pi_pass*", self.inp_pass)
        self.registerField("install_ts", self.chk_tailscale)
        self.registerField("wipe_data", self.chk_wipe_data)
        layout.addStretch()

    def initializePage(self) -> None:
        detected = self.field("detected_ip")
        if detected and str(detected).strip():
            self.inp_host.setText(str(detected).strip())
        self.updateGeometry()
        if self.layout() is not None:
            self.layout().activate()

    def _test_ssh(self) -> None:
        host = self.inp_host.text().strip()
        user = self.inp_user.text().strip()
        pw = self.inp_pass.text()
        self.lbl_test.setText("Connecting...")
        QApplication.processEvents()
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(host, username=user, password=pw, timeout=6.0)
            _, o, _ = ssh.exec_command(
                "cat /proc/device-tree/model 2>/dev/null | tr -d '\\0'; echo ''; "
                "python3 --version; findmnt -n -o SOURCE / 2>/dev/null"
            )
            lines = [l.strip() for l in o.read().decode("utf-8", errors="replace").splitlines() if l.strip()]
            root_dev = lines[-1] if len(lines) >= 3 else ""
            info = " • ".join(lines[:2]) if len(lines) >= 2 else " • ".join(lines)
            if root_dev:
                if "nvme" in root_dev or "/dev/sd" in root_dev:
                    self.cmb_storage.setCurrentIndex(1)
                    self.lbl_storage_hint.setText(f"Auto-detected SSD/Drive root ({root_dev}) — selected NVMe / USB SSD mode.")
                elif "mmcblk" in root_dev:
                    self.cmb_storage.setCurrentIndex(0)
                    self.lbl_storage_hint.setText(f"Auto-detected MicroSD root ({root_dev}) — selected Wear-Protection mode.")
            self.lbl_test.setStyleSheet("color: #34D399;")
            self.lbl_test.setText(f"✓ Connected! {info}")
        except Exception as exc:
            self.lbl_test.setStyleSheet("color: #FB7185;")
            self.lbl_test.setText(f"✗ Failed: {exc}")
        finally:
            ssh.close()


class DeployProgressPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Step 4: Provisioning & Installing GivEnergy Web Service")
        self.setSubTitle("Configuring SD-safe RAM logging, Python environment, Tailscale, and systemd service.")
        self._worker: DeployWorker | None = None
        self._done_ok = False
        self._ts_url = ""

        layout = QVBoxLayout(self)
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 100)
        layout.addWidget(self.pbar)

        self.btn_ts_login = QPushButton("🔗 Open Tailscale Login Link in Browser")
        self.btn_ts_login.setObjectName("primaryBtn")
        self.btn_ts_login.setVisible(False)
        self.btn_ts_login.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self._ts_url)))
        layout.addWidget(self.btn_ts_login)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box, 1)

        self.btn_retry = QPushButton("↻ Retry Deployment")
        self.btn_retry.setVisible(False)
        self.btn_retry.clicked.connect(self.start_deploy)
        layout.addWidget(self.btn_retry)

    def isComplete(self) -> bool:
        return self._done_ok

    def initializePage(self) -> None:
        self._done_ok = False
        self.btn_ts_login.setVisible(False)
        self.btn_retry.setVisible(False)
        self.log_box.clear()
        self.pbar.setValue(0)
        self.completeChanged.emit()
        self.start_deploy()

    def start_deploy(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self.btn_retry.setVisible(False)
        cred_page = self.wizard().page(2)
        storage_mode = cred_page.cmb_storage.currentData() if hasattr(cred_page, "cmb_storage") else "sd"
        self._worker = DeployWorker(
            host=str(self.field("pi_host")).strip(),
            user=str(self.field("pi_user")).strip(),
            password=str(self.field("pi_pass")),
            install_tailscale=bool(self.field("install_ts")),
            tailscale_authkey="",
            storage_mode=storage_mode,
            wipe_existing_data=bool(self.field("wipe_data")),
        )
        self._worker.log_line.connect(self.log_box.append)
        self._worker.progress.connect(self.pbar.setValue)
        self._worker.tailscale_url.connect(self._on_ts_url)
        self._worker.finished_deploy.connect(self._on_finished)
        self._worker.start()

    def _on_ts_url(self, url: str) -> None:
        self._ts_url = url
        self.btn_ts_login.setText(f"🔗 Click to Link Pi to Tailscale: {url}")
        self.btn_ts_login.setVisible(True)

    def _on_finished(self, ok: bool, local_url: str, ts_ip: str, ts_dns: str) -> None:
        self._done_ok = ok
        self.wizard().deployed_local_url = local_url
        self.wizard().deployed_ts_ip = ts_ip
        self.wizard().deployed_ts_dns = ts_dns
        self.wizard().deployed_ts_auth_url = self._ts_url
        if not ok:
            self.btn_retry.setVisible(True)
        self.completeChanged.emit()


class FinishPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Deployment Complete!")
        self.setSubTitle("Your Raspberry Pi is now running the GivEnergy Local Control & Historic Telemetry service.")

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        self.lbl_summary = QLabel()
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setTextFormat(Qt.RichText)
        self.lbl_summary.setOpenExternalLinks(True)
        layout.addWidget(self.lbl_summary)

        self.btn_open = QPushButton("🌐 Open Deployed Site && Dashboard")
        self.btn_open.setObjectName("successBtn")
        self.btn_open.setFixedHeight(38)
        self.btn_open.clicked.connect(lambda: self._open_site())
        layout.addWidget(self.btn_open)

        self.btn_setup_tab = QPushButton("⚙ Open Directly to Property Setup (Auto-Discover Hardware)")
        self.btn_setup_tab.setObjectName("primaryBtn")
        self.btn_setup_tab.setFixedHeight(38)
        self.btn_setup_tab.clicked.connect(lambda: self._open_site("settings"))
        layout.addWidget(self.btn_setup_tab)
        layout.addStretch()

    def initializePage(self) -> None:
        w = self.wizard()
        local_url = getattr(w, "deployed_local_url", "")
        ts_ip = getattr(w, "deployed_ts_ip", "")
        ts_dns = getattr(w, "deployed_ts_dns", "")
        ts_auth = getattr(w, "deployed_ts_auth_url", "")

        lines = [
            f"<b>Local LAN Dashboard:</b> <a style='color:#60A5FA;' href='{local_url}'>{local_url}</a>",
        ]
        if ts_ip:
            lines.append(f"<b>Tailscale IP (Remote):</b> <a style='color:#34D399;' href='http://{ts_ip}'>http://{ts_ip}</a>")
        if ts_dns:
            lines.append(f"<b>Tailscale MagicDNS:</b> <a style='color:#34D399;' href='http://{ts_dns}'>http://{ts_dns}</a>")
            lines.append(
                f"<b>Tailscale Funnel (Public HTTPS + Bearer Auth):</b> "
                f"<a style='color:#38BDF8;' href='https://{ts_dns}'>https://{ts_dns}</a>"
            )
        if ts_auth and not ts_ip:
            lines.append(
                f"<b>Tailscale Pending Approval:</b> <a style='color:#FBBF24;' href='{ts_auth}'>{ts_auth}</a>"
            )
        lines.append(
            "<br><b>Next Step:</b> Open <b>⚙ Property Setup</b> below — your local network will be scanned "
            "automatically and any GivEnergy Inverter (<code>:8899</code>) or EV Charger (<code>:502</code>) "
            "found will be configured for you. You can also scan the <b>Android App Pairing QR Code</b> there "
            "to connect <code>givenergy-local-control.apk</code>."
        )
        self.lbl_summary.setText("<br>".join(lines))

    def _open_site(self, tab: str = "") -> None:
        url = getattr(self.wizard(), "deployed_local_url", "").rstrip("/")
        if not url:
            return
        if tab:
            url = f"{url}/?tab={tab}"
        QDesktopServices.openUrl(QUrl(url))


def _app_icon() -> QIcon:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for rel in ("givenergy_web/static/icon.ico", "givenergy_web/static/icon-512.png"):
        p = base / rel
        if p.exists():
            return QIcon(str(p))
    return QIcon()


class GivEnergyDeploymentWizard(QWizard):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"GivEnergy Raspberry Pi Deployment Wizard v{APP_VERSION}")
        self.setWindowIcon(_app_icon())
        self.setWizardStyle(QWizard.ClassicStyle)
        self.resize(860, 780)
        self.setMinimumSize(840, 750)
        self.deployed_local_url = ""
        self.deployed_ts_ip = ""
        self.deployed_ts_dns = ""
        self.deployed_ts_auth_url = ""

        self.addPage(OSPrepPage())
        self.addPage(DiscoverPiPage())
        self.addPage(CredentialsPage())
        self.addPage(DeployProgressPage())
        self.addPage(FinishPage())
        self.currentIdChanged.connect(self._hide_top_back_arrow)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._hide_top_back_arrow()

    def _hide_top_back_arrow(self) -> None:
        for btn in self.findChildren(QAbstractButton):
            if btn.metaObject().className() == "QAbstractButton" and not btn.text() and not btn.objectName():
                btn.setFixedSize(0, 0)
                btn.hide()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(_app_icon())
    app.setStyleSheet(WIZARD_STYLESHEET)
    wiz = GivEnergyDeploymentWizard()
    wiz.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
