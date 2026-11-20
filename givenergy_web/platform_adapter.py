"""Platform abstraction layer for OS-specific telemetry, updates, and service lifecycle."""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
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


def _detect_linux_hardware() -> tuple[bool, str, str]:
    """Return (is_raspberry_pi, hardware_model, hardware_short)."""
    pi_model_path = Path("/proc/device-tree/model")
    if pi_model_path.exists():
        try:
            model_str = pi_model_path.read_text(errors="ignore").strip("\x00").strip()
            if model_str:
                is_pi = "raspberry pi" in model_str.lower()
                if is_pi:
                    short = model_str.split("Raspberry ", 1)[1]
                    for marker in (" Model ", " Rev "):
                        if marker in short:
                            short = short.split(marker, 1)[0]
                    return True, model_str, (short.strip() or "Pi")
                return False, model_str, model_str.split()[0][:16]
        except Exception:
            pass

    arch = platform.machine() or "Linux"
    dmi_name = ""
    for dmi_path in (Path("/sys/class/dmi/id/product_name"), Path("/sys/class/dmi/id/sys_vendor")):
        if dmi_path.exists():
            try:
                val = dmi_path.read_text(errors="ignore").strip()
                if val and val.lower() not in ("system product name", "to be filled by o.e.m.", "default string"):
                    dmi_name = val
                    break
            except Exception:
                pass

    os_pretty = ""
    os_release_path = Path("/etc/os-release")
    if os_release_path.exists():
        try:
            for line in os_release_path.read_text(errors="ignore").splitlines():
                if line.startswith("PRETTY_NAME="):
                    os_pretty = line.split("=", 1)[1].strip().strip('"')
                    break
        except Exception:
            pass

    if dmi_name and os_pretty:
        model_label = f"{os_pretty} ({dmi_name}, {arch})"
    elif os_pretty:
        model_label = f"{os_pretty} ({arch})"
    elif dmi_name:
        model_label = f"{dmi_name} ({arch})"
    else:
        model_label = f"Linux ({arch})"

    short_label = os_pretty.split()[0][:14] if os_pretty else "Linux"
    return False, model_label, short_label


def _detect_storage_label() -> str:
    """Programmatically detect root storage media type from /proc/mounts (e.g. MicroSD, NVMe SSD, SSD, HDD)."""
    mounts_path = Path("/proc/mounts")
    if not mounts_path.exists():
        return ""
    try:
        root_dev = ""
        for line in mounts_path.read_text(errors="ignore").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "/":
                root_dev = parts[0]
                break
        if not root_dev:
            return ""
        dev_name = Path(root_dev).name
        if dev_name.startswith("mmcblk"):
            return "MicroSD"
        if dev_name.startswith("nvme"):
            return "NVMe SSD"
        if dev_name.startswith(("vd", "xvd")):
            return "Virtual Disk"
        if dev_name.startswith("sd"):
            base_dev = dev_name.rstrip("0123456789")
            rot_path = Path(f"/sys/block/{base_dev}/queue/rotational")
            if rot_path.exists():
                val = rot_path.read_text(errors="ignore").strip()
                if val == "0":
                    return "SSD"
                if val == "1":
                    return "HDD"
            return "Disk"
    except Exception:
        pass
    return ""


