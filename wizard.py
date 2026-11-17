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
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

import deploy_to_pi
import setup_pi
from givenergy_web import __version__ as APP_VERSION
from givenergy_web.tariffs import (
    UK_GSP_REGIONS,
    UK_TARIFF_PRESETS,
    fetch_octopus_product_summary,
    fetch_octopus_products,
    lookup_gsp_by_postcode,
)

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
QLabel, QCheckBox, QRadioButton {
    background-color: transparent;
}
QGroupBox {
    background-color: #111827;
    border: 1px solid #1F2A40;
    border-radius: 8px;
    margin-top: 4px;
    padding: 28px 12px 12px 12px;
    font-weight: 700;
    color: #93C5FD;
}
QGroupBox::title {
    subcontrol-origin: padding;
    subcontrol-position: top left;
    left: 14px;
    top: 8px;
    padding: 0px;
    background-color: transparent;
}
QGroupBox:disabled {
    background-color: #0D1320;
    border-color: #162033;
    color: #475569;
}
QLabel:disabled {
    color: #475569;
}
QLineEdit, QComboBox, QDoubleSpinBox {
    background-color: #090D16;
    color: #F8FAFC;
    border: 1px solid #2E3F5E;
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 22px;
    font-size: 13px;
}
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {
    background-color: #0D1320;
    color: #475569;
    border-color: #1E293B;
}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
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
QPushButton:disabled {
    background-color: #0D1320;
    color: #475569;
    border-color: #1E293B;
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
        tariff_config_dict: dict | None = None,
        solar_forecast_config_dict: dict | None = None,
    ) -> None:
        super().__init__()
        self.host = host
        self.user = user
        self.password = password
        self.install_tailscale = install_tailscale
        self.tailscale_authkey = tailscale_authkey
        self.storage_mode = storage_mode
        self.wipe_existing_data = wipe_existing_data
        self.tariff_config_dict = tariff_config_dict
        self.solar_forecast_config_dict = solar_forecast_config_dict

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

            if self.tariff_config_dict or self.solar_forecast_config_dict:
                ssh_t = paramiko.SSHClient()
                ssh_t.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                try:
                    ssh_t.connect(self.host, username=self.user, password=self.password, timeout=8.0)
                    sftp = ssh_t.open_sftp()
                    if self.tariff_config_dict:
                        self.log_line.emit("  Uploading pre-configured UK energy tariff settings...")
                        remote_tariff_path = f"{deploy_to_pi.REMOTE_ROOT}/data/tariff_config.json"
                        with sftp.open(remote_tariff_path, "w") as f:
                            f.write(json.dumps(self.tariff_config_dict, indent=2))
                    if self.solar_forecast_config_dict:
                        self.log_line.emit("  Uploading pre-configured Open-Meteo solar forecast settings...")
                        remote_sf_path = f"{deploy_to_pi.REMOTE_ROOT}/data/solar_forecast_config.json"
                        with sftp.open(remote_sf_path, "w") as f:
                            f.write(json.dumps(self.solar_forecast_config_dict, indent=2))
                    sftp.close()
                    self._run_remote(ssh_t, "systemctl restart givenergy-web", sudo=True)
                    self.log_line.emit("  [OK] Pre-configured settings saved and service restarted.")
                finally:
                    ssh_t.close()

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
        self.setSubTitle("Select a detected Raspberry Pi below, or enter an IP address manually to continue.")
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
        self.selected_ip_edit.setPlaceholderText("Selected IP (or enter an IP address manually)")
        self.registerField("detected_ip", self.selected_ip_edit)
        self.selected_ip_edit.textChanged.connect(lambda: self.completeChanged.emit())
        row_sel = QHBoxLayout()
        row_sel.addWidget(QLabel("Selected Target IP:"))
        row_sel.addWidget(self.selected_ip_edit, 1)
        layout.addLayout(row_sel)

    def isComplete(self) -> bool:
        return bool(self.selected_ip_edit.text().strip())

    def initializePage(self) -> None:
        self.completeChanged.emit()
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


def _wizard_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "GivEnergyLocalControl" / "wizard.config"
    return Path.home() / ".config" / "givenergy-local-control" / "wizard.config"


def _dpapi_protect(plaintext: str) -> str:
    if not plaintext or sys.platform != "win32":
        return ""
    import base64
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    raw = plaintext.encode("utf-8")
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), "GivEnergyWizard", None, None, None, 0, ctypes.byref(blob_out)
    ):
        return ""
    try:
        out_bytes = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        return "dpapi:" + base64.b64encode(out_bytes).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(token: str) -> str:
    if not token or not token.startswith("dpapi:") or sys.platform != "win32":
        return ""
    import base64
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    try:
        raw = base64.b64decode(token[6:].encode("ascii"))
    except Exception:
        return ""
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        return ""
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8", errors="replace")
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _load_wizard_config() -> dict:
    p = _wizard_config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        if data.get("pi_pass_dpapi"):
            data["pi_pass"] = _dpapi_unprotect(str(data["pi_pass_dpapi"]))
        return data
    except Exception:
        return {}


def _save_wizard_config(
    host: str,
    user: str,
    password: str,
    install_tailscale: bool,
    storage_mode: str,
    remember: bool,
) -> None:
    p = _wizard_config_path()
    existing = _load_wizard_config()
    if not remember:
        # Clear Pi credentials but preserve optional Tariff & Solar Forecast preferences
        for k in ("pi_host", "pi_user", "pi_pass_dpapi", "pi_pass", "install_tailscale", "storage_mode"):
            existing.pop(k, None)
        if existing:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        elif p.exists():
            try:
                p.unlink()
            except Exception:
                pass
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    existing.pop("pi_pass", None)
    existing.update(
        {
            "pi_host": host.strip(),
            "pi_user": user.strip(),
            "pi_pass_dpapi": _dpapi_protect(password),
            "install_tailscale": bool(install_tailscale),
            "storage_mode": storage_mode,
        }
    )
    p.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def _update_wizard_config(updates: dict) -> None:
    p = _wizard_config_path()
    existing = _load_wizard_config()
    existing.pop("pi_pass", None)
    existing.update(updates)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(existing, indent=2), encoding="utf-8")


class CredentialsPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Step 5: Pi Credentials & Remote Access")
        self.setSubTitle("Confirm the target Raspberry Pi SSH login and optional Tailscale settings.")
        saved = _load_wizard_config()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        row_h = 32

        grp_ssh = QGroupBox("Raspberry Pi SSH Connection")
        form = QGridLayout(grp_ssh)
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)
        form.setContentsMargins(14, 18, 14, 12)
        form.setColumnStretch(1, 1)

        self.inp_host = QLineEdit(os.environ.get("GIVENERGY_PI_HOST", "") or str(saved.get("pi_host", "")))
        self.inp_host.setPlaceholderText("e.g. 192.168.0.243 or givenergy-pi.local")
        self.inp_host.setFixedHeight(row_h)
        self.inp_user = QLineEdit(os.environ.get("GIVENERGY_PI_USER", "") or str(saved.get("pi_user", "")))
        self.inp_user.setPlaceholderText("Enter Raspberry Pi SSH username")
        self.inp_user.setFixedHeight(row_h)
        self.inp_pass = QLineEdit(os.environ.get("GIVENERGY_PI_PASS", "") or str(saved.get("pi_pass", "")))
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

        self.chk_remember = QCheckBox("Remember SSH credentials securely on this PC (Windows DPAPI encrypted)")
        self.chk_remember.setChecked(True)

        form.addWidget(QLabel("Pi IP / Hostname:"), 0, 0)
        form.addWidget(self.inp_host, 0, 1)
        form.addWidget(QLabel("SSH Username:"), 1, 0)
        form.addWidget(self.inp_user, 1, 1)
        form.addWidget(QLabel("SSH Password:"), 2, 0)
        form.addLayout(pw_row, 2, 1)
        form.addWidget(self.chk_remember, 3, 0, 1, 2)
        form.addLayout(test_row, 4, 0, 1, 2)
        layout.addWidget(grp_ssh)

        grp_ts = QGroupBox("Tailscale Remote Access (Recommended for Phone && Off-Site Access)")
        ts_layout = QGridLayout(grp_ts)
        ts_layout.setVerticalSpacing(12)
        ts_layout.setHorizontalSpacing(12)
        ts_layout.setContentsMargins(14, 20, 14, 14)
        ts_layout.setColumnStretch(1, 1)

        self.chk_tailscale = QCheckBox("Install && configure Tailscale on this Pi")
        self.chk_tailscale.setChecked(bool(saved.get("install_tailscale", True)))

        lbl_ts_info = QLabel(
            "<b>What Tailscale is used for:</b> Enables <b>Tailscale Funnel</b> (HTTPS + Bearer Token) for remote access "
            "via browser or the <b>GivEnergy Control Android app</b> (<code>givenergy-local-control.apk</code>) without "
            "router port-forwarding or Tailscale on your phone.<br>"
            "Step 5 provides a <b>login.tailscale.com</b> link; pair the Android app via the QR code in <b>⚙ Property Setup</b>."
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
        if saved.get("storage_mode") == "ssd":
            self.cmb_storage.setCurrentIndex(1)
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

        self.registerField("pi_host", self.inp_host)
        self.registerField("pi_user", self.inp_user)
        self.registerField("pi_pass", self.inp_pass)
        self.registerField("install_ts", self.chk_tailscale)
        self.registerField("wipe_data", self.chk_wipe_data)
        self.inp_host.textChanged.connect(lambda: self.completeChanged.emit())
        self.inp_user.textChanged.connect(lambda: self.completeChanged.emit())
        self.inp_pass.textChanged.connect(lambda: self.completeChanged.emit())
        layout.addStretch()

    def isComplete(self) -> bool:
        return bool(self.inp_host.text().strip() and self.inp_user.text().strip() and self.inp_pass.text())

    def validatePage(self) -> bool:
        _save_wizard_config(
            host=self.inp_host.text(),
            user=self.inp_user.text(),
            password=self.inp_pass.text(),
            install_tailscale=self.chk_tailscale.isChecked(),
            storage_mode=self.cmb_storage.currentData() or "sd",
            remember=self.chk_remember.isChecked(),
        )
        return super().validatePage()

    def initializePage(self) -> None:
        detected = self.field("detected_ip")
        if detected and str(detected).strip():
            self.inp_host.setText(str(detected).strip())
        self.completeChanged.emit()
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


class _OctopusFetchWorker(QThread):
    loaded = Signal(dict)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.loaded.emit(fetch_octopus_products())
        except Exception as exc:
            self.failed.emit(str(exc))


class _PostcodeWorker(QThread):
    resolved = Signal(str)
    failed = Signal(str)

    def __init__(self, postcode: str) -> None:
        super().__init__()
        self.postcode = postcode

    def run(self) -> None:
        try:
            self.resolved.emit(lookup_gsp_by_postcode(self.postcode))
        except Exception as exc:
            self.failed.emit(str(exc))


class _OctopusRateWorker(QThread):
    loaded = Signal(str, dict)
    failed = Signal(str, str)

    def __init__(self, side: str, product_code: str, region: str) -> None:
        super().__init__()
        self.side = side
        self.product_code = product_code
        self.region = region

    def run(self) -> None:
        try:
            res = fetch_octopus_product_summary(self.product_code, self.region)
            self.loaded.emit(self.side, res)
        except Exception as exc:
            self.failed.emit(self.side, str(exc))


class TariffSetupPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Step 3: Energy Tariff & Cost Monitoring (Optional)")
        self.setSubTitle("Pre-configure your UK import & export tariff, or leave unchecked to keep existing Pi settings / configure later.")
        self._octopus_data: dict | None = None
        self._oct_worker: _OctopusFetchWorker | None = None
        self._pc_worker: _PostcodeWorker | None = None
        self._rate_workers: dict[str, _OctopusRateWorker] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.chk_enable = QCheckBox("Configure / overwrite UK Energy Tariff settings on this Pi")
        self.chk_enable.setChecked(False)
        self.chk_enable.toggled.connect(self._on_toggle_enable)
        layout.addWidget(self.chk_enable)

        self.container = QWidget()
        self.container.setEnabled(False)
        self._opacity_fx = QGraphicsOpacityEffect(self.container)
        self._opacity_fx.setOpacity(0.38)
        self.container.setGraphicsEffect(self._opacity_fx)
        cl = QVBoxLayout(self.container)
        cl.setContentsMargins(0, 0, 4, 0)
        cl.setSpacing(6)

        row_h = 28

        # Region & Currency (DNO/GSP applies to Octopus Live API regional pricing)
        grp_reg = QGroupBox("Currency && UK Region (Octopus Live API)")
        rl = QGridLayout(grp_reg)
        rl.setVerticalSpacing(6)
        rl.setHorizontalSpacing(10)
        rl.setContentsMargins(12, 8, 12, 8)
        rl.setColumnMinimumWidth(0, 170)
        rl.setColumnStretch(1, 1)

        self.lbl_region = QLabel("DNO / GSP Region:")
        self.cmb_region = QComboBox()
        self.cmb_region.setFixedHeight(row_h)
        for code, name in UK_GSP_REGIONS.items():
            self.cmb_region.addItem(f"{code} — {name}", code)
        idx_h = self.cmb_region.findData("H")
        if idx_h >= 0:
            self.cmb_region.setCurrentIndex(idx_h)
        self.cmb_region.currentIndexChanged.connect(self._on_region_changed)

        self.lbl_postcode = QLabel("Postcode Lookup:")
        self.inp_postcode = QLineEdit()
        self.inp_postcode.setFixedHeight(row_h)
        self.inp_postcode.setPlaceholderText("e.g. SW1A 1AA")
        self.btn_lookup = QPushButton("Lookup Region")
        self.btn_lookup.setFixedHeight(row_h)
        self.btn_lookup.clicked.connect(self._lookup_postcode)
        self.lbl_pc_status = QLabel("")
        self.lbl_pc_status.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.lbl_pc_status.setVisible(False)

        self.cmb_currency = QComboBox()
        self.cmb_currency.setFixedHeight(row_h)
        self.cmb_currency.addItem("£ (p)", ("£", "p"))
        self.cmb_currency.addItem("€ (c)", ("€", "c"))
        self.cmb_currency.addItem("$ (¢)", ("$", "¢"))

        cur_row = QHBoxLayout()
        cur_row.setSpacing(8)
        cur_row.addWidget(self.cmb_region, 1)
        cur_row.addWidget(QLabel("Currency:"))
        cur_row.addWidget(self.cmb_currency)

        rl.addWidget(self.lbl_region, 0, 0)
        rl.addLayout(cur_row, 0, 1, 1, 2)
        rl.addWidget(self.lbl_postcode, 1, 0)
        rl.addWidget(self.inp_postcode, 1, 1)
        rl.addWidget(self.btn_lookup, 1, 2)
        rl.addWidget(self.lbl_pc_status, 2, 1, 1, 2)
        cl.addWidget(grp_reg)

        # Import Tariff (includes Standing Charge & Dual Rate)
        grp_imp = QGroupBox("Import Tariff (Grid In)")
        il = QGridLayout(grp_imp)
        il.setVerticalSpacing(6)
        il.setHorizontalSpacing(10)
        il.setContentsMargins(12, 8, 12, 8)
        il.setColumnMinimumWidth(0, 170)
        il.setColumnStretch(1, 1)

        self.cmb_imp_mode = QComboBox()
        self.cmb_imp_mode.setFixedHeight(row_h)
        self.cmb_imp_mode.addItem("Custom Flat Rate", "custom_flat")
        self.cmb_imp_mode.addItem("Custom Dual Rate (Day / Off-Peak)", "custom_dual")
        self.cmb_imp_mode.addItem("UK Supplier Preset (E.ON, EDF, BG, OVO, Octopus)", "preset")
        self.cmb_imp_mode.addItem("Octopus Energy (Live Half-Hourly API)", "octopus_api")
        self.cmb_imp_mode.currentIndexChanged.connect(lambda: self._on_mode_change("imp"))

        self.lbl_imp_plan = QLabel("Plan / Product:")
        self.cmb_imp_plan = QComboBox()
        self.cmb_imp_plan.setFixedHeight(row_h)
        self.cmb_imp_plan.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_imp_plan.setMinimumContentsLength(24)
        self.cmb_imp_plan.currentIndexChanged.connect(lambda: self._on_plan_change("imp"))

        self.lbl_imp_rate = QLabel("Day / Flat Rate (p/kWh):")
        self.inp_imp_rate = QLineEdit("24.50")
        self.inp_imp_rate.setFixedHeight(row_h)

        self.lbl_imp_offpeak = QLabel("Off-Peak Rate && Window:")
        self.wid_imp_offpeak = QWidget()
        self.wid_imp_offpeak.setFixedHeight(row_h)
        opl = QHBoxLayout(self.wid_imp_offpeak)
        opl.setContentsMargins(0, 0, 0, 0)
        opl.setSpacing(6)
        self.inp_imp_offpeak_rate = QLineEdit("7.00")
        self.inp_imp_offpeak_rate.setFixedSize(72, row_h)
        self.inp_imp_start = QLineEdit("00:00")
        self.inp_imp_start.setFixedSize(64, row_h)
        self.inp_imp_end = QLineEdit("07:00")
        self.inp_imp_end.setFixedSize(64, row_h)
        opl.addWidget(self.inp_imp_offpeak_rate)
        opl.addWidget(QLabel("p  from"))
        opl.addWidget(self.inp_imp_start)
        opl.addWidget(QLabel("to"))
        opl.addWidget(self.inp_imp_end)
        opl.addStretch()

        self.inp_standing = QLineEdit("60.00")
        self.inp_standing.setFixedHeight(row_h)
        self.inp_imp_desc = QLineEdit()
        self.inp_imp_desc.setFixedHeight(row_h)
        self.inp_imp_desc.setPlaceholderText("Optional description (shown on dashboard)")

        il.addWidget(QLabel("Source:"), 0, 0)
        il.addWidget(self.cmb_imp_mode, 0, 1)
        il.addWidget(self.lbl_imp_plan, 1, 0)
        il.addWidget(self.cmb_imp_plan, 1, 1)
        il.addWidget(self.lbl_imp_rate, 2, 0)
        il.addWidget(self.inp_imp_rate, 2, 1)
        il.addWidget(self.lbl_imp_offpeak, 3, 0)
        il.addWidget(self.wid_imp_offpeak, 3, 1)
        il.addWidget(QLabel("Standing Charge (p/day):"), 4, 0)
        il.addWidget(self.inp_standing, 4, 1)
        il.addWidget(QLabel("Description:"), 5, 0)
        il.addWidget(self.inp_imp_desc, 5, 1)
        cl.addWidget(grp_imp)

        # Export Tariff
        grp_exp = QGroupBox("Export Tariff (Solar / Grid Out)")
        el = QGridLayout(grp_exp)
        el.setVerticalSpacing(6)
        el.setHorizontalSpacing(10)
        el.setContentsMargins(12, 8, 12, 8)
        el.setColumnMinimumWidth(0, 170)
        el.setColumnStretch(1, 1)

        self.cmb_exp_mode = QComboBox()
        self.cmb_exp_mode.setFixedHeight(row_h)
        self.cmb_exp_mode.addItem("Custom Flat Rate", "custom_flat")
        self.cmb_exp_mode.addItem("UK Supplier Preset (Octopus, E.ON, BG, EDF, SEG)", "preset")
        self.cmb_exp_mode.addItem("Octopus Energy (Live API)", "octopus_api")
        self.cmb_exp_mode.setCurrentIndex(1)  # Default to UK Supplier Preset (Octopus Outgoing Fixed)
        self.cmb_exp_mode.currentIndexChanged.connect(lambda: self._on_mode_change("exp"))

        self.lbl_exp_plan = QLabel("Plan / Product:")
        self.cmb_exp_plan = QComboBox()
        self.cmb_exp_plan.setFixedHeight(row_h)
        self.cmb_exp_plan.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_exp_plan.setMinimumContentsLength(24)
        self.cmb_exp_plan.currentIndexChanged.connect(lambda: self._on_plan_change("exp"))
        self.lbl_exp_rate = QLabel("Export Rate (p/kWh):")
        self.inp_exp_rate = QLineEdit("15.00")
        self.inp_exp_rate.setFixedHeight(row_h)
        self.inp_exp_desc = QLineEdit()
        self.inp_exp_desc.setFixedHeight(row_h)
        self.inp_exp_desc.setPlaceholderText("Optional description (shown on dashboard)")

        el.addWidget(QLabel("Source:"), 0, 0)
        el.addWidget(self.cmb_exp_mode, 0, 1)
        el.addWidget(self.lbl_exp_plan, 1, 0)
        el.addWidget(self.cmb_exp_plan, 1, 1)
        el.addWidget(self.lbl_exp_rate, 2, 0)
        el.addWidget(self.inp_exp_rate, 2, 1)
        el.addWidget(QLabel("Description:"), 3, 0)
        el.addWidget(self.inp_exp_desc, 3, 1)
        cl.addWidget(grp_exp)
        cl.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.container)
        layout.addWidget(scroll, 1)

        self._on_mode_change("imp")
        self._on_mode_change("exp")
        self._on_toggle_enable(False)
        self._load_saved_tariff_setup()

    def _load_saved_tariff_setup(self) -> None:
        saved = _load_wizard_config().get("tariff_setup")
        if not isinstance(saved, dict):
            return
        self._saved_imp_plan_key = str(saved.get("imp_plan_key", ""))
        self._saved_exp_plan_key = str(saved.get("exp_plan_key", ""))
        if "region" in saved:
            idx = self.cmb_region.findData(saved["region"])
            if idx >= 0:
                self.cmb_region.setCurrentIndex(idx)
        if "postcode" in saved:
            self.inp_postcode.setText(str(saved["postcode"]))
        if "currency_symbol" in saved:
            for i in range(self.cmb_currency.count()):
                d = self.cmb_currency.itemData(i)
                if d and d[0] == saved["currency_symbol"]:
                    self.cmb_currency.setCurrentIndex(i)
                    break
        if "standing_charge_p" in saved:
            self.inp_standing.setText(str(saved["standing_charge_p"]))
        for side, cmb_m, cmb_p, inp_r, inp_d in (
            ("imp", self.cmb_imp_mode, self.cmb_imp_plan, self.inp_imp_rate, self.inp_imp_desc),
            ("exp", self.cmb_exp_mode, self.cmb_exp_plan, self.inp_exp_rate, self.inp_exp_desc),
        ):
            mode_val = saved.get(f"{side}_mode")
            if mode_val:
                idx = cmb_m.findData(mode_val)
                if idx >= 0:
                    cmb_m.setCurrentIndex(idx)
            plan_key = saved.get(f"{side}_plan_key", "")
            if plan_key:
                for i in range(cmb_p.count()):
                    pd = cmb_p.itemData(i)
                    if isinstance(pd, dict) and (pd.get("id") == plan_key or pd.get("code") == plan_key):
                        cmb_p.setCurrentIndex(i)
                        break
            if f"{side}_rate" in saved:
                inp_r.setText(str(saved[f"{side}_rate"]))
            if f"{side}_desc" in saved:
                inp_d.setText(str(saved[f"{side}_desc"]))
        if "imp_offpeak_rate" in saved:
            self.inp_imp_offpeak_rate.setText(str(saved["imp_offpeak_rate"]))
        if "imp_start" in saved:
            self.inp_imp_start.setText(str(saved["imp_start"]))
        if "imp_end" in saved:
            self.inp_imp_end.setText(str(saved["imp_end"]))
        self.chk_enable.setChecked(bool(saved.get("enabled", False)))

    def validatePage(self) -> bool:
        def _plan_key(cmb: QComboBox) -> str:
            d = cmb.currentData()
            return str(d.get("id") or d.get("code") or "") if isinstance(d, dict) else ""

        cur, _ = self.cmb_currency.currentData() or ("£", "p")
        _update_wizard_config(
            {
                "tariff_setup": {
                    "enabled": self.chk_enable.isChecked(),
                    "region": self.cmb_region.currentData() or "H",
                    "postcode": self.inp_postcode.text().strip().upper(),
                    "currency_symbol": cur,
                    "standing_charge_p": self.inp_standing.text().strip(),
                    "imp_mode": self.cmb_imp_mode.currentData() or "custom_flat",
                    "imp_plan_key": _plan_key(self.cmb_imp_plan),
                    "imp_rate": self.inp_imp_rate.text().strip(),
                    "imp_offpeak_rate": self.inp_imp_offpeak_rate.text().strip(),
                    "imp_start": self.inp_imp_start.text().strip(),
                    "imp_end": self.inp_imp_end.text().strip(),
                    "imp_desc": self.inp_imp_desc.text().strip(),
                    "exp_mode": self.cmb_exp_mode.currentData() or "preset",
                    "exp_plan_key": _plan_key(self.cmb_exp_plan),
                    "exp_rate": self.inp_exp_rate.text().strip(),
                    "exp_desc": self.inp_exp_desc.text().strip(),
                }
            }
        )
        return super().validatePage()

    def _on_toggle_enable(self, checked: bool) -> None:
        self.container.setEnabled(checked)
        self.cmb_currency.setEnabled(checked)
        self._opacity_fx.setOpacity(1.0 if checked else 0.38)
        self._update_region_enabled()

    def _on_region_changed(self) -> None:
        if self.cmb_imp_mode.currentData() == "octopus_api":
            self._on_plan_change("imp")
        if self.cmb_exp_mode.currentData() == "octopus_api":
            self._on_plan_change("exp")

    def _populate_presets(self, side: str) -> None:
        key = "import" if side == "imp" else "export"
        cmb = self.cmb_imp_plan if side == "imp" else self.cmb_exp_plan
        cmb.blockSignals(True)
        cmb.clear()
        for p in UK_TARIFF_PRESETS[key]:
            cmb.addItem(f"{p['supplier']} — {p['name']}", p)
        cmb.blockSignals(False)
        self._on_plan_change(side)

    def _populate_octopus(self, side: str) -> None:
        cmb = self.cmb_imp_plan if side == "imp" else self.cmb_exp_plan
        cmb.blockSignals(True)
        cmb.clear()
        if not self._octopus_data:
            cmb.addItem("Fetching live Octopus products…", None)
            cmb.blockSignals(False)
            self._ensure_octopus_products()
            return
        key = "import_products" if side == "imp" else "export_products"
        for p in self._octopus_data.get(key, []):
            cmb.addItem(f"{p['display_name']} ({p['code']})", p)
        cmb.blockSignals(False)
        self._on_plan_change(side)

    def _ensure_octopus_products(self) -> None:
        if self._oct_worker and self._oct_worker.isRunning():
            return
        self._oct_worker = _OctopusFetchWorker()
        self._oct_worker.loaded.connect(self._on_octopus_loaded)
        self._oct_worker.failed.connect(lambda err: self.lbl_pc_status.setText(f"Octopus fetch failed: {err}"))
        self._oct_worker.start()

    def _on_octopus_loaded(self, data: dict) -> None:
        self._octopus_data = data
        if self.cmb_imp_mode.currentData() == "octopus_api":
            self._populate_octopus("imp")
        if self.cmb_exp_mode.currentData() == "octopus_api":
            self._populate_octopus("exp")

    def _on_mode_change(self, side: str) -> None:
        mode = (self.cmb_imp_mode if side == "imp" else self.cmb_exp_mode).currentData()
        cmb = self.cmb_imp_plan if side == "imp" else self.cmb_exp_plan
        lbl_plan = self.lbl_imp_plan if side == "imp" else self.lbl_exp_plan
        rate_inp = self.inp_imp_rate if side == "imp" else self.inp_exp_rate
        lbl_rate = self.lbl_imp_rate if side == "imp" else self.lbl_exp_rate

        show_plan = mode in ("preset", "octopus_api")
        lbl_plan.setVisible(show_plan)
        cmb.setVisible(show_plan)

        if side == "imp":
            self.lbl_imp_offpeak.setVisible(mode == "custom_dual")
            self.wid_imp_offpeak.setVisible(mode == "custom_dual")
            self.inp_standing.setReadOnly(mode == "octopus_api")

        rate_inp.setReadOnly(mode == "octopus_api")
        if mode == "octopus_api":
            lbl_rate.setText("Current Live Rate (p/kWh):" if side == "imp" else "Current Live Export (p/kWh):")
            self._populate_octopus(side)
        elif mode == "preset":
            lbl_rate.setText("Day / Default Rate (p/kWh):" if side == "imp" else "Export Rate (p/kWh):")
            self._populate_presets(side)
        elif mode == "custom_dual":
            lbl_rate.setText("Day Rate (p/kWh):")
        else:
            lbl_rate.setText("Flat Rate (p/kWh):" if side == "imp" else "Export Rate (p/kWh):")
        self._update_region_enabled()
        self.container.updateGeometry()
        if self.layout() is not None:
            self.layout().activate()

    def _update_region_enabled(self) -> None:
        uses_octopus = self.chk_enable.isChecked() and (
            self.cmb_imp_mode.currentData() == "octopus_api"
            or self.cmb_exp_mode.currentData() == "octopus_api"
        )
        for w in (self.lbl_region, self.cmb_region, self.lbl_postcode, self.inp_postcode, self.btn_lookup, self.lbl_pc_status):
            w.setEnabled(uses_octopus)

    def _on_plan_change(self, side: str) -> None:
        mode = (self.cmb_imp_mode if side == "imp" else self.cmb_exp_mode).currentData()
        cmb = self.cmb_imp_plan if side == "imp" else self.cmb_exp_plan
        p = cmb.currentData()
        if not isinstance(p, dict):
            return

        rate_inp = self.inp_imp_rate if side == "imp" else self.inp_exp_rate
        if mode == "preset":
            rate_inp.setText(f"{float(p.get('default_rate_p', 0.0)):.2f}")
            if side == "imp":
                if "standing_charge_p" in p:
                    self.inp_standing.setText(f"{float(p['standing_charge_p']):.2f}")
                wins = p.get("windows") or []
                has_win = len(wins) > 0
                self.lbl_imp_offpeak.setVisible(has_win)
                self.wid_imp_offpeak.setVisible(has_win)
                if has_win:
                    w = wins[0]
                    self.inp_imp_offpeak_rate.setText(f"{float(w.get('rate_p', 7.0)):.2f}")
                    self.inp_imp_start.setText(str(w.get("start", "00:00")))
                    self.inp_imp_end.setText(str(w.get("end", "07:00")))
                self.container.updateGeometry()
                if self.layout() is not None:
                    self.layout().activate()
        elif mode == "octopus_api":
            prod_code = str(p.get("code", ""))
            region = str(self.cmb_region.currentData() or "H")
            if not prod_code:
                return
            rate_inp.setText("Fetching…")
            if side == "imp":
                self.inp_standing.setText("Fetching…")
            worker = _OctopusRateWorker(side, prod_code, region)
            self._rate_workers[side] = worker
            worker.loaded.connect(self._on_octopus_rate_loaded)
            worker.failed.connect(self._on_octopus_rate_failed)
            worker.start()

    def _on_octopus_rate_loaded(self, side: str, info: dict) -> None:
        mode = (self.cmb_imp_mode if side == "imp" else self.cmb_exp_mode).currentData()
        if mode != "octopus_api":
            return
        rate_inp = self.inp_imp_rate if side == "imp" else self.inp_exp_rate
        cr = info.get("current_rate_p")
        rate_inp.setText(f"{float(cr):.2f}" if cr is not None else "Live API")
        if side == "imp":
            sc = info.get("standing_charge_p")
            if sc is not None:
                self.inp_standing.setText(f"{float(sc):.2f}")

    def _on_octopus_rate_failed(self, side: str, err: str) -> None:
        rate_inp = self.inp_imp_rate if side == "imp" else self.inp_exp_rate
        rate_inp.setText("Sync on Pi")
        if side == "imp" and self.inp_standing.text() == "Fetching…":
            self.inp_standing.setText("60.00")

    def _lookup_postcode(self) -> None:
        pc = self.inp_postcode.text().strip()
        if not pc:
            self.lbl_pc_status.setText("Enter a UK postcode first.")
            self.lbl_pc_status.setVisible(True)
            return
        self.btn_lookup.setEnabled(False)
        self.lbl_pc_status.setText("Looking up DNO region…")
        self.lbl_pc_status.setVisible(True)
        self._pc_worker = _PostcodeWorker(pc)
        self._pc_worker.resolved.connect(self._on_postcode_resolved)
        self._pc_worker.failed.connect(self._on_postcode_failed)
        self._pc_worker.start()

    def _on_postcode_resolved(self, region: str) -> None:
        self.btn_lookup.setEnabled(True)
        idx = self.cmb_region.findData(region)
        if idx >= 0:
            self.cmb_region.setCurrentIndex(idx)
        self.lbl_pc_status.setText(f"✓ Detected Region {region} — {UK_GSP_REGIONS.get(region, region)}")

    def _on_postcode_failed(self, err: str) -> None:
        self.btn_lookup.setEnabled(True)
        self.lbl_pc_status.setText(f"Lookup failed: {err}")

    def build_tariff_config(self) -> dict | None:
        if not self.chk_enable.isChecked():
            return None
        cur, sub = self.cmb_currency.currentData() or ("£", "p")
        try:
            standing = float(self.inp_standing.text().strip() or "60.00")
        except ValueError:
            standing = 60.0

        def _side(side: str) -> dict:
            mode = (self.cmb_imp_mode if side == "imp" else self.cmb_exp_mode).currentData()
            cmb = self.cmb_imp_plan if side == "imp" else self.cmb_exp_plan
            rate_inp = self.inp_imp_rate if side == "imp" else self.inp_exp_rate
            try:
                default_rate = float(rate_inp.text().strip() or "0")
            except ValueError:
                default_rate = 24.5 if side == "imp" else 15.0
            data = cmb.currentData()
            desc = (self.inp_imp_desc if side == "imp" else self.inp_exp_desc).text().strip()
            if mode == "octopus_api" and isinstance(data, dict):
                return {
                    "mode": "octopus_api",
                    "preset_id": "",
                    "label": f"{data.get('display_name', data.get('code', ''))}",
                    "description": desc,
                    "octopus_product_code": data.get("code", ""),
                    "octopus_tariff_code": "",
                    "default_rate_p": default_rate,
                    "windows": [],
                }
            if mode == "preset" and isinstance(data, dict):
                wins = list(data.get("windows", []))
                if side == "imp" and wins:
                    try:
                        op_rate = float(self.inp_imp_offpeak_rate.text().strip() or wins[0].get("rate_p", 7.0))
                    except ValueError:
                        op_rate = float(wins[0].get("rate_p", 7.0))
                    wins[0] = {
                        "start": self.inp_imp_start.text().strip() or wins[0].get("start", "00:00"),
                        "end": self.inp_imp_end.text().strip() or wins[0].get("end", "07:00"),
                        "rate_p": op_rate,
                        "label": wins[0].get("label", "Off-Peak"),
                    }
                return {
                    "mode": "preset",
                    "preset_id": data.get("id", ""),
                    "label": f"{data.get('supplier', '')} — {data.get('name', '')}",
                    "description": desc,
                    "octopus_product_code": "",
                    "octopus_tariff_code": "",
                    "default_rate_p": default_rate,
                    "windows": wins,
                }
            if mode == "custom_dual" and side == "imp":
                try:
                    op_rate = float(self.inp_imp_offpeak_rate.text().strip() or "7.0")
                except ValueError:
                    op_rate = 7.0
                return {
                    "mode": "custom",
                    "preset_id": "",
                    "label": "Custom Dual Rate",
                    "description": desc,
                    "octopus_product_code": "",
                    "octopus_tariff_code": "",
                    "default_rate_p": default_rate,
                    "windows": [
                        {
                            "start": self.inp_imp_start.text().strip() or "00:00",
                            "end": self.inp_imp_end.text().strip() or "07:00",
                            "rate_p": op_rate,
                            "label": "Off-Peak",
                        }
                    ],
                }
            return {
                "mode": "custom",
                "preset_id": "",
                "label": "Custom Flat Rate",
                "description": desc,
                "octopus_product_code": "",
                "octopus_tariff_code": "",
                "default_rate_p": default_rate,
                "windows": [],
            }

        return {
            "currency_symbol": cur,
            "sub_unit_symbol": sub,
            "region": self.cmb_region.currentData() or "H",
            "postcode": self.inp_postcode.text().strip().upper(),
            "standing_charge_p": standing,
            "import_tariff": _side("imp"),
            "export_tariff": _side("exp"),
            "last_octopus_sync": "",
        }


