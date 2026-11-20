"""Windows Service (Win32 SCM via ctypes) and console runner for GivEnergy Local Control."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import sys
import threading
import time
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import uvicorn

from givenergy_web.server import create_app

SERVICE_NAME = "GivEnergyLocalControl"


def get_program_data_root() -> Path:
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "GivEnergyLocalControl"


def load_service_config() -> dict[str, Any]:
    root = get_program_data_root()
    cfg_path = root / "service_config.json"
    defaults: dict[str, Any] = {
        "host": "0.0.0.0",
        "port": 80,
        "data_dir": str(root / "data"),
        "flush_interval_s": 15.0,
    }
    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                defaults.update(raw)
        except Exception:
            pass
    return defaults


def setup_service_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "service.log"
    handler = RotatingFileHandler(
        str(log_file),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)


def _bind_dual_stack_sockets(host: str, port: int) -> list[socket.socket]:
    """Bind IPv4 and (when available) IPv6 sockets so both localhost (::1) and 127.0.0.1 respond."""
    sockets: list[socket.socket] = []

    s4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s4.bind((host, port))
    s4.listen(128)
    s4.setblocking(False)
    sockets.append(s4)

    if host in ("0.0.0.0", "127.0.0.1") and socket.has_ipv6:
        try:
            s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            s6.bind(("::" if host == "0.0.0.0" else "::1", port))
            s6.listen(128)
            s6.setblocking(False)
            sockets.append(s6)
        except OSError:
            pass

    return sockets


def _build_uvicorn_server() -> tuple[uvicorn.Server, dict[str, Any]]:
    cfg = load_service_config()
    root = get_program_data_root()
    setup_service_logging(root / "logs")
    data_dir = Path(str(cfg.get("data_dir") or (root / "data")))
    data_dir.mkdir(parents=True, exist_ok=True)
    flush_s = float(cfg.get("flush_interval_s", 15.0))
    host = str(cfg.get("host") or "0.0.0.0")
    port = int(cfg.get("port") or 80)

    app = create_app(data_dir=data_dir, db_flush_interval_s=flush_s)
    uvi_cfg = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
    )
    return uvicorn.Server(uvi_cfg), cfg


def run_console() -> int:
    server, cfg = _build_uvicorn_server()
    sockets = _bind_dual_stack_sockets(str(cfg.get("host") or "0.0.0.0"), int(cfg.get("port") or 80))
    logging.getLogger(__name__).info("Starting GivEnergy Local Control in console mode on port %s", cfg["port"])
    server.run(sockets=sockets)
    return 0


def run_as_windows_service() -> int:
    """Register with the Windows Service Control Manager (advapi32) and run uvicorn."""
    if sys.platform != "win32":
        raise RuntimeError("Windows Service mode is only supported on Windows.")

    advapi32 = ctypes.windll.advapi32

    SERVICE_WIN32_OWN_PROCESS = 0x00000010
    SERVICE_START_PENDING = 0x00000002
    SERVICE_STOP_PENDING = 0x00000003
    SERVICE_RUNNING = 0x00000004
    SERVICE_STOPPED = 0x00000001
    SERVICE_ACCEPT_STOP = 0x00000001
    SERVICE_ACCEPT_SHUTDOWN = 0x00000004
    SERVICE_CONTROL_STOP = 0x00000001
    SERVICE_CONTROL_SHUTDOWN = 0x00000005

    class SERVICE_STATUS(ctypes.Structure):
        _fields_ = [
            ("dwServiceType", wintypes.DWORD),
            ("dwCurrentState", wintypes.DWORD),
            ("dwControlsAccepted", wintypes.DWORD),
            ("dwWin32ExitCode", wintypes.DWORD),
            ("dwServiceSpecificExitCode", wintypes.DWORD),
            ("dwCheckPoint", wintypes.DWORD),
            ("dwWaitHint", wintypes.DWORD),
        ]

    HANDLER_EX = ctypes.WINFUNCTYPE(wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID)
    SERVICE_MAIN_FN = ctypes.WINFUNCTYPE(None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))

    class SERVICE_TABLE_ENTRYW(ctypes.Structure):
        _fields_ = [
            ("lpServiceName", wintypes.LPWSTR),
            ("lpServiceProc", SERVICE_MAIN_FN),
        ]

    advapi32.RegisterServiceCtrlHandlerExW.argtypes = [wintypes.LPCWSTR, HANDLER_EX, wintypes.LPVOID]
    advapi32.RegisterServiceCtrlHandlerExW.restype = wintypes.HANDLE
    advapi32.SetServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(SERVICE_STATUS)]
    advapi32.SetServiceStatus.restype = wintypes.BOOL
    advapi32.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(SERVICE_TABLE_ENTRYW)]
    advapi32.StartServiceCtrlDispatcherW.restype = wintypes.BOOL

    status_handle_ref: list[int | None] = [None]
    server_ref: list[uvicorn.Server | None] = [None]
    stop_event = threading.Event()

    def set_status(state: int, *, wait_hint: int = 0, exit_code: int = 0) -> None:
        h = status_handle_ref[0]
        if not h:
            return
        accepted = (SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN) if state == SERVICE_RUNNING else 0
        st = SERVICE_STATUS(
            dwServiceType=SERVICE_WIN32_OWN_PROCESS,
            dwCurrentState=state,
            dwControlsAccepted=accepted,
            dwWin32ExitCode=exit_code,
            dwServiceSpecificExitCode=0,
            dwCheckPoint=0,
            dwWaitHint=wait_hint,
        )
        if not advapi32.SetServiceStatus(h, ctypes.byref(st)):
            logging.getLogger(__name__).error("SetServiceStatus(%s) failed: winerr=%s", state, ctypes.GetLastError())

    @HANDLER_EX
    def ctrl_handler(dwControl: int, dwEventType: int, lpEventData: Any, lpContext: Any) -> int:
        if dwControl in (SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN):
            set_status(SERVICE_STOP_PENDING, wait_hint=10000)
            stop_event.set()
            srv = server_ref[0]
            if srv is not None:
                srv.should_exit = True
            return 0
        return 0

    @SERVICE_MAIN_FN
    def service_main(dwArgc: int, lpszArgv: Any) -> None:
        h = advapi32.RegisterServiceCtrlHandlerExW(SERVICE_NAME, ctrl_handler, None)
        status_handle_ref[0] = h
        if not h:
            return

        set_status(SERVICE_START_PENDING, wait_hint=15000)
        exit_code = 0
        try:
            server, cfg = _build_uvicorn_server()
            sockets = _bind_dual_stack_sockets(str(cfg.get("host") or "0.0.0.0"), int(cfg.get("port") or 80))
            server_ref[0] = server
            logging.getLogger(__name__).info(
                "Windows Service %s starting on %s:%s",
                SERVICE_NAME,
                cfg.get("host"),
                cfg.get("port"),
            )
            set_status(SERVICE_RUNNING)
            server.run(sockets=sockets)
        except Exception:
            logging.getLogger(__name__).exception("Fatal error in Windows Service %s", SERVICE_NAME)
            exit_code = 1
        finally:
            set_status(SERVICE_STOPPED, exit_code=exit_code)

    table = (SERVICE_TABLE_ENTRYW * 2)(
        SERVICE_TABLE_ENTRYW(SERVICE_NAME, service_main),
        SERVICE_TABLE_ENTRYW(None, SERVICE_MAIN_FN()),
    )
    ok = advapi32.StartServiceCtrlDispatcherW(table)
    if not ok:
        # Fallback if invoked interactively without SCM
        return run_console()
    return 0


if __name__ == "__main__":
    if "--console" in sys.argv:
        sys.exit(run_console())
    sys.exit(run_as_windows_service())