class LinuxPiAdapter(PlatformAdapter):
    """Raspberry Pi / Debian Linux implementation using sysfs, procfs, apt, and systemd."""

    def __init__(self, service_name: str = "givenergy-web.service") -> None:
        self.service_name = service_name
        self._prev_cpu: tuple[int, int] | None = None
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
        is_pi, _, _ = _detect_linux_hardware()
        return {
            "platform": "linux",
            "is_raspberry_pi": is_pi,
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

        cpu_usage_pct: float | None = None
        stat_path = Path("/proc/stat")
        if stat_path.exists():
            try:
                first_line = stat_path.read_text(errors="ignore").splitlines()[0]
                if first_line.startswith("cpu "):
                    nums = [int(x) for x in first_line.split()[1:]]
                    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
                    total = sum(nums)
                    if self._prev_cpu is not None:
                        d_total = total - self._prev_cpu[0]
                        d_idle = idle - self._prev_cpu[1]
                        if d_total > 0:
                            cpu_usage_pct = round((d_total - d_idle) * 100.0 / d_total, 1)
                    self._prev_cpu = (total, idle)
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

        is_pi, model_str, short_str = _detect_linux_hardware()

        reboot_req, reboot_pkgs = self._check_reboot_required()
        return {
            "app_version": app_version,
            "hostname": socket.gethostname(),
            "hardware_model": model_str,
            "hardware_short": short_str,
            "is_raspberry_pi": is_pi,
            "reboot_required": reboot_req,
            "reboot_pkgs": reboot_pkgs,
            "os_release": f"{platform.system()} {platform.release()}",
            "python_version": platform.python_version(),
            "cpu_temp_c": cpu_temp_c,
            "cpu_usage_pct": cpu_usage_pct,
            "load_avg": load_avg,
            "mem_total_mb": mem_total_mb,
            "mem_used_mb": mem_used_mb,
            "mem_pct": mem_pct,
            "disk_total_gb": disk_total_gb,
            "disk_used_gb": disk_used_gb,
            "disk_free_gb": disk_free_gb,
            "disk_pct": disk_pct,
            "storage_type": _detect_storage_label(),
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
        is_pi, _, _ = _detect_linux_hardware()
        target_label = "Raspberry Pi" if is_pi else "Host"
        return True, f"{target_label} {cmd_arg} scheduled."

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
                    "status_text": "Checking for OS package updates (apt-get update)...",
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


class WindowsAdapter(PlatformAdapter):
    """Windows Service host implementation using Win32 APIs and standard library."""

    def __init__(self) -> None:
        self._prev_cpu_times: tuple[int, int, int] | None = self._read_cpu_times()

    @staticmethod
    def _read_cpu_times() -> tuple[int, int, int] | None:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        idle, kern, user = FILETIME(), FILETIME(), FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
            return None
        return (
            (idle.dwHighDateTime << 32) | idle.dwLowDateTime,
            (kern.dwHighDateTime << 32) | kern.dwLowDateTime,
            (user.dwHighDateTime << 32) | user.dwLowDateTime,
        )

    def _read_cpu_usage_pct(self) -> float | None:
        cur = self._read_cpu_times()
        if cur is None:
            return None
        prev = self._prev_cpu_times
        self._prev_cpu_times = cur
        if prev is None:
            return None
        idle_d = cur[0] - prev[0]
        tot_d = (cur[1] - prev[1]) + (cur[2] - prev[2])
        if tot_d <= 0:
            return None
        return round(max(0.0, min(100.0, (tot_d - idle_d) * 100.0 / tot_d)), 1)

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "platform": "windows",
            "is_raspberry_pi": False,
            "supports_os_updates": False,
            "supports_power_actions": False,
            "supports_app_updates": False,
            "supports_tailscale_cli": bool(shutil.which("tailscale")),
        }

    @staticmethod
    def _read_windows_memory() -> tuple[int, int, float]:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("sullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return 0, 0, 0.0
        total_mb = round(stat.ullTotalPhys / (1024 * 1024))
        avail_mb = round(stat.ullAvailPhys / (1024 * 1024))
        used_mb = max(0, total_mb - avail_mb)
        pct = round((used_mb * 100.0 / total_mb), 1) if total_mb else 0.0
        return total_mb, used_mb, pct

    def read_system_stats(
        self,
        *,
        app_version: str,
        started_at: float,
        ws_clients: int,
        db_stats: dict[str, Any],
        data_dir: Path,
    ) -> dict[str, Any]:
        mem_total_mb, mem_used_mb, mem_pct = self._read_windows_memory()
        disk = shutil.disk_usage(str(data_dir))
        disk_total_gb = round(disk.total / (1024**3), 2)
        disk_used_gb = round(disk.used / (1024**3), 2)
        disk_free_gb = round(disk.free / (1024**3), 2)
        disk_pct = round(disk.used * 100.0 / disk.total, 1) if disk.total else 0.0

        uptime_sec = int(time.time() - started_at)
        hours, rem = divmod(uptime_sec, 3600)
        mins, secs = divmod(rem, 60)

        arch = platform.machine() or "AMD64"
        os_rel = f"Windows {platform.release()}"
        model_str = f"{os_rel} ({arch})"

        return {
            "app_version": app_version,
            "hostname": socket.gethostname(),
            "hardware_model": model_str,
            "hardware_short": "Windows",
            "is_raspberry_pi": False,
            "reboot_required": False,
            "reboot_pkgs": [],
            "os_release": f"{platform.system()} {platform.release()} ({platform.version()})",
            "python_version": platform.python_version(),
            "cpu_temp_c": None,
            "cpu_usage_pct": self._read_cpu_usage_pct(),
            "load_avg": [],
            "mem_total_mb": mem_total_mb,
            "mem_used_mb": mem_used_mb,
            "mem_pct": mem_pct,
            "disk_total_gb": disk_total_gb,
            "disk_used_gb": disk_used_gb,
            "disk_free_gb": disk_free_gb,
            "disk_pct": disk_pct,
            "storage_type": "",
            "service_uptime_sec": uptime_sec,
            "service_uptime_str": f"{hours}h {mins}m {secs}s",
            "ws_clients": ws_clients,
            "database": db_stats,
            "capabilities": self.capabilities,
        }

    def read_tailscale_funnel_info(self) -> dict[str, str]:
        info = {"tailscale_ip": "", "tailscale_dns": "", "funnel_url": ""}
        ts_exe = shutil.which("tailscale")
        if not ts_exe:
            return info
        try:
            proc = subprocess.run([ts_exe, "status", "--json"], capture_output=True, text=True, timeout=3.0)
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
        return False, "Host reboot/shutdown is disabled on Windows."

    def start_system_updates(self) -> dict[str, Any]:
        return {"ok": False, "started": False, "error": "OS package updates are not supported on Windows."}

    def get_system_update_status(self) -> dict[str, Any]:
        return {
            "running": False,
            "phase": "unsupported",
            "status_text": "Managed via Windows Update",
            "percent": 0,
            "ok": None,
            "reboot_required": False,
            "reboot_pkgs": [],
            "output": "",
        }

    def trigger_service_restart(self) -> None:
        return


def get_platform_adapter() -> PlatformAdapter:
    """Return the active platform adapter for the current OS."""
    if sys.platform == "win32":
        return WindowsAdapter()
    return LinuxPiAdapter()