class SolarForecastSetupPage(QWizardPage):
    _AZIMUTH_PRESETS = [
        ("South (180°)", 180.0),
        ("South-East (135°)", 135.0),
        ("South-West (225°)", 225.0),
        ("East (90°)", 90.0),
        ("West (270°)", 270.0),
        ("North-East (45°)", 45.0),
        ("North-West (315°)", 315.0),
        ("North (0°)", 0.0),
        ("Custom", -1.0),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Step 4: Solar Forecasting via Open-Meteo (Optional)")
        self.setSubTitle(
            "Pre-configure your solar array location & roof geometry for Open-Meteo forecasting, "
            "or leave unchecked to configure later in the web interface."
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.chk_enable = QCheckBox("Configure / overwrite Solar Forecasting (Open-Meteo) settings on this Pi")
        self.chk_enable.setChecked(False)
        self.chk_enable.toggled.connect(self._on_toggle_enable)
        layout.addWidget(self.chk_enable)

        self.container = QWidget()
        self.container.setEnabled(False)
        self._opacity_fx = QGraphicsOpacityEffect(self.container)
        self._opacity_fx.setOpacity(0.38)
        self.container.setGraphicsEffect(self._opacity_fx)
        cl = QVBoxLayout(self.container)
        cl.setContentsMargins(0, 0, 4, 0)
        cl.setSpacing(10)

        # Instructions Box
        grp_help = QGroupBox("How to Determine Your Solar Parameters")
        hl = QVBoxLayout(grp_help)
        hl.setContentsMargins(14, 18, 14, 12)
        lbl_help = QLabel(
            "• <b>Latitude &amp; Longitude:</b> Open <b>Google Maps</b>, right-click on your house/roof, and click the "
            "coordinates at the top of the menu to copy them (e.g. <code>51.5074, -0.1278</code>). "
            "You can paste both directly into Latitude to auto-fill Longitude.<br>"
            "• <b>Array Capacity (kWp):</b> Total rated DC power of your panels "
            "(e.g. 10 × 400W panels = <code>4.0 kWp</code>). Check your MCS certificate or inverter paperwork.<br>"
            "• <b>Roof Tilt (°):</b> Panel angle from horizontal (<code>0°</code> = flat, <code>90°</code> = vertical). "
            "Typical UK pitched roofs are <code>30°–40°</code> (default <code>35°</code>).<br>"
            "• <b>Azimuth (°):</b> Compass direction the panels face (<code>180°</code> = Due South, "
            "<code>90°</code> = East, <code>270°</code> = West, <code>0°</code> = North).<br>"
            "<i>Tip: As actual solar data is recorded, an adaptive efficiency factor automatically calibrates future forecasts.</i>"
        )
        lbl_help.setWordWrap(True)
        lbl_help.setTextFormat(Qt.RichText)
        lbl_help.setStyleSheet("color: #CBD5E1; font-size: 12px; line-height: 1.45;")
        hl.addWidget(lbl_help)
        cl.addWidget(grp_help)

        row_h = 32

        # Location & Primary Array
        grp_loc = QGroupBox("Location && Primary Solar Array")
        gl = QGridLayout(grp_loc)
        gl.setVerticalSpacing(8)
        gl.setHorizontalSpacing(10)
        gl.setContentsMargins(14, 18, 14, 12)
        gl.setColumnMinimumWidth(0, 180)
        gl.setColumnStretch(1, 1)

        self.edt_lat = QLineEdit()
        self.edt_lat.setFixedHeight(row_h)
        self.edt_lat.setPlaceholderText("e.g. 51.5074 (or paste 'lat, lon')")
        self.edt_lat.textChanged.connect(self._on_lat_text_changed)

        self.edt_lon = QLineEdit()
        self.edt_lon.setFixedHeight(row_h)
        self.edt_lon.setPlaceholderText("e.g. -0.1278")

        self.spn_cap = QDoubleSpinBox()
        self.spn_cap.setFixedHeight(row_h)
        self.spn_cap.setRange(0.1, 200.0)
        self.spn_cap.setDecimals(2)
        self.spn_cap.setSingleStep(0.5)
        self.spn_cap.setSuffix(" kWp")
        self.spn_cap.setValue(5.0)

        self.spn_tilt = QDoubleSpinBox()
        self.spn_tilt.setFixedHeight(row_h)
        self.spn_tilt.setRange(0.0, 90.0)
        self.spn_tilt.setDecimals(1)
        self.spn_tilt.setSuffix(" °")
        self.spn_tilt.setValue(35.0)

        az_row = QHBoxLayout()
        az_row.setSpacing(8)
        self.cmb_az = QComboBox()
        self.cmb_az.setFixedHeight(row_h)
        for label, deg in self._AZIMUTH_PRESETS:
            self.cmb_az.addItem(label, deg)
        self.spn_az = QDoubleSpinBox()
        self.spn_az.setFixedHeight(row_h)
        self.spn_az.setRange(0.0, 360.0)
        self.spn_az.setDecimals(1)
        self.spn_az.setSuffix(" °")
        self.spn_az.setValue(180.0)
        self.cmb_az.currentIndexChanged.connect(lambda: self._sync_az_from_combo(self.cmb_az, self.spn_az))
        self.spn_az.valueChanged.connect(lambda v: self._sync_combo_from_az(self.cmb_az, v))
        az_row.addWidget(self.cmb_az, 1)
        az_row.addWidget(self.spn_az)

        gl.addWidget(QLabel("Latitude / Longitude:"), 0, 0)
        coord_row = QHBoxLayout()
        coord_row.setSpacing(8)
        coord_row.addWidget(self.edt_lat, 1)
        coord_row.addWidget(self.edt_lon, 1)
        gl.addLayout(coord_row, 0, 1)

        gl.addWidget(QLabel("Array Peak Capacity:"), 1, 0)
        gl.addWidget(self.spn_cap, 1, 1)
        gl.addWidget(QLabel("Roof Tilt Angle:"), 2, 0)
        gl.addWidget(self.spn_tilt, 2, 1)
        gl.addWidget(QLabel("Panel Azimuth (Compass):"), 3, 0)
        gl.addLayout(az_row, 3, 1)
        cl.addWidget(grp_loc)

        # Optional Split Roof (String 2)
        grp_a2 = QGroupBox("Optional Second Roof / Split String (e.g. East + West)")
        a2l = QGridLayout(grp_a2)
        a2l.setVerticalSpacing(8)
        a2l.setHorizontalSpacing(10)
        a2l.setContentsMargins(14, 18, 14, 12)
        a2l.setColumnMinimumWidth(0, 180)
        a2l.setColumnStretch(1, 1)

        self.chk_array2 = QCheckBox("Enable Second Array / String 2")
        self.chk_array2.setChecked(False)
        self.chk_array2.toggled.connect(self._on_toggle_array2)
        a2l.addWidget(self.chk_array2, 0, 0, 1, 2)

        self.spn_cap2 = QDoubleSpinBox()
        self.spn_cap2.setFixedHeight(row_h)
        self.spn_cap2.setRange(0.1, 200.0)
        self.spn_cap2.setDecimals(2)
        self.spn_cap2.setSingleStep(0.5)
        self.spn_cap2.setSuffix(" kWp")
        self.spn_cap2.setValue(2.0)

        self.spn_tilt2 = QDoubleSpinBox()
        self.spn_tilt2.setFixedHeight(row_h)
        self.spn_tilt2.setRange(0.0, 90.0)
        self.spn_tilt2.setDecimals(1)
        self.spn_tilt2.setSuffix(" °")
        self.spn_tilt2.setValue(35.0)

        az2_row = QHBoxLayout()
        az2_row.setSpacing(8)
        self.cmb_az2 = QComboBox()
        self.cmb_az2.setFixedHeight(row_h)
        for label, deg in self._AZIMUTH_PRESETS:
            self.cmb_az2.addItem(label, deg)
        idx_w = self.cmb_az2.findData(270.0)
        if idx_w >= 0:
            self.cmb_az2.setCurrentIndex(idx_w)
        self.spn_az2 = QDoubleSpinBox()
        self.spn_az2.setFixedHeight(row_h)
        self.spn_az2.setRange(0.0, 360.0)
        self.spn_az2.setDecimals(1)
        self.spn_az2.setSuffix(" °")
        self.spn_az2.setValue(270.0)
        self.cmb_az2.currentIndexChanged.connect(lambda: self._sync_az_from_combo(self.cmb_az2, self.spn_az2))
        self.spn_az2.valueChanged.connect(lambda v: self._sync_combo_from_az(self.cmb_az2, v))
        az2_row.addWidget(self.cmb_az2, 1)
        az2_row.addWidget(self.spn_az2)

        a2l.addWidget(QLabel("String 2 Capacity:"), 1, 0)
        a2l.addWidget(self.spn_cap2, 1, 1)
        a2l.addWidget(QLabel("String 2 Tilt Angle:"), 2, 0)
        a2l.addWidget(self.spn_tilt2, 2, 1)
        a2l.addWidget(QLabel("String 2 Azimuth:"), 3, 0)
        a2l.addLayout(az2_row, 3, 1)
        self._on_toggle_array2(False)
        cl.addWidget(grp_a2)
        cl.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self.container)
        layout.addWidget(scroll, 1)
        self._load_saved_solar_setup()

    def _load_saved_solar_setup(self) -> None:
        saved = _load_wizard_config().get("solar_forecast_setup")
        if not isinstance(saved, dict):
            return
        if "latitude" in saved:
            self.edt_lat.setText(str(saved["latitude"]))
        if "longitude" in saved:
            self.edt_lon.setText(str(saved["longitude"]))
        if "capacity_kwp" in saved:
            self.spn_cap.setValue(float(saved["capacity_kwp"]))
        if "tilt_deg" in saved:
            self.spn_tilt.setValue(float(saved["tilt_deg"]))
        if "azimuth_deg" in saved:
            self.spn_az.setValue(float(saved["azimuth_deg"]))
        self.chk_array2.setChecked(bool(saved.get("array2_enabled", False)))
        if "array2_capacity_kwp" in saved and float(saved["array2_capacity_kwp"]) > 0:
            self.spn_cap2.setValue(float(saved["array2_capacity_kwp"]))
        if "array2_tilt_deg" in saved:
            self.spn_tilt2.setValue(float(saved["array2_tilt_deg"]))
        if "array2_azimuth_deg" in saved:
            self.spn_az2.setValue(float(saved["array2_azimuth_deg"]))
        self.chk_enable.setChecked(bool(saved.get("enabled", False)))

    def _on_toggle_enable(self, checked: bool) -> None:
        self.container.setEnabled(checked)
        self._opacity_fx.setOpacity(1.0 if checked else 0.38)

    def _on_toggle_array2(self, checked: bool) -> None:
        for w in (self.spn_cap2, self.spn_tilt2, self.cmb_az2, self.spn_az2):
            w.setEnabled(checked)

    def _on_lat_text_changed(self, text: str) -> None:
        if "," in text:
            parts = [p.strip() for p in text.split(",", 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                try:
                    float(parts[0])
                    float(parts[1])
                    self.edt_lat.blockSignals(True)
                    self.edt_lat.setText(parts[0])
                    self.edt_lat.blockSignals(False)
                    self.edt_lon.setText(parts[1])
                except ValueError:
                    pass

    @staticmethod
    def _sync_az_from_combo(cmb: QComboBox, spn: QDoubleSpinBox) -> None:
        deg = float(cmb.currentData())
        if deg >= 0:
            spn.blockSignals(True)
            spn.setValue(deg)
            spn.blockSignals(False)

    @staticmethod
    def _sync_combo_from_az(cmb: QComboBox, val: float) -> None:
        idx = cmb.findData(round(val, 1))
        cmb.blockSignals(True)
        if idx >= 0:
            cmb.setCurrentIndex(idx)
        else:
            custom_idx = cmb.findData(-1.0)
            if custom_idx >= 0:
                cmb.setCurrentIndex(custom_idx)
        cmb.blockSignals(False)

    def validatePage(self) -> bool:
        if not self.chk_enable.isChecked():
            _update_wizard_config(
                {
                    "solar_forecast_setup": {
                        "enabled": False,
                        "latitude": self.edt_lat.text().strip(),
                        "longitude": self.edt_lon.text().strip(),
                        "capacity_kwp": round(self.spn_cap.value(), 2),
                        "tilt_deg": round(self.spn_tilt.value(), 1),
                        "azimuth_deg": round(self.spn_az.value(), 1),
                        "array2_enabled": self.chk_array2.isChecked(),
                        "array2_capacity_kwp": round(self.spn_cap2.value(), 2),
                        "array2_tilt_deg": round(self.spn_tilt2.value(), 1),
                        "array2_azimuth_deg": round(self.spn_az2.value(), 1),
                    }
                }
            )
            return True
        try:
            lat = float(self.edt_lat.text().strip())
            lon = float(self.edt_lon.text().strip())
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid Coordinates",
                "Please enter valid numeric Latitude and Longitude values (e.g. 51.5074 and -0.1278), "
                "or uncheck Solar Forecasting to configure it later in the web interface.",
            )
            return False
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            QMessageBox.warning(
                self,
                "Coordinates Out of Range",
                "Latitude must be between -90 and 90, and Longitude must be between -180 and 180.",
            )
            return False
        _update_wizard_config(
            {
                "solar_forecast_setup": {
                    "enabled": True,
                    "latitude": round(lat, 6),
                    "longitude": round(lon, 6),
                    "capacity_kwp": round(self.spn_cap.value(), 2),
                    "tilt_deg": round(self.spn_tilt.value(), 1),
                    "azimuth_deg": round(self.spn_az.value(), 1),
                    "array2_enabled": self.chk_array2.isChecked(),
                    "array2_capacity_kwp": round(self.spn_cap2.value(), 2),
                    "array2_tilt_deg": round(self.spn_tilt2.value(), 1),
                    "array2_azimuth_deg": round(self.spn_az2.value(), 1),
                }
            }
        )
        return True

    def build_solar_forecast_config(self) -> dict | None:
        if not self.chk_enable.isChecked():
            return None
        lat = float(self.edt_lat.text().strip())
        lon = float(self.edt_lon.text().strip())
        return {
            "enabled": True,
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "capacity_kwp": round(self.spn_cap.value(), 2),
            "tilt_deg": round(self.spn_tilt.value(), 1),
            "azimuth_deg": round(self.spn_az.value(), 1),
            "array2_enabled": self.chk_array2.isChecked(),
            "array2_capacity_kwp": round(self.spn_cap2.value(), 2) if self.chk_array2.isChecked() else 0.0,
            "array2_tilt_deg": round(self.spn_tilt2.value(), 1),
            "array2_azimuth_deg": round(self.spn_az2.value(), 1),
            "base_efficiency": 0.85,
            "forecast_days": 7,
            "last_sync_at": "",
            "last_sync_error": "",
        }


class DeployProgressPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Step 6: Provisioning & Installing GivEnergy Web Service")
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
        tariff_page = self.wizard().page(2)
        tariff_cfg = tariff_page.build_tariff_config() if hasattr(tariff_page, "build_tariff_config") else None
        solar_page = self.wizard().page(3)
        solar_cfg = solar_page.build_solar_forecast_config() if hasattr(solar_page, "build_solar_forecast_config") else None
        cred_page = self.wizard().page(4)
        storage_mode = cred_page.cmb_storage.currentData() if hasattr(cred_page, "cmb_storage") else "sd"
        self._worker = DeployWorker(
            host=str(self.field("pi_host")).strip(),
            user=str(self.field("pi_user")).strip(),
            password=str(self.field("pi_pass")),
            install_tailscale=bool(self.field("install_ts")),
            tailscale_authkey="",
            storage_mode=storage_mode,
            wipe_existing_data=bool(self.field("wipe_data")),
            tariff_config_dict=tariff_cfg,
            solar_forecast_config_dict=solar_cfg,
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
        self.resize(880, 830)
        self.setMinimumSize(850, 780)
        self.deployed_local_url = ""
        self.deployed_ts_ip = ""
        self.deployed_ts_dns = ""
        self.deployed_ts_auth_url = ""

        self.addPage(OSPrepPage())
        self.addPage(DiscoverPiPage())
        self.addPage(TariffSetupPage())
        self.addPage(SolarForecastSetupPage())
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
