"""Persistent Property Configuration & Local Network GivEnergy Device Scanner."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class PropertyConfig:
    property_name: str = "Home"
    inverter_enabled: bool = False
    inverter_ip: str = ""
    inverter_port: int = 8899
    evc_enabled: bool = False
    evc_ip: str = ""
    evc_port: int = 502
    has_solar_pv: str = "auto"  # "auto", "yes", or "no"
    poll_interval_s: float = 5.0


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def load_config(config_path: Path) -> PropertyConfig:
    """Load PropertyConfig from JSON file, falling back to environment variables and defaults."""
    inv_ip = os.environ.get("GIVENERGY_INVERTER_IP", "").strip()
    evc_ip = os.environ.get("GIVENERGY_EVC_IP", "").strip()
    cfg = PropertyConfig(
        property_name=os.environ.get("GIVENERGY_PROPERTY_NAME", "Home"),
        inverter_enabled=_env_bool("GIVENERGY_INVERTER_ENABLED", bool(inv_ip)),
        inverter_ip=inv_ip,
        inverter_port=int(os.environ.get("GIVENERGY_INVERTER_PORT", "8899")),
        evc_enabled=_env_bool("GIVENERGY_EVC_ENABLED", bool(evc_ip)),
        evc_ip=evc_ip,
        evc_port=int(os.environ.get("GIVENERGY_EVC_PORT", "502")),
        has_solar_pv=os.environ.get("GIVENERGY_HAS_SOLAR", "auto"),
        poll_interval_s=float(os.environ.get("GIVENERGY_POLL_INTERVAL", "5.0")),
    )
    if config_path.exists():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
        except Exception:
            pass
    return cfg


def save_config(config_path: Path, cfg: PropertyConfig) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")


def _detect_local_ipv4() -> str:
    """Determine the primary local LAN IPv4 address of this host."""
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


async def scan_lan_for_devices(subnet_cidr: str | None = None) -> dict[str, Any]:
    """Scan the local /24 subnet for GivEnergy Inverters (port 8899) and EV Chargers (port 502)."""
    if not subnet_cidr:
        local_ip = _detect_local_ipv4()
        net = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    else:
        net = ipaddress.ip_network(subnet_cidr, strict=False)

    hosts = [str(ip) for ip in net.hosts()][:254]
    sem = asyncio.Semaphore(80)

    inv_tasks = [_probe_tcp(ip, 8899, sem) for ip in hosts]
    evc_tasks = [_probe_tcp(ip, 502, sem) for ip in hosts]
    inv_results, evc_results = await asyncio.gather(
        asyncio.gather(*inv_tasks),
        asyncio.gather(*evc_tasks),
    )

    inverters = [ip for ip, ok in zip(hosts, inv_results) if ok]
    ev_chargers = [ip for ip, ok in zip(hosts, evc_results) if ok]
    return {
        "subnet": str(net),
        "inverters": inverters,
        "ev_chargers": ev_chargers,
    }
