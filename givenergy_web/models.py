"""Data models for GivEnergy Hybrid Gen 3 Inverter, 9.5kWh Battery, and EV Charger."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time


@dataclass
class InverterTelemetry:
    """Live telemetry and configuration from the GivEnergy Hybrid Gen 3 Inverter."""

    connected: bool = False
    last_updated: datetime | None = None
    error_message: str = ""

    # Identity & Status
    serial_number: str = "—"
    model_name: str = "Hybrid Gen 3"
    device_type_code: str = "2001"
    firmware_version: str = "—"
    arm_firmware_version: int = 0
    dsp_firmware_version: int = 0
    bms_firmware_version: int = 0
    modbus_version: str = "—"
    modbus_address: int = 17
    status: str = "—"
    system_time: datetime | None = None
    work_time_hours: int = 0
    fault_code: str = "0000"
    warning_code: str = "0000"
    fault_messages: list[str] = field(default_factory=list)
    charger_warning_messages: list[str] = field(default_factory=list)

    # Real-time Power Flows (W)
    p_pv1: int = 0
    p_pv2: int = 0
    p_pv_total: int = 0
    p_battery: int = 0  # Positive = discharging, Negative = charging
    battery_charge_power: int = 0
    battery_discharge_power: int = 0
    p_grid_out: int = 0  # Positive = exporting to grid, Negative = importing from grid
    grid_import_power: int = 0
    grid_export_power: int = 0
    p_grid_apparent: int = 0
    p_load_demand: int = 0
    p_backup: int = 0
    p_inverter_out: int = 0

    # PV String Electricals
    v_pv1: float = 0.0
    i_pv1: float = 0.0
    v_pv2: float = 0.0
    i_pv2: float = 0.0

    # AC Grid & Output Electricals
    v_ac1: float = 0.0
    i_grid_port: float = 0.0
    f_ac1: float = 0.0
    v_ac1_output: float = 0.0
    i_ac1: float = 0.0
    f_ac1_output: float = 0.0
    v_p_bus: float = 0.0
    v_highbrigh_bus: float = 0.0
    power_factor: float = 0.0
    inverter_max_power: int = 5000
    grid_port_max_power_output: int = 3680

    # Battery Inverter-side Metrics
    battery_soc: int = 0
    v_battery: float = 0.0
    i_battery: float = 0.0
    charge_status_label: str = "—"
    battery_capacity_ah: int = 186
    battery_capacity_kwh: float = 9.52
    battery_max_power: int = 3600

    # Temperatures (°C)
    t_inverter_heatsink: float = 0.0
    t_charger: float = 0.0
    t_battery: float = 0.0

    # Daily Energy Totals (kWh)
    e_pv1_day: float = 0.0
    e_pv2_day: float = 0.0
    e_pv_generation_today: float = 0.0
    e_consumption_today: float = 0.0
    e_self_consumption_today: float = 0.0
    e_grid_in_day: float = 0.0
    e_grid_out_day: float = 0.0
    e_battery_charge_today: float = 0.0
    e_battery_discharge_today: float = 0.0
    e_ac_charge_today: float = 0.0

    # Lifetime Energy Totals (kWh)
    e_pv_generation_total: float = 0.0
    e_grid_in_total: float = 0.0
    e_grid_out_total: float = 0.0
    e_inverter_in_total: float = 0.0
    e_battery_throughput: float = 0.0
    e_self_consumption_total: float = 0.0

    # Active Control / Schedule Settings
    battery_power_mode: int = 1  # 1 = SELF_CONSUMPTION (Match Demand / Eco), 0 = MAX_POWER (Export)
    enable_charge: bool = False
    enable_discharge: bool = False
    enable_charge_target: bool = False
    charge_target_soc: int = 100
    battery_soc_reserve: int = 4
    battery_discharge_min_power_reserve: int = 4
    battery_charge_limit: int = 50
    battery_discharge_limit: int = 50
    active_power_rate: int = 100
    charge_slot_1_start: time = field(default_factory=lambda: time(0, 0))
    charge_slot_1_end: time = field(default_factory=lambda: time(0, 0))
    charge_target_soc_1: int = 100
    charge_slot_2_start: time = field(default_factory=lambda: time(0, 0))
    charge_slot_2_end: time = field(default_factory=lambda: time(0, 0))
    discharge_slot_1_start: time = field(default_factory=lambda: time(0, 0))
    discharge_slot_1_end: time = field(default_factory=lambda: time(0, 0))
    discharge_target_soc_1: int = 4
    discharge_slot_2_start: time = field(default_factory=lambda: time(0, 0))
    discharge_slot_2_end: time = field(default_factory=lambda: time(0, 0))

    @property
    def operating_mode_summary(self) -> str:
        """Human-readable summary of current inverter battery operating mode."""
        if self.battery_power_mode == 1 and not self.enable_discharge:
            return "Eco / Dynamic (Self-Consumption)"
        if self.enable_discharge and self.battery_power_mode == 1:
            return "Timed Discharge (Match Demand)"
        if self.enable_discharge and self.battery_power_mode == 0:
            return "Timed Export (Max Power)"
        if self.battery_power_mode == 0:
            return "Max Power Export"
        return "Custom"


@dataclass
class BatteryTelemetry:
    """Detailed BMS telemetry from the 9.5kWh GivEnergy LiFePO4 Battery Pack."""

    present: bool = False
    serial_number: str = "—"
    bms_firmware_version: int = 0
    soc: int = 0
    num_cells: int = 16
    num_cycles: int = 0

    # Voltages & Current
    v_out: float = 0.0
    v_cells_sum: float = 0.0
    i_battery: float = 0.0
    cell_voltages: list[float] = field(default_factory=lambda: [0.0] * 16)

    # Capacities & Energy
    cap_design: float = 186.0
    cap_calibrated: float = 186.0
    cap_remaining: float = 0.0
    remaining_energy_nominal_wh: int = 0
    remaining_energy_measured_wh: int = 0
    e_battery_charge_total: float = 0.0
    e_battery_discharge_total: float = 0.0

    # Temperatures (°C)
    t_bms_mosfet: float = 0.0
    t_max: float = 0.0
    t_min: float = 0.0
    t_cells_01_04: float = 0.0
    t_cells_05_08: float = 0.0
    t_cells_09_12: float = 0.0
    t_cells_13_16: float = 0.0

    # Status / Warnings
    warning_1: int = 0
    warning_2: int = 0

    @property
    def cell_min_v(self) -> float:
        valid = [v for v in self.cell_voltages if v > 0]
        return min(valid) if valid else 0.0

    @property
    def cell_max_v(self) -> float:
        valid = [v for v in self.cell_voltages if v > 0]
        return max(valid) if valid else 0.0

    @property
    def cell_delta_mv(self) -> float:
        valid = [v for v in self.cell_voltages if v > 0]
        if not valid:
            return 0.0
        return (max(valid) - min(valid)) * 1000.0


@dataclass
class EVChargerTelemetry:
    """Live telemetry and control state from the GivEnergy EV Charger via Modbus TCP (Port 502)."""

    connected: bool = False
    last_updated: datetime | None = None
    error_message: str = ""

    serial_number: str = "—"
    charging_state: str = "Unknown"
    charging_state_code: int = 0
    connection_status: str = "Not Connected"
    cable_connected: bool = False
    error_code: int = 0
    error_description: str = "Clear"

    # Electricals
    active_power_w: int = 0
    active_power_l1_w: int = 0
    current_l1_a: float = 0.0
    voltage_l1_v: float = 0.0

    # Limits & Controls
    charge_limit_a: float = 32.0
    setpoint_limit_a: float = 32.0
    evse_max_current_a: float = 32.0
    evse_min_current_a: float = 6.0
    plug_and_go_enabled: bool = True
    charge_control_state: str = "Ready"

    # Session & Lifetime Energy
    session_energy_kwh: float = 0.0
    session_duration_seconds: int = 0
    session_start_time: str = "—"
    session_end_time: str = "—"
    total_meter_energy_kwh: float = 0.0
    system_time: datetime | None = None

    @property
    def session_duration_formatted(self) -> str:
        secs = max(0, int(self.session_duration_seconds))
        hrs, rem = divmod(secs, 3600)
        mins, s = divmod(rem, 60)
        return f"{hrs:02d}:{mins:02d}:{s:02d}"


@dataclass
class PowerHistoryPoint:
    """Single time-series sample for the live power chart."""

    timestamp: datetime
    solar_w: int
    battery_w: int  # positive = discharge, negative = charge
    grid_w: int     # positive = export, negative = import
    home_w: int
    ev_w: int
    soc_pct: int


@dataclass
class PlantSnapshot:
    """Combined snapshot of Inverter, Battery Pack(s), EV Charger, and History."""

    inverter: InverterTelemetry = field(default_factory=InverterTelemetry)
    battery: BatteryTelemetry = field(default_factory=BatteryTelemetry)
    batteries: list[BatteryTelemetry] = field(default_factory=list)
    ev_charger: EVChargerTelemetry = field(default_factory=EVChargerTelemetry)
    history: list[PowerHistoryPoint] = field(default_factory=list)
