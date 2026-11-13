"""Background telemetry polling and active hardware control worker."""

from __future__ import annotations

import asyncio
import copy
import logging
import queue
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from givenergy_modbus.client import commands
from givenergy_modbus.client.client import Client as GivInverterClient
from givenergy_modbus.exceptions import PlantTopologyMismatch, RefreshFailed, RefreshPartiallySucceeded
from givenergy_modbus.model import TimeSlot
from givenergy_modbus.model.inverter import Model
from givenergy_modbus.model.plant import PlantCapabilities
from pymodbus.client import ModbusTcpClient
from PySide6.QtCore import QThread, Signal

from givenergy_app.models import (
    BatteryTelemetry,
    EVChargerTelemetry,
    InverterTelemetry,
    PlantSnapshot,
    PowerHistoryPoint,
)

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

EVC_CABLE_STATUS = [
    "Not Connected",
    "Connected",
]

EVC_CHARGE_CONTROL = [
    "Ready",
    "Start",
    "Stop",
]

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
class ControlCommand:
    """Represents a user-triggered hardware write command."""

    target: str  # "inverter" or "evc"
    action: str
    params: dict[str, Any]
    description: str


class TelemetryWorker(QThread):
    """Background QThread running an asyncio loop to poll Inverter & EV Charger and execute controls."""

    snapshot_updated = Signal(object)  # Emits PlantSnapshot
    status_message = Signal(str, str)  # (level: "info"|"success"|"warning"|"error", message: str)

    def __init__(
        self,
        inverter_ip: str = "192.168.0.10",
        inverter_port: int = 8899,
        evc_ip: str = "192.168.0.11",
        evc_port: int = 502,
        poll_interval_s: float = 5.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.inverter_ip = inverter_ip
        self.inverter_port = inverter_port
        self.evc_ip = evc_ip
        self.evc_port = evc_port
        self.poll_interval_s = poll_interval_s

        self._running = True
        self._force_refresh_flag = False
        self._cmd_queue: queue.Queue[ControlCommand] = queue.Queue()

        self._snapshot = PlantSnapshot()
        self._history: list[PowerHistoryPoint] = []
        self._max_history_points = 720  # 1 hour at 5s intervals

    def update_endpoints(self, inverter_ip: str, evc_ip: str, poll_interval_s: float) -> None:
        """Update target IP addresses or polling interval dynamically."""
        ip_changed = (inverter_ip != self.inverter_ip) or (evc_ip != self.evc_ip)
        self.inverter_ip = inverter_ip.strip()
        self.evc_ip = evc_ip.strip()
        self.poll_interval_s = max(2.0, float(poll_interval_s))
        if ip_changed:
            self._cmd_queue.put(
                ControlCommand(
                    target="system",
                    action="reconnect",
                    params={},
                    description=f"Reconnect to Inverter ({self.inverter_ip}) & EVC ({self.evc_ip})",
                )
            )
        self._force_refresh_flag = True

    def trigger_immediate_refresh(self) -> None:
        """Wake up the poll loop immediately."""
        self._force_refresh_flag = True

    def enqueue_command(self, target: str, action: str, params: dict[str, Any], description: str) -> None:
        """Queue a control command for execution on the worker thread."""
        self._cmd_queue.put(ControlCommand(target=target, action=action, params=params, description=description))
        self._force_refresh_flag = True

    def stop(self) -> None:
        """Request clean shutdown of the background thread."""
        self._running = False
        self._force_refresh_flag = True

    def run(self) -> None:
        """Entry point for QThread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main_loop())
        except Exception as exc:
            logger.exception("Fatal error in TelemetryWorker loop: %s", exc)
        finally:
            loop.close()

    async def _main_loop(self) -> None:
        inv_client: GivInverterClient | None = None
        inv_detected = False
        cycle_count = 0

        self.status_message.emit(
            "info",
            f"Connecting to Hybrid Gen 3 Inverter ({self.inverter_ip}:{self.inverter_port}) "
            f"and EV Charger ({self.evc_ip}:{self.evc_port})...",
        )

        while self._running:
            need_config_reload = (cycle_count % 6 == 0)

            # 1. Process any pending control commands first
            while not self._cmd_queue.empty():
                cmd = self._cmd_queue.get_nowait()
                if cmd.target == "system" and cmd.action == "reconnect":
                    if inv_client is not None:
                        try:
                            await inv_client.close()
                        except Exception:
                            pass
                        inv_client = None
                        inv_detected = False
                    self.status_message.emit("info", cmd.description)
                    continue

                if cmd.target == "inverter":
                    inv_client, inv_detected = await self._ensure_inverter_connected(inv_client, inv_detected)
                    if inv_client and inv_detected:
                        ok = await self._execute_inverter_command(inv_client, cmd)
                        if ok:
                            need_config_reload = True
                    else:
                        self.status_message.emit("error", f"Cannot execute '{cmd.description}': Inverter offline.")
                elif cmd.target == "evc":
                    await asyncio.to_thread(self._execute_evc_command, cmd)

            if not self._running:
                break

            # 2. Poll EV Charger first (fast ~50ms Modbus TCP read) and emit initial state if inverter not yet ready
            await asyncio.to_thread(self._poll_evc)
            if not inv_detected and self._snapshot.ev_charger.connected:
                self.snapshot_updated.emit(copy.deepcopy(self._snapshot))

            # 3. Poll Inverter & Battery
            inv_client, inv_detected = await self._poll_inverter(
                inv_client, inv_detected, load_config=need_config_reload
            )

            # 4. Record history sample if inverter or EVC is connected
            if self._snapshot.inverter.connected or self._snapshot.ev_charger.connected:
                pt = PowerHistoryPoint(
                    timestamp=datetime.now(),
                    solar_w=self._snapshot.inverter.p_pv_total,
                    battery_w=self._snapshot.inverter.p_battery,
                    grid_w=self._snapshot.inverter.p_grid_out,
                    home_w=self._snapshot.inverter.p_load_demand,
                    ev_w=self._snapshot.ev_charger.active_power_w,
                    soc_pct=self._snapshot.battery.soc if self._snapshot.battery.present else self._snapshot.inverter.battery_soc,
                )
                self._history.append(pt)
                if len(self._history) > self._max_history_points:
                    self._history = self._history[-self._max_history_points :]
                self._snapshot.history = list(self._history)

            # 5. Emit deep copy of snapshot to UI
            self.snapshot_updated.emit(copy.deepcopy(self._snapshot))
            cycle_count += 1

            # 6. Wait for next poll interval or immediate wakeup
            self._force_refresh_flag = False
            waited = 0.0
            step = 0.1
            while self._running and not self._force_refresh_flag and self._cmd_queue.empty():
                if waited >= self.poll_interval_s:
                    break
                await asyncio.sleep(step)
                waited += step

        # Clean up inverter client on exit
        if inv_client is not None:
            try:
                await inv_client.close()
            except Exception:
                pass

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
                self._snapshot.inverter.connected = False
                self._snapshot.inverter.error_message = f"Connection failed: {exc}"
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
                self.status_message.emit(
                    "success",
                    f"Connected to Inverter {inv_client.plant.inverter_serial_number or 'Gen 3'} at {self.inverter_ip}:{self.inverter_port}",
                )
            except Exception as exc:
                self._snapshot.inverter.connected = False
                self._snapshot.inverter.error_message = f"Discovery failed: {exc}"
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
                except RefreshPartiallySucceeded:
                    pass
            try:
                await inv_client.refresh(timeout=2.5, retries=2)
            except RefreshPartiallySucceeded:
                pass

            self._extract_inverter_and_battery(inv_client)
            self._snapshot.inverter.connected = True
            self._snapshot.inverter.last_updated = datetime.now()
            self._snapshot.inverter.error_message = ""
        except RefreshFailed as exc:
            self._snapshot.inverter.connected = False
            self._snapshot.inverter.error_message = f"Refresh failed: {exc}"
            self.status_message.emit("warning", f"Inverter poll failed ({exc}); will reconnect next cycle.")
            try:
                await inv_client.close()
            except Exception:
                pass
            return None, False
        except Exception as exc:
            self._snapshot.inverter.connected = False
            self._snapshot.inverter.error_message = f"Error: {exc}"
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

        t_inv = self._snapshot.inverter
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

        # Power flows
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

        # PV String Electricals
        t_inv.v_pv1 = float(getattr(inv, "v_pv1", 0.0) or 0.0)
        t_inv.i_pv1 = float(getattr(inv, "i_pv1", 0.0) or 0.0)
        t_inv.v_pv2 = float(getattr(inv, "v_pv2", 0.0) or 0.0)
        t_inv.i_pv2 = float(getattr(inv, "i_pv2", 0.0) or 0.0)

        # AC Electricals
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

        # Battery Inverter-side
        t_inv.battery_soc = int(getattr(inv, "battery_soc", 0) or 0)
        t_inv.v_battery = float(getattr(inv, "v_battery", 0.0) or 0.0)
        t_inv.i_battery = float(getattr(inv, "i_battery", 0.0) or 0.0)
        cs_label = getattr(inv, "charge_status_label", None)
        t_inv.charge_status_label = getattr(cs_label, "name", str(cs_label)) if cs_label is not None else "—"
        t_inv.battery_capacity_ah = int(getattr(inv, "battery_capacity_ah", 186) or 186)
        t_inv.battery_capacity_kwh = float(getattr(inv, "battery_capacity_kwh", 9.52) or 9.52)
        t_inv.battery_max_power = int(getattr(inv, "battery_max_power", 3600) or 3600)

        # Temperatures
        t_inv.t_inverter_heatsink = float(getattr(inv, "t_inverter_heatsink", 0.0) or 0.0)
        t_inv.t_charger = float(getattr(inv, "t_charger", 0.0) or 0.0)
        t_inv.t_battery = float(getattr(inv, "t_battery", 0.0) or 0.0)

        # Daily Energy
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

        # Lifetime Energy
        t_inv.e_pv_generation_total = float(getattr(inv, "e_pv_generation_total", 0.0) or 0.0)
        t_inv.e_grid_in_total = float(getattr(inv, "e_grid_in_total", 0.0) or 0.0)
        t_inv.e_grid_out_total = float(getattr(inv, "e_grid_out_total", 0.0) or 0.0)
        t_inv.e_inverter_in_total = float(getattr(inv, "e_inverter_in_total", 0.0) or 0.0)
        t_inv.e_battery_throughput = float(getattr(inv, "e_battery_throughput", 0.0) or 0.0)
        t_inv.e_self_consumption_total = float(getattr(inv, "e_self_consumption_total", 0.0) or 0.0)

        # Controls & Modes
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
        t_inv.battery_charge_limit = int(getattr(inv, "battery_charge_limit", 50) or 50)
        t_inv.battery_discharge_limit = int(getattr(inv, "battery_discharge_limit", 50) or 50)
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

        t_inv.charge_target_soc_1 = int(getattr(inv, "charge_target_soc_1", 100) or 100)
        t_inv.discharge_target_soc_1 = int(getattr(inv, "discharge_target_soc_1", 4) or 4)

        # Extract 9.5kWh Battery Pack BMS Telemetry
        t_bat = self._snapshot.battery
        if plant.batteries:
            bat = plant.batteries[0]
            t_bat.present = True
            t_bat.serial_number = str(getattr(bat, "serial_number", "—") or "—")
            t_bat.bms_firmware_version = int(getattr(bat, "bms_firmware_version", 0) or 0)
            t_bat.soc = int(getattr(bat, "soc", t_inv.battery_soc) or t_inv.battery_soc)
            t_bat.num_cells = int(getattr(bat, "num_cells", 16) or 16)
            t_bat.num_cycles = int(getattr(bat, "num_cycles", 0) or 0)
            t_bat.v_out = float(getattr(bat, "v_out", 0.0) or 0.0)
            t_bat.v_cells_sum = float(getattr(bat, "v_cells_sum", 0.0) or 0.0)
            t_bat.i_battery = float(getattr(bat, "i_battery", 0.0) or 0.0)

            cells = []
            for idx in range(1, 17):
                cv = float(getattr(bat, f"v_cell_{idx:02d}", 0.0) or 0.0)
                cells.append(cv)
            t_bat.cell_voltages = cells

            t_bat.cap_design = float(getattr(bat, "cap_design", 186.0) or 186.0)
            t_bat.cap_calibrated = float(getattr(bat, "cap_calibrated", 186.0) or 186.0)
            t_bat.cap_remaining = float(getattr(bat, "cap_remaining", 0.0) or 0.0)
            t_bat.remaining_energy_nominal_wh = int(getattr(bat, "remaining_energy_nominal_wh", 0) or 0)
            t_bat.remaining_energy_measured_wh = int(getattr(bat, "remaining_energy_measured_wh", 0) or 0)
            t_bat.e_battery_charge_total = float(getattr(bat, "e_battery_charge_total", 0.0) or 0.0)
            t_bat.e_battery_discharge_total = float(getattr(bat, "e_battery_discharge_total", 0.0) or 0.0)

            t_bat.t_bms_mosfet = float(getattr(bat, "t_bms_mosfet", 0.0) or 0.0)
            t_bat.t_max = float(getattr(bat, "t_max", 0.0) or 0.0)
            t_bat.t_min = float(getattr(bat, "t_min", 0.0) or 0.0)
            t_bat.t_cells_01_04 = float(getattr(bat, "t_cells_01_04", 0.0) or 0.0)
            t_bat.t_cells_05_08 = float(getattr(bat, "t_cells_05_08", 0.0) or 0.0)
            t_bat.t_cells_09_12 = float(getattr(bat, "t_cells_09_12", 0.0) or 0.0)
            t_bat.t_cells_13_16 = float(getattr(bat, "t_cells_13_16", 0.0) or 0.0)
            t_bat.warning_1 = int(getattr(bat, "warning_1", 0) or 0)
            t_bat.warning_2 = int(getattr(bat, "warning_2", 0) or 0)
        else:
            t_bat.present = False

    def _poll_evc(self) -> None:
        """Poll GivEnergy EV Charger over Modbus TCP (Port 502)."""
        t_evc = self._snapshot.ev_charger
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

            # 32-bit total meter energy at HR 28 (high word) & HR 29 (low word), unit 0.1 kWh
            raw_meter_energy = (int(regs[28]) << 16) | int(regs[29])
            t_evc.total_meter_energy_kwh = round(raw_meter_energy / 10.0, 1)

            t_evc.evse_max_current_a = round(regs[32] / 10.0, 1)
            t_evc.evse_min_current_a = round(regs[34] / 10.0, 1)
            t_evc.charge_limit_a = round(regs[36] / 10.0, 1)
            t_evc.setpoint_limit_a = round(regs[91] / 10.0, 1) if regs[91] > 0 else t_evc.charge_limit_a

            # Serial number in ASCII at HR 38..68
            sn_chars = [chr(c) for c in regs[38:69] if 32 <= c <= 126]
            t_evc.serial_number = "".join(sn_chars).strip() or "—"

            # Session energy & duration
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

    async def _execute_inverter_command(self, inv_client: GivInverterClient, cmd: ControlCommand) -> bool:
        """Build and send a validated command to the Hybrid Gen 3 inverter."""
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
            elif cmd.action == "set_battery_soc_reserve":
                reqs = commands.set_battery_soc_reserve(int(p["soc"]))
            elif cmd.action == "set_battery_power_reserve":
                reqs = commands.set_battery_power_reserve(int(p["soc"]))
            elif cmd.action == "set_battery_charge_limit":
                reqs = commands.set_battery_charge_limit(int(p["limit"]))
            elif cmd.action == "set_battery_discharge_limit":
                reqs = commands.set_battery_discharge_limit(int(p["limit"]))
            elif cmd.action == "set_charge_slot_1":
                slot = TimeSlot(start=p["start"], end=p["end"])
                reqs = commands.set_charge_slot(1, slot, slot_map)
            elif cmd.action == "set_discharge_slot_1":
                slot = TimeSlot(start=p["start"], end=p["end"])
                reqs = commands.set_discharge_slot(1, slot, slot_map)
            elif cmd.action == "sync_time":
                reqs = commands.set_system_date_time(datetime.now())
            else:
                self.status_message.emit("error", f"Unknown inverter action: {cmd.action}")
                return False

            await inv_client.one_shot_command(reqs, timeout=2.5, retries=2)
            self.status_message.emit("success", f"Inverter: {cmd.description}")
            return True
        except Exception as exc:
            self.status_message.emit("error", f"Inverter command failed ({cmd.description}): {exc}")
            return False

    def _execute_evc_command(self, cmd: ControlCommand) -> bool:
        """Send a Modbus TCP holding register write to the GivEnergy EV Charger."""
        client = ModbusTcpClient(self.evc_ip, port=self.evc_port, timeout=3.0)
        try:
            if not client.connect():
                self.status_message.emit("error", f"EV Charger offline ({self.evc_ip}:{self.evc_port})")
                return False

            p = cmd.params
            if cmd.action == "charge_control":
                # 0 = Ready, 1 = Start, 2 = Stop on HR 95
                mode_str = p["mode"]
                val = EVC_CHARGE_CONTROL.index(mode_str)
                res = client.write_registers(95, [val], device_id=1)
            elif cmd.action == "plug_and_go":
                # 0 = Enable, 1 = Disable on HR 93
                val = 0 if p["enabled"] else 1
                res = client.write_registers(93, [val], device_id=1)
            elif cmd.action == "current_limit":
                # Value in 0.1A (60..320) on HR 91
                amps = max(6, min(32, int(p["amps"])))
                res = client.write_registers(91, [amps * 10], device_id=1)
            elif cmd.action == "sync_time":
                now = datetime.now()
                res = client.write_registers(
                    97, [now.year, now.month, now.day, now.hour, now.minute, now.second], device_id=1
                )
            else:
                self.status_message.emit("error", f"Unknown EVC action: {cmd.action}")
                return False

            if hasattr(res, "isError") and res.isError():
                self.status_message.emit("error", f"EV Charger rejected command ({cmd.description}): {res}")
                return False

            self.status_message.emit("success", f"EV Charger: {cmd.description}")
            return True
        except Exception as exc:
            self.status_message.emit("error", f"EV Charger command error ({cmd.description}): {exc}")
            return False
        finally:
            try:
                client.close()
            except Exception:
                pass
