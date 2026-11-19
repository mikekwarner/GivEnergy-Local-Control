"""Platform abstraction layer for OS-specific telemetry, updates, and service lifecycle."""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class PlatformAdapter(ABC):
    """Abstract interface for OS-specific system operations."""

    @property
    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Return platform capability flags for UI and API consumers."""

    @abstractmethod
    def read_system_stats(
        self,
        *,
        app_version: str,
        started_at: float,
        ws_clients: int,
        db_stats: dict[str, Any],
        data_dir: Path,
    ) -> dict[str, Any]:
        """Return host hardware, OS, memory, disk, and service health metrics."""

    @abstractmethod
    def read_tailscale_funnel_info(self) -> dict[str, str]:
        """Return Tailscale IP, DNS name, and Funnel HTTPS URL if available."""

    @abstractmethod
    def run_power_action(self, action: str) -> tuple[bool, str]:
        """Trigger a host reboot or shutdown if supported."""

    @abstractmethod
    def start_system_updates(self) -> dict[str, Any]:
        """Start OS package updates in the background if supported."""

    @abstractmethod
    def get_system_update_status(self) -> dict[str, Any]:
        """Return current OS update progress and log output."""

    @abstractmethod
    def trigger_service_restart(self) -> None:
        """Restart the hosting service cleanly after an in-place app update."""


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


class LinuxPiAdapter(PlatformAdapter):
    """Raspberry Pi / Debian Linux implementation using sysfs, procfs, apt, and systemd."""

    def __init__(self, service_name: str = "givenergy-web.service") -> None:
        self.service_name = service_name
        self._update_lock = threading.Lock()
        self._update_state: dict[str, Any] = {
            "running": False,
            "phase": "idle",
            "status_text": "Idle",
            "percent": 0,
            "ok": None,
            "reboot_required": False,
            "reboot_pkgs": [],
            "output": "",
        }

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "platform": "linux",
            "supports_os_updates": True,
            "supports_power_actions": True,
            "supports_app_updates": True,
            "supports_tailscale_cli": True,
        }

    @staticmethod
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

    def read_system_stats(
        self,
        *,
        app_version: str,
        started_at: float,
        ws_clients: int,
        db_stats: dict[str, Any],
        data_dir: Path,
    ) -> dict[str, Any]:
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

        disk = shutil.disk_usage(str(data_dir))
        disk_total_gb = round(disk.total / (1024**3), 2)
        disk_used_gb = round(disk.used / (1024**3), 2)
        disk_free_gb = round(disk.free / (1024**3), 2)
        disk_pct = round(disk.used * 100.0 / disk.total, 1) if disk.total else 0.0

        uptime_sec = int(time.time() - started_at)
        hours, rem = divmod(uptime_sec, 3600)
        mins, secs = divmod(rem, 60)

        model_str = platform.machine()
        pi_model_path = Path("/proc/device-tree/model")
        if pi_model_path.exists():
            try:
                model_str = pi_model_path.read_text(errors="ignore").strip("\x00").strip()
            except Exception:
                pass

        reboot_req, reboot_pkgs = self._check_reboot_required()
        return {
            "app_version": app_version,
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
            "ws_clients": ws_clients,
            "database": db_stats,
            "capabilities": self.capabilities,
        }

    def read_tailscale_funnel_info(self) -> dict[str, str]:
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

    def run_power_action(self, action: str) -> tuple[bool, str]:
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

    def _stream_apt_cmd(self, cmd: list[str], env: dict[str, str], base_pct: int, max_pct: int) -> int:
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
            with self._update_lock:
                self._update_state["percent"] = pct
                self._update_state["output"] = (self._update_state["output"] + "\n" + line).lstrip("\n")[-8000:]
        return proc.wait()

    def _run_system_updates_bg(self) -> None:
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        try:
            with self._update_lock:
                self._update_state.update({
                    "running": True,
                    "phase": "update",
                    "status_text": "Checking for Raspberry Pi OS package updates (apt-get update)...",
                    "percent": 10,
                    "ok": None,
                    "output": "=== Step 1/2: Running apt-get update ===",
                })
            rc1 = self._stream_apt_cmd(["sudo", "-n", "apt-get", "update"], env, 12, 35)
            if rc1 != 0:
                with self._update_lock:
                    self._update_state.update({
                        "running": False,
                        "phase": "error",
                        "status_text": f"Software update failed during apt-get update (exit code {rc1}).",
                        "percent": 100,
                        "ok": False,
                    })
                return

            with self._update_lock:
                self._update_state.update({
                    "phase": "upgrade",
                    "status_text": "Installing available OS software updates (apt-get upgrade -y)...",
                    "percent": 40,
                    "output": (self._update_state["output"] + "\n\n=== Step 2/2: Running apt-get upgrade -y ===")[-8000:],
                })
            rc2 = self._stream_apt_cmd(["sudo", "-n", "apt-get", "upgrade", "-y"], env, 42, 96)
            reboot_req, reboot_pkgs = self._check_reboot_required()
            with self._update_lock:
                if rc2 != 0:
                    status_msg = f"❌ Software update failed (exit code {rc2})."
                elif reboot_req:
                    status_msg = "✅ Software updates complete — reboot required to apply updates."
                else:
                    status_msg = "✅ Software updates complete! System is up to date (no reboot required)."
                self._update_state.update({
                    "running": False,
                    "phase": "done" if rc2 == 0 else "error",
                    "status_text": status_msg,
                    "percent": 100,
                    "ok": rc2 == 0,
                    "reboot_required": reboot_req,
                    "reboot_pkgs": reboot_pkgs,
                    "output": (self._update_state["output"] + "\n\n=== Update process finished ===")[-8000:],
                })
        except Exception as exc:
            with self._update_lock:
                self._update_state.update({
                    "running": False,
                    "phase": "error",
                    "status_text": f"❌ Software update error: {exc}",
                    "percent": 100,
                    "ok": False,
                    "output": (self._update_state["output"] + f"\n[ERROR] {exc}")[-8000:],
                })

    def start_system_updates(self) -> dict[str, Any]:
        with self._update_lock:
            if self._update_state["running"]:
                return {"ok": True, "started": False, **self._update_state}
            t = threading.Thread(target=self._run_system_updates_bg, daemon=True)
            t.start()
        return {"ok": True, "started": True}

    def get_system_update_status(self) -> dict[str, Any]:
        with self._update_lock:
            return dict(self._update_state)

    def trigger_service_restart(self) -> None:
        time.sleep(1.5)
        try:
            check = subprocess.run(
                ["sudo", "-n", "-l", "systemctl", "restart", self.service_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if check.returncode == 0:
                subprocess.Popen(
                    ["sudo", "-n", "systemctl", "restart", self.service_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return
        except Exception:
            pass
        os.kill(os.getpid(), signal.SIGTERM)


def get_platform_adapter() -> PlatformAdapter:
    """Return the active platform adapter (Linux/Pi currently supported)."""
    return LinuxPiAdapter()
