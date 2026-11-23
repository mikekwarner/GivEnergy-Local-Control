"""Pure-asyncio Modbus Polling & Hardware Control Service for FastAPI Web Server."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Callable

from givenergy_modbus.client import commands
from givenergy_modbus.client.client import Client as GivInverterClient
from givenergy_modbus.client.commands import BatteryPauseMode, ExportPriority
from givenergy_modbus.exceptions import PlantTopologyMismatch, RefreshFailed, RefreshPartiallySucceeded
from givenergy_modbus.model import TimeSlot
from givenergy_modbus.model.inverter import Model
from givenergy_modbus.model.manifest import has_capability
from givenergy_modbus.model.plant import PlantCapabilities
from pymodbus.client import ModbusTcpClient

from givenergy_web.models import BatteryTelemetry, PlantSnapshot
from givenergy_web.config import PropertyConfig, save_config
from givenergy_web.database import HistoricDatabase

logger = logging.getLogger(__name__)

EVC_STATUS_LIST = [
    "Unknown",
    "Idle",
    "Connected",
    "Starting",
    "Charging",
    "Startup Failure",
    "End of Charging",
    "System Failure",
    "Scheduled",
    "Updating",
    "Unstable CP",
]

EVC_CABLE_STATUS = ["Not Connected", "Connected"]
EVC_CHARGE_CONTROL = ["Ready", "Start", "Stop"]
EVC_CHARGING_MODES = ["Solar", "Hybrid", "Grid"]

EVC_ERROR_CODES = {
    0: "Clear",
    11: "CP voltage is abnormal",
    12: "Emergency stop",
    13: "Undervoltage",
    14: "Overvoltage",
    15: "Over temperature",
    16: "Meter failure",
    17: "Leakage fault",
    18: "Output short circuit",
    19: "Overcurrent",
    21: "Vehicle response timeout",
    22: "No diode at the vehicle end",
    23: "Relay sticking",
    24: "Leakage current device failure",
    25: "Ground fault",
    26: "Failed to start process",
}


@dataclass
class WebControlCommand:
    target: str  # "inverter", "evc", or "system"
    action: str
    params: dict[str, Any]
    description: str


def _serialize_battery(bat: BatteryTelemetry) -> dict[str, Any]:
    d = asdict(bat)
    d["cell_min_v"] = round(bat.cell_min_v, 3)
    d["cell_max_v"] = round(bat.cell_max_v, 3)
    d["cell_delta_mv"] = round(bat.cell_delta_mv, 1)
    return d


def serialize_snapshot(snap: PlantSnapshot, cfg: PropertyConfig | None = None) -> dict[str, Any]:
    """Convert PlantSnapshot into a clean JSON-serializable dictionary for WebSockets/REST."""
    inv = snap.inverter
    bat = snap.battery
    evc = snap.ev_charger

    inv_dict = asdict(inv)
    inv_dict["last_updated"] = inv.last_updated.strftime("%Y-%m-%d %H:%M:%S") if inv.last_updated else None
    inv_dict["system_time"] = inv.system_time.strftime("%Y-%m-%d %H:%M:%S") if inv.system_time else None
    inv_dict["system_time_polled_at"] = (
        inv.system_time_polled_at.strftime("%Y-%m-%d %H:%M:%S") if inv.system_time_polled_at else None
    )
    inv_dict["charge_slot_1_start"] = inv.charge_slot_1_start.strftime("%H:%M")
    inv_dict["charge_slot_1_end"] = inv.charge_slot_1_end.strftime("%H:%M")
    inv_dict["charge_slot_2_start"] = inv.charge_slot_2_start.strftime("%H:%M")
    inv_dict["charge_slot_2_end"] = inv.charge_slot_2_end.strftime("%H:%M")
    inv_dict["discharge_slot_1_start"] = inv.discharge_slot_1_start.strftime("%H:%M")
    inv_dict["discharge_slot_1_end"] = inv.discharge_slot_1_end.strftime("%H:%M")
    inv_dict["discharge_slot_2_start"] = inv.discharge_slot_2_start.strftime("%H:%M")
    inv_dict["discharge_slot_2_end"] = inv.discharge_slot_2_end.strftime("%H:%M")
    inv_dict["battery_pause_slot_start"] = inv.battery_pause_slot_start.strftime("%H:%M")
    inv_dict["battery_pause_slot_end"] = inv.battery_pause_slot_end.strftime("%H:%M")
    inv_dict["operating_mode_summary"] = inv.operating_mode_summary

    bat_dict = _serialize_battery(bat)
    bats_list = [_serialize_battery(b) for b in (snap.batteries or ([bat] if bat.present else []))]

    evc_dict = asdict(evc)
    evc_dict["last_updated"] = evc.last_updated.strftime("%Y-%m-%d %H:%M:%S") if evc.last_updated else None
    evc_dict["system_time"] = evc.system_time.strftime("%Y-%m-%d %H:%M:%S") if evc.system_time else None
    evc_dict["session_duration_formatted"] = evc.session_duration_formatted
    evc_dict["cable_status"] = evc.connection_status
    evc_dict["error_text"] = evc.error_description

    has_solar_mode = cfg.has_solar_pv if cfg else "auto"
    if has_solar_mode == "yes":
        has_solar = True
    elif has_solar_mode == "no":
        has_solar = False
    else:
        has_solar = bool(
            inv.p_pv_total > 0
            or inv.v_pv1 > 10
            or inv.v_pv2 > 10
            or inv.e_pv_generation_today > 0
            or inv.e_pv_generation_total > 0
            or not inv.connected
        )

    return {
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inverter": inv_dict,
        "battery": bat_dict,
        "batteries": bats_list,
        "ev_charger": evc_dict,
        "capabilities": {
            "property_name": cfg.property_name if cfg else "Home",
            "inverter_enabled": cfg.inverter_enabled if cfg else True,
            "evc_enabled": cfg.evc_enabled if cfg else True,
            "battery_count": len([b for b in bats_list if b.get("present")]),
            "has_solar": has_solar,
            "has_solar_mode": has_solar_mode,
        },
    }


class AsyncPlantPoller:
    """Background asyncio service polling Inverter (8899) and EV Charger (502)."""

    def __init__(
        self,
        db: HistoricDatabase,
        config: PropertyConfig | None = None,
        broadcast_cb: Callable[[dict[str, Any]], Any] | None = None,
        config_path: Path | None = None,
        tariff_config: Any = None,
    ) -> None:
        self.db = db
        self.config = config or PropertyConfig()
        self.config_path = config_path
        self.tariff_config = tariff_config
        self.inverter_enabled = self.config.inverter_enabled
        self.inverter_ip = self.config.inverter_ip
        self.inverter_port = self.config.inverter_port
        self.evc_enabled = self.config.evc_enabled
        self.evc_ip = self.config.evc_ip
        self.evc_port = self.config.evc_port
        self.poll_interval_s = self.config.poll_interval_s
        self.broadcast_cb = broadcast_cb

        self.snapshot = PlantSnapshot()
        self._last_evc_solar_adjust_ts = 0.0
        self._evc_low_solar_since: float | None = None
        self._evc_solar_started: bool = False
        self._evc_was_scheduled: bool = False
        self._evc_scheduled_session: bool = False
        self._evc_schedule_started: bool = False
        self._evc_schedule_prev_in_window: bool = False
        self._evc_schedule_handled_window: str | None = None
        self._saved_inv_discharge_limit: int | None = None
        self._evc_last_logged_cap_pct: int | None = None
        self._low_soc_guard_active: bool = False
        self._low_soc_last_logged_active: bool | None = None
        self.snapshot.inverter.low_soc_discharge_guard_enabled = bool(self.config.low_soc_discharge_guard)
        self._sync_evc_config_to_snapshot()
        self.events: list[dict[str, str]] = []
        self._running = False
        self._task: asyncio.Task | None = None
        self._wakeup_event = asyncio.Event()
        self._cmd_queue: asyncio.Queue[WebControlCommand] = asyncio.Queue()
        self.started_at = time.time()

    @staticmethod
    def _normalize_evc_mode(mode: str | None) -> str:
        raw = str(mode or "Grid").strip().lower()
        if raw in ("solar", "supereco", "eco+"):
            return "Solar"
        if raw in ("hybrid", "eco"):
            return "Hybrid"
        return "Grid"

    def _save_config_safe(self) -> None:
        if self.config_path is not None:
            try:
                save_config(self.config_path, self.config)
            except Exception as exc:
                logger.warning("Failed to persist property config: %s", exc)

    @staticmethod
    def _normalize_hhmm(raw: Any, fallback: str) -> str:
        s = str(raw or "").strip()
        try:
            parts = s.split(":")
            hh = int(parts[0])
            mm = int(parts[1])
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{hh:02d}:{mm:02d}"
        except Exception:
            pass
        return fallback

    @staticmethod
    def _hhmm_to_minutes(hhmm: str) -> int:
        try:
            parts = hhmm.strip().split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return 0

    @classmethod
    def _is_in_time_window(cls, now_min: int, start_hhmm: str, end_hhmm: str) -> bool:
        s = cls._hhmm_to_minutes(start_hhmm)
        e = cls._hhmm_to_minutes(end_hhmm)
        if s == e:
            return False
        if s < e:
            return s <= now_min < e
        return now_min >= s or now_min < e

    def _get_evc_schedule_windows(self) -> list[tuple[str, str, str]]:
        """Return active schedule windows as [(start, end, label), ...]."""
        custom_start = self._normalize_hhmm(self.config.evc_schedule_start, "00:00")
        custom_end = self._normalize_hhmm(self.config.evc_schedule_end, "06:00")
        if self.config.evc_schedule_use_tariff and self.tariff_config is not None:
            imp = getattr(self.tariff_config, "import_tariff", None)
            raw_windows = list(getattr(imp, "windows", []) or []) if imp is not None else []
            default_rate = float(getattr(imp, "default_rate_p", 0.0) or 0.0) if imp is not None else 0.0
            off_peak: list[tuple[str, str, str]] = []
            fallback_wins: list[tuple[str, str, str]] = []
            for w in raw_windows:
                ws = self._normalize_hhmm(w.get("start"), "")
                we = self._normalize_hhmm(w.get("end"), "")
                if not ws or not we or ws == we:
                    continue
                lbl = str(w.get("label") or "Off-Peak").strip() or "Off-Peak"
                rate = float(w.get("rate_p", default_rate))
                if rate < default_rate:
                    off_peak.append((ws, we, lbl))
                else:
                    fallback_wins.append((ws, we, lbl))
            if off_peak:
                return off_peak
            if fallback_wins:
                return fallback_wins
        return [(custom_start, custom_end, "Custom")]

    def _get_active_evc_schedule_window(self, now: datetime | None = None) -> tuple[bool, tuple[str, str, str] | None, list[tuple[str, str, str]]]:
        windows = self._get_evc_schedule_windows()
        dt = now or datetime.now()
        now_min = dt.hour * 60 + dt.minute
        for win in windows:
            if self._is_in_time_window(now_min, win[0], win[1]):
                return True, win, windows
        return False, None, windows

    def _sync_evc_config_to_snapshot(self) -> None:
        t_evc = self.snapshot.ev_charger
        t_evc.charging_mode = self._normalize_evc_mode(self.config.evc_charging_mode)
        t_evc.max_battery_discharge_w = max(0, min(5000, int(self.config.evc_max_battery_discharge_w)))
        t_evc.solar_export_split_pct = max(0, min(100, int(self.config.evc_solar_export_split_pct)))
        in_win, active_win, windows = self._get_active_evc_schedule_window()
        disp_win = active_win or (windows[0] if windows else ("00:00", "06:00", "Off-Peak"))
        t_evc.schedule_enabled = bool(self.config.evc_schedule_enabled)
        t_evc.schedule_use_tariff = bool(self.config.evc_schedule_use_tariff)
        t_evc.schedule_start = disp_win[0]
        t_evc.schedule_end = disp_win[1]
        t_evc.schedule_active = bool(self.config.evc_schedule_enabled and in_win)
        t_evc.schedule_window_label = f"{disp_win[2]} ({disp_win[0]}–{disp_win[1]})"

    async def apply_config(self, cfg: PropertyConfig) -> None:
        self.config = cfg
        self.inverter_enabled = cfg.inverter_enabled
        self.inverter_ip = cfg.inverter_ip
        self.inverter_port = cfg.inverter_port
        self.evc_enabled = cfg.evc_enabled
        self.evc_ip = cfg.evc_ip
        self.evc_port = cfg.evc_port
        self.poll_interval_s = max(2.0, float(cfg.poll_interval_s))
        self._sync_evc_config_to_snapshot()
        if not self.inverter_enabled:
            self.snapshot.inverter.connected = False
            self.snapshot.battery.present = False
            self.snapshot.batteries = []
        if not self.evc_enabled:
            self.snapshot.ev_charger.connected = False
            self.snapshot.ev_charger.active_power_w = 0
        await self.enqueue_command(
            target="system",
            action="reconnect",
            params={},
            description=f"Applied property config for '{cfg.property_name}'",
        )

    def log_event(self, level: str, message: str) -> None:
        entry = {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        self.events.append(entry)
        if len(self.events) > 50:
            self.events = self.events[-50:]
        logger.info("[%s] %s", level.upper(), message)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        self._wakeup_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except Exception:
                pass
        self.db.flush()

    def trigger_poll_now(self) -> None:
        self._wakeup_event.set()

    async def enqueue_command(self, target: str, action: str, params: dict[str, Any], description: str) -> None:
        await self._cmd_queue.put(WebControlCommand(target=target, action=action, params=params, description=description))
        self._wakeup_event.set()

    async def _run_loop(self) -> None:
        inv_client: GivInverterClient | None = None
        inv_detected = False
        cycle_count = 0

        self.log_event(
            "info",
            f"Starting GivEnergy plant poller for Inverter ({self.inverter_ip}:{self.inverter_port}) "
            f"& EV Charger ({self.evc_ip}:{self.evc_port})",
        )

        while self._running:
            need_config_reload = (cycle_count % 6 == 0)

            # 1. Process queued control commands
            while not self._cmd_queue.empty():
                cmd = await self._cmd_queue.get()
                if cmd.target == "system" and cmd.action == "reconnect":
                    if inv_client is not None:
                        try:
                            await inv_client.close()
                        except Exception:
                            pass
                        inv_client = None
                        inv_detected = False
                    self.log_event("info", cmd.description)
                    continue

                if cmd.target == "inverter" and cmd.action == "set_low_soc_discharge_guard":
                    enabled = bool(cmd.params.get("enabled", True))
                    self.config.low_soc_discharge_guard = enabled
                    self.snapshot.inverter.low_soc_discharge_guard_enabled = enabled
                    if not enabled:
                        self._low_soc_guard_active = False
                        self.snapshot.inverter.low_soc_discharge_guard_active = False
                    self._save_config_safe()
                    self.log_event("info", f"Battery Guard: Low-SoC Discharge Guard {'enabled' if enabled else 'disabled'}")
                    continue

                if cmd.target == "inverter":
                    inv_client, inv_detected = await self._ensure_inverter_connected(inv_client, inv_detected)
                    if inv_client and inv_detected:
                        ok = await self._execute_inverter_command(inv_client, cmd)
                        if ok:
                            need_config_reload = True
                    else:
                        self.log_event("error", f"Cannot execute '{cmd.description}': Inverter offline.")
                elif cmd.target == "evc":
                    await asyncio.to_thread(self._execute_evc_command, cmd)

            if not self._running:
                break

            # 2. Poll EV Charger (fast ~40ms TCP read) if enabled
            if self.evc_enabled and self.evc_ip:
                await asyncio.to_thread(self._poll_evc)
                if not inv_detected and self.snapshot.ev_charger.connected and self.broadcast_cb:
                    await self.broadcast_cb(self.get_full_payload())
            else:
                self.snapshot.ev_charger.connected = False
                self.snapshot.ev_charger.active_power_w = 0

            # 3. Poll Inverter & Battery Pack(s) if enabled
            if self.inverter_enabled and self.inverter_ip:
                inv_client, inv_detected = await self._poll_inverter(
                    inv_client, inv_detected, load_config=need_config_reload
                )
            else:
                self.snapshot.inverter.connected = False

            # 3b. Apply EV Charger Solar/Hybrid & Battery Discharge controls
            await self._apply_evc_smart_controls(inv_client, inv_detected)

            # 4. Record in SD-safe database & broadcast to WebSockets
            self.db.record_snapshot(copy.deepcopy(self.snapshot))
            if self.broadcast_cb:
                await self.broadcast_cb(self.get_full_payload())

            cycle_count += 1

            # 5. Wait for next poll interval or immediate command wakeup
            self._wakeup_event.clear()
            try:
                await asyncio.wait_for(self._wakeup_event.wait(), timeout=self.poll_interval_s)
            except TimeoutError:
                pass

        if inv_client is not None:
            try:
                await inv_client.close()
            except Exception:
                pass

    def get_full_payload(self) -> dict[str, Any]:
        data = serialize_snapshot(self.snapshot, self.config)
        data["events"] = list(self.events[-15:])
        data["live_ring"] = self.db.get_live_ring(1440, max_age_s=7200)  # Last 2 hours of live samples
        return data

    async def _ensure_inverter_connected(
        self, inv_client: GivInverterClient | None, inv_detected: bool
    ) -> tuple[GivInverterClient | None, bool]:
        if inv_client is None:
            inv_client = GivInverterClient(host=self.inverter_ip, port=self.inverter_port)
            inv_detected = False

        if not getattr(inv_client, "connected", False):
            try:
                await inv_client.connect()
                inv_detected = False
            except Exception as exc:
                self.snapshot.inverter.connected = False
                self.snapshot.inverter.error_message = f"Connection failed: {exc}"
                return inv_client, False

        if not inv_detected or inv_client.plant.capabilities is None:
            try:
                default_hint = PlantCapabilities(
                    device_type=Model.HYBRID_GEN3,
                    arm_firmware_version=318,
                    lv_battery_addresses=[0x32],
                )
                try:
                    await inv_client.detect(timeout=2.5, retries=2, prior=default_hint)
                except PlantTopologyMismatch:
                    await inv_client.detect(timeout=2.5, retries=2, probe_timeout=0.3, probe_retries=0)
                inv_detected = True
                sn = getattr(inv_client.plant, "inverter_serial_number", None) or "Gen 3"
                self.log_event("success", f"Connected to Hybrid Gen 3 Inverter ({sn}) at {self.inverter_ip}:{self.inverter_port}")
            except Exception as exc:
                self.snapshot.inverter.connected = False
                self.snapshot.inverter.error_message = f"Discovery failed: {exc}"
                try:
                    await inv_client.close()
                except Exception:
                    pass
                return None, False

        return inv_client, inv_detected

    async def _poll_inverter(
        self,
        inv_client: GivInverterClient | None,
        inv_detected: bool,
        load_config: bool = False,
    ) -> tuple[GivInverterClient | None, bool]:
        inv_client, inv_detected = await self._ensure_inverter_connected(inv_client, inv_detected)
        if not inv_client or not inv_detected:
            return inv_client, inv_detected

        try:
            if load_config:
                try:
                    await inv_client.load_config(timeout=2.5, retries=2)
                    self.snapshot.inverter.system_time_polled_at = datetime.now()
                except RefreshPartiallySucceeded:
                    self.snapshot.inverter.system_time_polled_at = datetime.now()
            try:
                await inv_client.refresh(timeout=2.5, retries=2)
            except RefreshPartiallySucceeded:
                pass

            self._extract_inverter_and_battery(inv_client)
            self.snapshot.inverter.connected = True
            self.snapshot.inverter.last_updated = datetime.now()
            self.snapshot.inverter.error_message = ""
        except RefreshFailed as exc:
            self.snapshot.inverter.connected = False
            self.snapshot.inverter.error_message = f"Refresh failed: {exc}"
            self.log_event("warning", f"Inverter poll timeout ({exc}); reconnecting next cycle.")
            try:
                await inv_client.close()
            except Exception:
                pass
            return None, False
        except Exception as exc:
            self.snapshot.inverter.connected = False
            self.snapshot.inverter.error_message = f"Error: {exc}"
            try:
                await inv_client.close()
            except Exception:
                pass
            return None, False

        return inv_client, inv_detected

    def _extract_inverter_and_battery(self, inv_client: GivInverterClient) -> None:
        plant = inv_client.plant
        inv = plant.inverter
        if inv is None:
            return

        t_inv = self.snapshot.inverter
        t_inv.serial_number = str(getattr(inv, "serial_number", None) or getattr(plant, "inverter_serial_number", "—"))
        dtc = str(getattr(inv, "device_type_code", "2001") or "2001")
        t_inv.device_type_code = dtc
        arm_fw = int(getattr(inv, "arm_firmware_version", 0) or 0)
        t_inv.arm_firmware_version = arm_fw
        t_inv.dsp_firmware_version = int(getattr(inv, "dsp_firmware_version", 0) or 0)
        t_inv.bms_firmware_version = int(getattr(inv, "bms_firmware_version", 0) or 0)
        t_inv.firmware_version = str(getattr(inv, "firmware_version", "—") or "—")
        t_inv.modbus_version = str(getattr(inv, "modbus_version", "—") or "—")
        t_inv.modbus_address = int(getattr(inv, "modbus_address", 17) or 17)

        if dtc.startswith("20") and (arm_fw // 100) == 3:
            t_inv.model_name = "Hybrid Gen 3"
        elif getattr(inv, "model", None) is not None:
            t_inv.model_name = getattr(inv.model, "name", str(inv.model)).replace("_", " ").title()

        status_obj = getattr(inv, "status", None)
        t_inv.status = getattr(status_obj, "name", str(status_obj)) if status_obj is not None else "NORMAL"
        t_inv.system_time = getattr(inv, "system_time", None)
        t_inv.work_time_hours = int(getattr(inv, "work_time_total_hours", 0) or 0)
        t_inv.fault_code = str(getattr(inv, "inverter_fault_code", "0000") or "0000")
        t_inv.warning_code = str(getattr(inv, "inverter_warning_code", "0000") or "0000")
        t_inv.fault_messages = list(getattr(inv, "inverter_fault_messages", None) or [])
        t_inv.charger_warning_messages = list(getattr(inv, "charger_warning_messages", None) or [])

        t_inv.p_pv1 = int(getattr(inv, "p_pv1", 0) or 0)
        t_inv.p_pv2 = int(getattr(inv, "p_pv2", 0) or 0)
        t_inv.p_pv_total = t_inv.p_pv1 + t_inv.p_pv2

        t_inv.p_battery = int(getattr(inv, "p_battery", 0) or 0)
        t_inv.battery_charge_power = int(getattr(inv, "battery_charge_power", 0) or max(0, -t_inv.p_battery))
        t_inv.battery_discharge_power = int(getattr(inv, "battery_discharge_power", 0) or max(0, t_inv.p_battery))

        t_inv.p_grid_out = int(getattr(inv, "p_grid_out", 0) or 0)
        t_inv.grid_import_power = int(getattr(inv, "grid_import_power", 0) or max(0, -t_inv.p_grid_out))
        t_inv.grid_export_power = int(getattr(inv, "grid_export_power", 0) or max(0, t_inv.p_grid_out))
        t_inv.p_grid_apparent = int(getattr(inv, "p_grid_apparent", 0) or 0)
        t_inv.p_load_demand = int(getattr(inv, "p_load_demand", 0) or 0)
        t_inv.p_backup = int(getattr(inv, "p_backup", 0) or 0)
        t_inv.p_inverter_out = int(getattr(inv, "p_grid_out_ph1", 0) or 0)

        t_inv.v_pv1 = float(getattr(inv, "v_pv1", 0.0) or 0.0)
        t_inv.i_pv1 = float(getattr(inv, "i_pv1", 0.0) or 0.0)
        t_inv.v_pv2 = float(getattr(inv, "v_pv2", 0.0) or 0.0)
        t_inv.i_pv2 = float(getattr(inv, "i_pv2", 0.0) or 0.0)

        t_inv.v_ac1 = float(getattr(inv, "v_ac1", 0.0) or 0.0)
        t_inv.i_grid_port = float(getattr(inv, "i_grid_port", 0.0) or 0.0)
        t_inv.f_ac1 = float(getattr(inv, "f_ac1", 0.0) or 0.0)
        t_inv.v_ac1_output = float(getattr(inv, "v_ac1_output", 0.0) or 0.0)
        t_inv.i_ac1 = float(getattr(inv, "i_ac1", 0.0) or 0.0)
        t_inv.f_ac1_output = float(getattr(inv, "f_ac1_output", 0.0) or 0.0)
        t_inv.v_p_bus = float(getattr(inv, "v_p_bus", 0.0) or 0.0)
        t_inv.v_highbrigh_bus = float(getattr(inv, "v_highbrigh_bus", 0.0) or 0.0)
        t_inv.power_factor = float(getattr(inv, "pf_inverter_output_now", 0.0) or 0.0)
        t_inv.inverter_max_power = int(getattr(inv, "inverter_max_power", 5000) or 5000)
        t_inv.grid_port_max_power_output = int(getattr(inv, "grid_port_max_power_output", 3680) or 3680)

        t_inv.battery_soc = int(getattr(inv, "battery_soc", 0) or 0)
        t_inv.v_battery = float(getattr(inv, "v_battery", 0.0) or 0.0)
        t_inv.i_battery = float(getattr(inv, "i_battery", 0.0) or 0.0)
        cs_label = getattr(inv, "charge_status_label", None)
        t_inv.charge_status_label = getattr(cs_label, "name", str(cs_label)) if cs_label is not None else "—"
        t_inv.battery_capacity_ah = int(getattr(inv, "battery_capacity_ah", 186) or 186)
        t_inv.battery_capacity_kwh = float(getattr(inv, "battery_capacity_kwh", 9.52) or 9.52)
        t_inv.battery_max_power = int(getattr(inv, "battery_max_power", 3600) or 3600)

        t_inv.t_inverter_heatsink = float(getattr(inv, "t_inverter_heatsink", 0.0) or 0.0)
        t_inv.t_charger = float(getattr(inv, "t_charger", 0.0) or 0.0)
        t_inv.t_battery = float(getattr(inv, "t_battery", 0.0) or 0.0)

        t_inv.e_pv1_day = float(getattr(inv, "e_pv1_day", 0.0) or 0.0)
        t_inv.e_pv2_day = float(getattr(inv, "e_pv2_day", 0.0) or 0.0)
        t_inv.e_pv_generation_today = float(getattr(inv, "e_pv_generation_today", 0.0) or 0.0)
        t_inv.e_consumption_today = float(getattr(inv, "e_consumption_today", 0.0) or 0.0)
        t_inv.e_self_consumption_today = float(getattr(inv, "e_self_consumption_today", 0.0) or 0.0)
        t_inv.e_grid_in_day = float(getattr(inv, "e_grid_in_day", 0.0) or 0.0)
        t_inv.e_grid_out_day = float(getattr(inv, "e_grid_out_day", 0.0) or 0.0)
        t_inv.e_ac_charge_today = float(getattr(inv, "e_ac_charge_today", 0.0) or 0.0)

        bat_chg_today = getattr(inv, "e_battery_charge_today", None)
        if bat_chg_today is None:
            bat_chg_today = getattr(inv, "e_battery_charge_today_alt1", 0.0)
        t_inv.e_battery_charge_today = float(bat_chg_today or 0.0)

        bat_dis_today = getattr(inv, "e_battery_discharge_today", None)
        if bat_dis_today is None:
            bat_dis_today = getattr(inv, "e_battery_discharge_today_alt1", 0.0)
        t_inv.e_battery_discharge_today = float(bat_dis_today or 0.0)

        t_inv.e_pv_generation_total = float(getattr(inv, "e_pv_generation_total", 0.0) or 0.0)
        t_inv.e_grid_in_total = float(getattr(inv, "e_grid_in_total", 0.0) or 0.0)
        t_inv.e_grid_out_total = float(getattr(inv, "e_grid_out_total", 0.0) or 0.0)
        t_inv.e_inverter_in_total = float(getattr(inv, "e_inverter_in_total", 0.0) or 0.0)
        t_inv.e_battery_throughput = float(getattr(inv, "e_battery_throughput", 0.0) or 0.0)
        t_inv.e_self_consumption_total = float(getattr(inv, "e_self_consumption_total", 0.0) or 0.0)

        bpm = getattr(inv, "battery_power_mode", 1)
        t_inv.battery_power_mode = int(getattr(bpm, "value", bpm) if bpm is not None else 1)
        t_inv.enable_charge = bool(getattr(inv, "enable_charge", False))
        t_inv.enable_discharge = bool(getattr(inv, "enable_discharge", False))
        t_inv.enable_charge_target = bool(getattr(inv, "enable_charge_target", False))
        t_inv.charge_target_soc = int(getattr(inv, "charge_target_soc", 100) or 100)
        t_inv.battery_soc_reserve = int(getattr(inv, "battery_soc_reserve", 4) or 4)
        t_inv.battery_discharge_min_power_reserve = int(
            getattr(inv, "battery_discharge_min_power_reserve", 4) or 4
        )
        raw_chg_limit = getattr(inv, "battery_charge_limit", None)
        t_inv.battery_charge_limit = int(raw_chg_limit if raw_chg_limit is not None else 50)
        raw_dis_limit = getattr(inv, "battery_discharge_limit", None)
        t_inv.battery_discharge_limit = int(raw_dis_limit if raw_dis_limit is not None else 50)
        t_inv.active_power_rate = int(getattr(inv, "active_power_rate", 100) or 100)

        cs1 = getattr(inv, "charge_slot_1", None)
        if cs1 is not None:
            t_inv.charge_slot_1_start = cs1.start
            t_inv.charge_slot_1_end = cs1.end
        cs2 = getattr(inv, "charge_slot_2", None)
        if cs2 is not None:
            t_inv.charge_slot_2_start = cs2.start
            t_inv.charge_slot_2_end = cs2.end
        ds1 = getattr(inv, "discharge_slot_1", None)
        if ds1 is not None:
            t_inv.discharge_slot_1_start = ds1.start
            t_inv.discharge_slot_1_end = ds1.end
        ds2 = getattr(inv, "discharge_slot_2", None)
        if ds2 is not None:
            t_inv.discharge_slot_2_start = ds2.start
            t_inv.discharge_slot_2_end = ds2.end

        slot_map = getattr(inv, "slot_map", None)
        num_chg_slots = len(slot_map.charge_slots) if slot_map is not None else 2
        num_dis_slots = len(slot_map.discharge_slots) if slot_map is not None else 2
        t_inv.num_charge_slots = num_chg_slots
        t_inv.num_discharge_slots = num_dis_slots

        inv_model = getattr(inv, "model", None)
        t_inv.has_ac_config_block = bool(inv_model is not None and has_capability("has_ac_config_block", inv_model))
        t_inv.is_three_phase = bool(inv_model is not None and has_capability("is_three_phase", inv_model))

        t_inv.charge_slots = []
        for idx in range(1, num_chg_slots + 1):
            if idx == 2 and num_chg_slots > 2:
                slot_obj = getattr(inv, "charge_slot_2_x", None) or getattr(inv, "charge_slot_2", None)
            else:
                slot_obj = getattr(inv, f"charge_slot_{idx}", None)
            target_soc = getattr(inv, f"charge_target_soc_{idx}", None)
            t_inv.charge_slots.append({
                "slot": idx,
                "start": slot_obj.start.strftime("%H:%M") if slot_obj is not None else "00:00",
                "end": slot_obj.end.strftime("%H:%M") if slot_obj is not None else "00:00",
                "target_soc": int(target_soc) if target_soc is not None else None,
            })

        t_inv.discharge_slots = []
        for idx in range(1, num_dis_slots + 1):
            slot_obj = getattr(inv, f"discharge_slot_{idx}", None)
            target_soc = getattr(inv, f"discharge_target_soc_{idx}", None)
            t_inv.discharge_slots.append({
                "slot": idx,
                "start": slot_obj.start.strftime("%H:%M") if slot_obj is not None else "00:00",
                "end": slot_obj.end.strftime("%H:%M") if slot_obj is not None else "00:00",
                "target_soc": int(target_soc) if target_soc is not None else None,
            })

        pause_mode = getattr(inv, "battery_pause_mode", 0)
        t_inv.battery_pause_mode = int(getattr(pause_mode, "value", pause_mode) if pause_mode is not None else 0)
        t_inv.battery_pause_mode_label = getattr(pause_mode, "name", str(pause_mode)) if pause_mode is not None else "DISABLED"
        ps1 = getattr(inv, "battery_pause_slot_1", None)
        if ps1 is not None:
            t_inv.battery_pause_slot_start = ps1.start
            t_inv.battery_pause_slot_end = ps1.end

        t_inv.enable_eps = bool(getattr(inv, "enable_eps", False))
        exp_pri = getattr(inv, "export_priority", 0)
        t_inv.export_priority = int(getattr(exp_pri, "value", exp_pri) if exp_pri is not None else 0)
        t_inv.export_priority_label = getattr(exp_pri, "name", str(exp_pri)) if exp_pri is not None else "LOAD_FIRST"
        raw_chg_ac = getattr(inv, "battery_charge_limit_ac", None)
        t_inv.battery_charge_limit_ac = int(raw_chg_ac if raw_chg_ac is not None else 100)
        raw_dis_ac = getattr(inv, "battery_discharge_limit_ac", None)
        t_inv.battery_discharge_limit_ac = int(raw_dis_ac if raw_dis_ac is not None else 100)
        t_inv.enable_rtc = bool(getattr(inv, "enable_rtc", True))

        bats_out: list[BatteryTelemetry] = []
        if plant.batteries:
            for bat in plant.batteries:
                b = BatteryTelemetry(present=True)
                b.serial_number = str(getattr(bat, "serial_number", "—") or "—")
                b.bms_firmware_version = int(getattr(bat, "bms_firmware_version", 0) or 0)
                b.soc = int(getattr(bat, "soc", t_inv.battery_soc) or t_inv.battery_soc)
                b.num_cells = int(getattr(bat, "num_cells", 16) or 16)
                b.num_cycles = int(getattr(bat, "num_cycles", 0) or 0)
                b.v_out = float(getattr(bat, "v_out", 0.0) or 0.0)
                b.v_cells_sum = float(getattr(bat, "v_cells_sum", 0.0) or 0.0)
                b.i_battery = float(getattr(bat, "i_battery", 0.0) or 0.0)
                b.cell_voltages = [float(getattr(bat, f"v_cell_{idx:02d}", 0.0) or 0.0) for idx in range(1, 17)]
                b.cap_design = float(getattr(bat, "cap_design", 186.0) or 186.0)
                b.cap_calibrated = float(getattr(bat, "cap_calibrated", 186.0) or 186.0)
                b.cap_remaining = float(getattr(bat, "cap_remaining", 0.0) or 0.0)
                b.remaining_energy_nominal_wh = int(getattr(bat, "remaining_energy_nominal_wh", 0) or 0)
                b.remaining_energy_measured_wh = int(getattr(bat, "remaining_energy_measured_wh", 0) or 0)
                b.e_battery_charge_total = float(getattr(bat, "e_battery_charge_total", 0.0) or 0.0)
                b.e_battery_discharge_total = float(getattr(bat, "e_battery_discharge_total", 0.0) or 0.0)
                b.t_bms_mosfet = float(getattr(bat, "t_bms_mosfet", 0.0) or 0.0)
                b.t_max = float(getattr(bat, "t_max", 0.0) or 0.0)
                b.t_min = float(getattr(bat, "t_min", 0.0) or 0.0)
                b.t_cells_01_04 = float(getattr(bat, "t_cells_01_04", 0.0) or 0.0)
                b.t_cells_05_08 = float(getattr(bat, "t_cells_05_08", 0.0) or 0.0)
                b.t_cells_09_12 = float(getattr(bat, "t_cells_09_12", 0.0) or 0.0)
                b.t_cells_13_16 = float(getattr(bat, "t_cells_13_16", 0.0) or 0.0)
                b.warning_1 = int(getattr(bat, "warning_1", 0) or 0)
                b.warning_2 = int(getattr(bat, "warning_2", 0) or 0)
                bats_out.append(b)
            self.snapshot.batteries = bats_out
            self.snapshot.battery = bats_out[0]
        else:
            self.snapshot.batteries = []
            self.snapshot.battery = BatteryTelemetry(present=False)

    def _poll_evc(self) -> None:
        t_evc = self.snapshot.ev_charger
        client = ModbusTcpClient(self.evc_ip, port=self.evc_port, timeout=2.5)
        try:
            if not client.connect():
                t_evc.connected = False
                t_evc.error_message = f"Could not connect to {self.evc_ip}:{self.evc_port}"
                return

            r1 = client.read_holding_registers(0, count=60, device_id=1)
            r2 = client.read_holding_registers(60, count=55, device_id=1)
            if r1.isError() or r2.isError() or not hasattr(r1, "registers") or not hasattr(r2, "registers"):
                t_evc.connected = False
                t_evc.error_message = "Modbus register read error"
                return

            regs = list(r1.registers) + list(r2.registers)
            if len(regs) < 115:
                t_evc.connected = False
                t_evc.error_message = f"Incomplete register block ({len(regs)}/115)"
                return

            state_code = regs[0]
            t_evc.charging_state_code = state_code
            t_evc.charging_state = (
                EVC_STATUS_LIST[state_code] if 0 <= state_code < len(EVC_STATUS_LIST) else f"State {state_code}"
            )

            cable_code = regs[2]
            t_evc.cable_connected = cable_code == 1
            t_evc.connection_status = (
                EVC_CABLE_STATUS[cable_code] if 0 <= cable_code < len(EVC_CABLE_STATUS) else f"Code {cable_code}"
            )

            err_code = regs[4]
            t_evc.error_code = err_code
            t_evc.error_description = EVC_ERROR_CODES.get(err_code, f"Fault Code {err_code}")

            t_evc.current_l1_a = round(regs[6] / 10.0, 1)
            t_evc.active_power_w = int(regs[13])
            t_evc.active_power_l1_w = int(regs[17])
            t_evc.voltage_l1_v = round(regs[109] / 10.0, 1)

            raw_meter_energy = (int(regs[28]) << 16) | int(regs[29])
            t_evc.total_meter_energy_kwh = round(raw_meter_energy / 10.0, 1)

            t_evc.evse_max_current_a = round(regs[32] / 10.0, 1)
            t_evc.evse_min_current_a = round(regs[34] / 10.0, 1)
            t_evc.charge_limit_a = round(regs[36] / 10.0, 1)
            t_evc.setpoint_limit_a = round(regs[91] / 10.0, 1) if regs[91] > 0 else t_evc.charge_limit_a

            sn_chars = [chr(c) for c in regs[38:69] if 32 <= c <= 126]
            t_evc.serial_number = "".join(sn_chars).strip() or "—"

            session_kwh = round(regs[72] / 10.0, 1)
            if session_kwh > 0 or t_evc.charging_state == "Charging":
                t_evc.session_energy_kwh = session_kwh
            t_evc.session_duration_seconds = int(regs[79])

            t_evc.session_start_time = f"{regs[74]:02d}:{regs[75]:02d}:{regs[76]:02d}"
            t_evc.session_end_time = f"{regs[82]:02d}:{regs[83]:02d}:{regs[84]:02d}"

            t_evc.plug_and_go_enabled = regs[93] == 0
            ctrl_idx = regs[94]
            if state_code == 4:
                t_evc.charge_control_state = "Start"
            elif 0 <= ctrl_idx < len(EVC_CHARGE_CONTROL):
                t_evc.charge_control_state = EVC_CHARGE_CONTROL[ctrl_idx]
            else:
                t_evc.charge_control_state = "Stop"

            try:
                if 2020 <= regs[97] <= 2100:
                    t_evc.system_time = datetime(regs[97], regs[98], regs[99], regs[100], regs[101], regs[102])
            except ValueError:
                pass

            self._sync_evc_config_to_snapshot()

            t_evc.connected = True
            t_evc.last_updated = datetime.now()
            t_evc.error_message = ""
        except Exception as exc:
            t_evc.connected = False
            t_evc.error_message = f"EVC Poll Error: {exc}"
        finally:
            try:
                client.close()
            except Exception:
                pass

    @staticmethod
    def _parse_time_slot(start_str: Any, end_str: Any) -> TimeSlot:
        sh, sm = [int(x) for x in str(start_str).split(":")[:2]]
        eh, em = [int(x) for x in str(end_str).split(":")[:2]]
        return TimeSlot(start=dt_time(sh, sm), end=dt_time(eh, em))

    async def _execute_inverter_command(self, inv_client: GivInverterClient, cmd: WebControlCommand) -> bool:
        try:
            inv = inv_client.plant.inverter
            slot_map = inv.slot_map
            reqs = []
            p = cmd.params

            if cmd.action == "set_mode_eco":
                reqs = commands.set_mode_dynamic()
            elif cmd.action == "set_mode_timed_discharge":
                reqs = commands.set_discharge_mode_to_match_demand() + commands.set_enable_discharge(True)
            elif cmd.action == "set_mode_timed_export":
                reqs = commands.set_discharge_mode_max_power() + commands.set_enable_discharge(True)
            elif cmd.action == "set_enable_charge":
                reqs = commands.set_enable_charge(bool(p["enabled"]))
            elif cmd.action == "set_enable_discharge":
                reqs = commands.set_enable_discharge(bool(p["enabled"]))
            elif cmd.action == "set_charge_target_soc":
                reqs = commands.set_charge_target_enabled(int(p["soc"]))
            elif cmd.action == "set_enable_charge_target":
                if bool(p["enabled"]):
                    soc = int(p.get("soc", self.snapshot.inverter.charge_target_soc or 100))
                    reqs = commands.set_charge_target_enabled(soc)
                else:
                    reqs = commands.disable_charge_target()
            elif cmd.action == "set_battery_soc_reserve":
                reqs = commands.set_battery_soc_reserve(int(p["soc"]))
            elif cmd.action == "set_battery_power_reserve":
                reqs = commands.set_battery_power_reserve(int(p["soc"]))
            elif cmd.action == "set_battery_charge_limit":
                reqs = commands.set_battery_charge_limit(int(p["limit"]))
            elif cmd.action == "set_battery_discharge_limit":
                reqs = commands.set_battery_discharge_limit(int(p["limit"]))
            elif cmd.action == "set_active_power_rate":
                reqs = commands.set_active_power_rate(int(p["rate"]))
            elif cmd.action == "set_charge_slot_1":
                reqs = commands.set_charge_slot(1, self._parse_time_slot(p["start"], p["end"]), slot_map)
            elif cmd.action == "set_discharge_slot_1":
                reqs = commands.set_discharge_slot(1, self._parse_time_slot(p["start"], p["end"]), slot_map)
            elif cmd.action == "set_charge_slot":
                slot_idx = int(p["slot"])
                reqs = commands.set_charge_slot(slot_idx, self._parse_time_slot(p["start"], p["end"]), slot_map)
            elif cmd.action == "reset_charge_slot":
                reqs = commands.reset_charge_slot(int(p["slot"]), slot_map)
            elif cmd.action == "set_discharge_slot":
                slot_idx = int(p["slot"])
                reqs = commands.set_discharge_slot(slot_idx, self._parse_time_slot(p["start"], p["end"]), slot_map)
            elif cmd.action == "reset_discharge_slot":
                reqs = commands.reset_discharge_slot(int(p["slot"]), slot_map)
            elif cmd.action == "set_battery_pause_mode":
                reqs = commands.set_battery_pause_mode(BatteryPauseMode(int(p["mode"])))
            elif cmd.action == "set_pause_slot":
                reqs = commands.set_pause_slot(self._parse_time_slot(p["start"], p["end"]))
            elif cmd.action == "reset_pause_slot":
                reqs = commands.set_pause_slot(None)
            elif cmd.action == "set_battery_charge_limit_ac":
                reqs = commands.set_battery_charge_limit_ac(int(p["limit"]))
            elif cmd.action == "set_battery_discharge_limit_ac":
                reqs = commands.set_battery_discharge_limit_ac(int(p["limit"]))
            elif cmd.action == "set_export_priority":
                reqs = commands.set_export_priority(ExportPriority(int(p["priority"])))
            elif cmd.action == "set_enable_eps":
                reqs = commands.set_enable_eps(bool(p["enabled"]))
            elif cmd.action == "set_enable_rtc":
                reqs = commands.set_enable_rtc(bool(p["enabled"]))
            elif cmd.action == "set_inverter_reboot":
                reqs = commands.set_inverter_reboot()
            elif cmd.action == "sync_time":
                reqs = commands.set_system_date_time(datetime.now())
            else:
                self.log_event("error", f"Unknown inverter action: {cmd.action}")
                return False

            await inv_client.one_shot_command(reqs, timeout=2.5, retries=2)
            self.log_event("success", f"Inverter: {cmd.description}")
            return True
        except Exception as exc:
            err_detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            if cmd.action in ("set_charge_slot", "reset_charge_slot") and int(cmd.params.get("slot", 0) or 0) == 2 and len(inv_client.plant.inverter.slot_map.charge_slots) > 2:
                err_detail += " (Charge Slot 2 HR 31/32 is rejected by this firmware; use Slot 1 or Slots 3–10)"
            self.log_event("error", f"Inverter command failed ({cmd.description}): {err_detail}")
            return False

    def _write_evc_registers(self, addr: int, values: list[int]) -> bool:
        if not self.evc_ip:
            return False
        client = ModbusTcpClient(self.evc_ip, port=self.evc_port, timeout=2.5)
        try:
            if not client.connect():
                return False
            res = client.write_registers(addr, values, device_id=1)
            return not (hasattr(res, "isError") and res.isError())
        except Exception:
            return False
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _write_evc_current_limit(self, amps: int) -> bool:
        clamped = max(6, min(32, int(amps)))
        ok = self._write_evc_registers(91, [clamped * 10])
        if ok:
            self.snapshot.ev_charger.setpoint_limit_a = float(clamped)
        return ok

    def _write_evc_charge_control(self, mode: str) -> bool:
        val = EVC_CHARGE_CONTROL.index(mode)
        ok = self._write_evc_registers(95, [val])
        if ok:
            self.snapshot.ev_charger.charge_control_state = mode
        return ok

    async def _apply_evc_smart_controls(
        self, inv_client: GivInverterClient | None, inv_detected: bool
    ) -> None:
        evc = self.snapshot.ev_charger
        inv = self.snapshot.inverter

        if not evc.cable_connected or self.config.evc_charging_mode != "Solar":
            self._evc_low_solar_since = None
            self._evc_solar_started = False

        is_active = (
            evc.charging_state in ("Charging", "Starting")
            or evc.charge_control_state == "Start"
            or evc.active_power_w > 150
        )
        if not is_active:
            self._evc_solar_started = False

        if evc.charging_state == "Scheduled":
            self._evc_was_scheduled = True
            self._evc_scheduled_session = False
        elif is_active and self._evc_was_scheduled:
            self._evc_scheduled_session = True
            self._evc_was_scheduled = False
        elif not is_active:
            self._evc_was_scheduled = False
            self._evc_scheduled_session = False

        user_max_a = max(6, min(32, int(self.config.evc_user_charge_limit_a)))

        # 0. Native Off-Peak Scheduler (local Pi clock, independent of GivEnergy Cloud)
        in_win, active_win, _ = self._get_active_evc_schedule_window()
        evc.schedule_active = bool(self.config.evc_schedule_enabled and in_win)
        if self.evc_enabled and evc.connected:
            if self.config.evc_schedule_enabled and in_win and active_win is not None:
                win_start, win_end, win_label = active_win
                win_key = f"{win_start}-{win_end}"
                if not evc.cable_connected:
                    self._evc_schedule_handled_window = None
                elif evc.error_code == 0 and self._evc_schedule_handled_window != win_key:
                    full_amps = max(6, min(int(evc.evse_max_current_a or 32), user_max_a))
                    if not is_active:
                        limit_ok = await asyncio.to_thread(self._write_evc_current_limit, full_amps)
                        if not limit_ok:
                            self.log_event(
                                "warning",
                                f"EV Charger (Schedule): Failed to set full charge limit ({full_amps}A) before start; retrying",
                            )
                        else:
                            ok = await asyncio.to_thread(self._write_evc_charge_control, "Start")
                            if ok:
                                is_active = True
                                self._evc_schedule_started = True
                                self._evc_schedule_handled_window = win_key
                                self.log_event(
                                    "info",
                                    f"EV Charger (Schedule): Set charge limit to {full_amps}A and started charging for {win_label} ({win_start}–{win_end})",
                                )
                    else:
                        if abs(full_amps - evc.setpoint_limit_a) >= 1 or abs(full_amps - evc.charge_limit_a) >= 1:
                            await asyncio.to_thread(self._write_evc_current_limit, full_amps)
                        self._evc_solar_started = False
                        self._evc_low_solar_since = None
                        self._evc_schedule_started = True
                        self._evc_schedule_handled_window = win_key
            elif self.config.evc_schedule_enabled and not in_win:
                if self._evc_schedule_prev_in_window or self._evc_schedule_started:
                    if is_active:
                        ok = await asyncio.to_thread(self._write_evc_charge_control, "Stop")
                        if ok:
                            is_active = False
                            self.log_event(
                                "info",
                                "EV Charger (Schedule): Stopped charging — off-peak schedule window ended",
                            )
                    self._evc_schedule_started = False
                self._evc_schedule_handled_window = None
            else:
                self._evc_schedule_started = False
                self._evc_schedule_handled_window = None
            self._evc_schedule_prev_in_window = bool(self.config.evc_schedule_enabled and in_win)

        # Scheduled (or externally started in Solar mode) sessions must run at full power unimpeded
        scheduled_or_external = (
            evc.charging_state == "Scheduled"
            or self._evc_scheduled_session
            or (self.config.evc_schedule_enabled and in_win)
            or (self.config.evc_charging_mode == "Solar" and is_active and not self._evc_solar_started)
        )

        # 1. Dynamic Solar / Hybrid current limit adjustment (or full-power restore for scheduled sessions)
        now_ts = time.time()
        if (
            self.evc_enabled
            and evc.connected
            and evc.cable_connected
            and self.config.evc_charging_mode in ("Solar", "Hybrid")
            and scheduled_or_external
            and (now_ts - self._last_evc_solar_adjust_ts >= 15.0)
        ):
            self._last_evc_solar_adjust_ts = now_ts
            if abs(user_max_a - evc.setpoint_limit_a) >= 1 or abs(user_max_a - evc.charge_limit_a) >= 1:
                ok = await asyncio.to_thread(self._write_evc_current_limit, user_max_a)
                if ok:
                    self.log_event(
                        "info",
                        f"EV Charger (Scheduled): Restored full charge limit to {user_max_a}A",
                    )
        elif (
            self.evc_enabled
            and evc.connected
            and inv.connected
            and evc.cable_connected
            and self.config.evc_charging_mode in ("Solar", "Hybrid")
            and not scheduled_or_external
            and (now_ts - self._last_evc_solar_adjust_ts >= 15.0)
        ):
            self._last_evc_solar_adjust_ts = now_ts
            non_ev_load_w = max(0, inv.p_load_demand - evc.active_power_w)
            excess_solar_w = max(0, inv.p_pv_total - non_ev_load_w)
            split_pct = max(0, min(100, int(self.config.evc_solar_export_split_pct)))
            ev_share = (100 - split_pct) / 100.0
            allocated_solar_w = excess_solar_w * ev_share
            v_ac = inv.v_ac1 if inv.v_ac1 > 100 else (evc.voltage_l1_v if evc.voltage_l1_v > 100 else 230.0)
            solar_amps = allocated_solar_w / v_ac

            target_amps: int | None = None
            if self.config.evc_charging_mode == "Solar":
                if solar_amps >= 6:
                    self._evc_low_solar_since = None
                    target_amps = min(user_max_a, max(6, int(solar_amps)))
                else:
                    if is_active and self._evc_solar_started:
                        if self._evc_low_solar_since is None:
                            self._evc_low_solar_since = now_ts
                        elif now_ts - self._evc_low_solar_since >= 45.0:
                            ok = await asyncio.to_thread(self._write_evc_charge_control, "Stop")
                            if ok:
                                self._evc_low_solar_since = None
                                self._evc_solar_started = False
                                self.log_event(
                                    "info",
                                    f"EV Charger (Solar): Stopped charging — excess solar ({int(allocated_solar_w)}W) below 6A minimum",
                                )
            elif self.config.evc_charging_mode == "Hybrid":
                target_amps = min(user_max_a, max(6, 6 + int(solar_amps)))

            if target_amps is not None and abs(target_amps - evc.setpoint_limit_a) >= 1:
                ok = await asyncio.to_thread(self._write_evc_current_limit, target_amps)
                if ok:
                    self.log_event(
                        "info",
                        f"EV Charger ({self.config.evc_charging_mode}): Adjusted charge limit to {target_amps}A "
                        f"(excess solar {int(excess_solar_w)}W, export split {split_pct}%)",
                    )

            if (
                self.config.evc_charging_mode == "Solar"
                and solar_amps >= 6
                and evc.charging_state not in ("Charging", "Starting", "Scheduled")
                and evc.charge_control_state != "Start"
                and evc.error_code == 0
            ):
                ok = await asyncio.to_thread(self._write_evc_charge_control, "Start")
                if ok:
                    self._evc_solar_started = True
                    self.log_event(
                        "info",
                        f"EV Charger (Solar): Auto-started charging ({int(allocated_solar_w)}W excess solar available)",
                    )

        # 2. Unified Low-SoC Discharge Guard & Max Battery Discharge Power to EV
        inv.low_soc_discharge_guard_enabled = bool(self.config.low_soc_discharge_guard)
        if inv_client is not None and inv_detected and inv.connected:
            reserve_soc = max(4, int(inv.battery_soc_reserve or 4))
            if self.config.low_soc_discharge_guard:
                if inv.battery_soc <= reserve_soc:
                    self._low_soc_guard_active = True
                elif inv.battery_soc >= min(100, reserve_soc + 2):
                    self._low_soc_guard_active = False
            else:
                self._low_soc_guard_active = False
            inv.low_soc_discharge_guard_active = self._low_soc_guard_active

            ev_active = self.evc_enabled and (evc.charging_state == "Charging" or evc.active_power_w > 150)
            max_w = max(0, min(5000, int(self.config.evc_max_battery_discharge_w)))
            ev_target_pct = (
                0
                if max_w <= 0
                else min(50, max(1, round(max_w * 50 / max(1, inv.battery_max_power))))
            )

            if self._low_soc_guard_active:
                target_pct = 0
                reason = "low_soc"
            elif ev_active:
                target_pct = ev_target_pct
                reason = "evc"
            else:
                target_pct = None
                reason = None

            if target_pct is not None:
                if inv.battery_discharge_limit != target_pct:
                    if self._saved_inv_discharge_limit is None and inv.battery_discharge_limit > 0:
                        self._saved_inv_discharge_limit = inv.battery_discharge_limit
                    try:
                        reqs = commands.set_battery_discharge_limit(target_pct)
                        await inv_client.one_shot_command(reqs, timeout=2.5, retries=2)
                        inv.battery_discharge_limit = target_pct
                        if reason == "low_soc":
                            if self._low_soc_last_logged_active is not True:
                                self._low_soc_last_logged_active = True
                                self.log_event(
                                    "warning",
                                    f"Battery Guard: SoC ({inv.battery_soc}%) <= Reserve ({reserve_soc}%) — capped discharge limit to 0%",
                                )
                        elif self._evc_last_logged_cap_pct != target_pct:
                            self._evc_last_logged_cap_pct = target_pct
                            self.log_event(
                                "info",
                                f"EV Charger: Capped inverter battery discharge limit to {target_pct}% (~{max_w}W) during EV charge",
                            )
                    except Exception as exc:
                        self.log_event("warning", f"Failed to apply battery discharge limit: {exc}")
            elif self._saved_inv_discharge_limit is not None:
                restore_pct = self._saved_inv_discharge_limit
                try:
                    reqs = commands.set_battery_discharge_limit(restore_pct)
                    await inv_client.one_shot_command(reqs, timeout=2.5, retries=2)
                    inv.battery_discharge_limit = restore_pct
                    self._saved_inv_discharge_limit = None
                    self._evc_last_logged_cap_pct = None
                    self._low_soc_last_logged_active = None
                    self.log_event(
                        "info",
                        f"Battery Guard / EV Charger: Restored inverter battery discharge limit to {restore_pct}%",
                    )
                except Exception as exc:
                    self.log_event("warning", f"Failed to restore inverter battery discharge limit: {exc}")
        else:
            inv.low_soc_discharge_guard_active = False

    def _execute_evc_command(self, cmd: WebControlCommand) -> bool:
        p = cmd.params
        if cmd.action == "set_charging_mode":
            prev_mode = self._normalize_evc_mode(self.config.evc_charging_mode)
            mode = self._normalize_evc_mode(p.get("mode"))
            self.config.evc_charging_mode = mode
            self.snapshot.ev_charger.charging_mode = mode
            self._last_evc_solar_adjust_ts = 0.0
            self._evc_low_solar_since = None
            self._evc_solar_started = False
            self._save_config_safe()
            if mode == "Grid" and self.evc_enabled and self.evc_ip:
                evc = self.snapshot.ev_charger
                is_active = (
                    evc.charging_state == "Charging"
                    or evc.charge_control_state == "Start"
                    or evc.active_power_w > 150
                )
                if prev_mode in ("Solar", "Hybrid") and is_active:
                    if self._write_evc_charge_control("Stop"):
                        self.log_event(
                            "info",
                            f"EV Charger: Stopped active {prev_mode} charge session on switch to Grid mode",
                        )
                amps = max(6, min(32, int(self.config.evc_user_charge_limit_a)))
                self._write_evc_current_limit(amps)
            self.log_event("success", f"EV Charger: {cmd.description}")
            return True

        if cmd.action == "set_max_battery_discharge":
            power_w = max(0, min(5000, int(p.get("power_w", 0))))
            self.config.evc_max_battery_discharge_w = power_w
            self.snapshot.ev_charger.max_battery_discharge_w = power_w
            self._save_config_safe()
            self.log_event("success", f"EV Charger: {cmd.description}")
            return True

        if cmd.action == "set_solar_export_split":
            split_pct = max(0, min(100, int(p.get("split_pct", 0))))
            self.config.evc_solar_export_split_pct = split_pct
            self.snapshot.ev_charger.solar_export_split_pct = split_pct
            self._last_evc_solar_adjust_ts = 0.0
            self._save_config_safe()
            self.log_event("success", f"EV Charger: {cmd.description}")
            return True

        if cmd.action == "set_schedule":
            if "enabled" in p:
                self.config.evc_schedule_enabled = bool(p["enabled"])
            if "use_tariff" in p:
                self.config.evc_schedule_use_tariff = bool(p["use_tariff"])
            if "start" in p:
                self.config.evc_schedule_start = self._normalize_hhmm(
                    p.get("start"), self.config.evc_schedule_start
                )
            if "end" in p:
                self.config.evc_schedule_end = self._normalize_hhmm(
                    p.get("end"), self.config.evc_schedule_end
                )
            self._evc_schedule_handled_window = None
            if not self.config.evc_schedule_enabled:
                self._evc_schedule_started = False
                self._evc_schedule_prev_in_window = False
            self._sync_evc_config_to_snapshot()
            self._save_config_safe()
            self.log_event("success", f"EV Charger: {cmd.description}")
            return True

        client = ModbusTcpClient(self.evc_ip, port=self.evc_port, timeout=3.0)
        try:
            if not client.connect():
                self.log_event("error", f"EV Charger offline ({self.evc_ip}:{self.evc_port})")
                return False

            if cmd.action == "charge_control":
                raw_mode = str(p.get("mode") or p.get("action") or "Ready").strip().capitalize()
                val = EVC_CHARGE_CONTROL.index(raw_mode)
                res = client.write_registers(95, [val], device_id=1)
                if not (hasattr(res, "isError") and res.isError()):
                    self._evc_solar_started = False
                    self._evc_low_solar_since = None
                    if raw_mode == "Stop":
                        self._evc_schedule_started = False
            elif cmd.action == "plug_and_go":
                val = 0 if p.get("enabled") else 1
                res = client.write_registers(93, [val], device_id=1)
                if not (hasattr(res, "isError") and res.isError()):
                    self.snapshot.ev_charger.plug_and_go_enabled = bool(p.get("enabled"))
            elif cmd.action == "current_limit":
                amps = max(6, min(32, int(p["amps"])))
                res = client.write_registers(91, [amps * 10], device_id=1)
                if not (hasattr(res, "isError") and res.isError()):
                    self.config.evc_user_charge_limit_a = amps
                    self.snapshot.ev_charger.setpoint_limit_a = float(amps)
                    self._save_config_safe()
            elif cmd.action == "sync_time":
                now = datetime.now()
                res = client.write_registers(
                    97, [now.year, now.month, now.day, now.hour, now.minute, now.second], device_id=1
                )
            else:
                self.log_event("error", f"Unknown EVC action: {cmd.action}")
                return False

            if hasattr(res, "isError") and res.isError():
                self.log_event("error", f"EV Charger rejected command ({cmd.description}): {res}")
                return False

            self.log_event("success", f"EV Charger: {cmd.description}")
            return True
        except Exception as exc:
            self.log_event("error", f"EV Charger command error ({cmd.description}): {exc}")
            return False
        finally:
            try:
                client.close()
            except Exception:
                pass
