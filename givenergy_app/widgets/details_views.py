"""Detailed Telemetry Tabs for Hybrid Gen 3 Inverter/Solar PV and GivEnergy EV Charger."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from givenergy_app.models import PlantSnapshot


def _build_kv_group(title: str, accent_hex: str, rows: list[tuple[str, str]]) -> tuple[QFrame, dict[str, QLabel]]:
    frame = QFrame()
    frame.setStyleSheet(
        f"""
        QFrame {{
            background-color: #0F172A;
            border: 1px solid #1E293B;
            border-top: 3px solid {accent_hex};
            border-radius: 12px;
        }}
        """
    )
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)

    hdr = QLabel(title)
    hdr.setStyleSheet(f"color: {accent_hex}; font-size: 11px; font-weight: 700; border: none; letter-spacing: 0.8px;")
    lay.addWidget(hdr)

    grid = QGridLayout()
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(7)

    value_labels: dict[str, QLabel] = {}
    for idx, (key, label_text) in enumerate(rows):
        lbl_k = QLabel(label_text)
        lbl_k.setStyleSheet("color: #94A3B8; font-size: 12px; border: none;")
        lbl_v = QLabel("—")
        lbl_v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl_v.setStyleSheet("color: #F8FAFC; font-size: 12px; font-weight: 600; border: none;")
        grid.addWidget(lbl_k, idx, 0)
        grid.addWidget(lbl_v, idx, 1)
        value_labels[key] = lbl_v

    lay.addLayout(grid)
    lay.addStretch(1)
    return frame, value_labels


class InverterSolarDetailsView(QWidget):
    """Detailed view of Hybrid Gen 3 Inverter, Dual MPPT Solar Strings, Grid, and Energy Totals."""

    def __init__(self, enqueue_cmd_cb: Callable[[str, str, dict, str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._enqueue_cmd = enqueue_cmd_cb
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        grid = QGridLayout()
        grid.setSpacing(12)

        # 1. Dual MPPT Solar Strings
        f_pv, self.kv_pv = _build_kv_group(
            "☀  SOLAR PV DUAL-MPPT STRINGS",
            "#F59E0B",
            [
                ("pv_total_w", "Total PV Power"),
                ("pv1_w", "String 1 Power (PV1)"),
                ("pv1_vi", "String 1 Voltage / Current"),
                ("pv1_day", "String 1 Energy Today"),
                ("pv2_w", "String 2 Power (PV2)"),
                ("pv2_vi", "String 2 Voltage / Current"),
                ("pv2_day", "String 2 Energy Today"),
                ("pv_today", "Total PV Generated Today"),
                ("pv_total", "Lifetime PV Generation"),
            ],
        )

        # 2. AC Grid & EPS Output
        f_grid, self.kv_grid = _build_kv_group(
            "🗼  AC GRID & INVERTER OUTPUT",
            "#38BDF8",
            [
                ("grid_flow", "Net Grid Power"),
                ("grid_import_export", "Import / Export Power"),
                ("grid_apparent", "Apparent Power (VA)"),
                ("grid_vf", "Grid Voltage & Frequency"),
                ("grid_current", "Grid Port Current"),
                ("inv_out_vf", "Inverter Output V & Freq"),
                ("inv_out_pi", "Inverter Output Power / Current"),
                ("load_demand", "Home Load Demand"),
                ("eps_backup", "EPS Backup Load Power"),
                ("dc_bus", "HV / P-Bus Voltage"),
            ],
        )

        # 3. Daily & Lifetime Energy Ledger
        f_energy, self.kv_energy = _build_kv_group(
            "📊  DAILY & LIFETIME ENERGY LEDGER",
            "#10B981",
            [
                ("cons_today", "Home Consumption Today"),
                ("self_today", "Solar Self-Consumption Today"),
                ("grid_in_day", "Grid Import Today"),
                ("grid_out_day", "Grid Export Today"),
                ("bat_chg_day", "Battery Charged Today"),
                ("bat_dis_day", "Battery Discharged Today"),
                ("ac_chg_day", "AC Grid Charged Today"),
                ("grid_in_tot", "Lifetime Grid Import"),
                ("grid_out_tot", "Lifetime Grid Export"),
                ("bat_thru_tot", "Lifetime Battery Throughput"),
            ],
        )

        # 4. Inverter Identity, Thermal & Schedule Configuration
        f_sys, self.kv_sys = _build_kv_group(
            "⚙  INVERTER IDENTITY, THERMALS & SCHEDULES",
            "#A78BFA",
            [
                ("serial_model", "Serial & Model"),
                ("firmware", "Firmware (DSP / ARM / BMS)"),
                ("status_fault", "Operational Status / Faults"),
                ("temps", "Heatsink / Charger / Battery Temp"),
                ("work_hours", "Total Operating Hours"),
                ("sys_time", "Inverter System Clock"),
                ("mode_summary", "Battery Power Mode"),
                ("soc_limits", "Target SoC / Reserve Cutoff"),
                ("chg_slots", "Charge Slots 1 & 2"),
                ("dis_slots", "Discharge Slots 1 & 2"),
            ],
        )

        grid.addWidget(f_pv, 0, 0)
        grid.addWidget(f_grid, 0, 1)
        grid.addWidget(f_energy, 1, 0)
        grid.addWidget(f_sys, 1, 1)

        root.addLayout(grid, 1)

        # Bottom utility bar
        bar = QHBoxLayout()
        bar.addStretch(1)
        btn_sync = QPushButton("🕒 Sync Inverter Clock to PC Time")
        btn_sync.setStyleSheet(
            """
            QPushButton {
                background-color: #1E293B;
                color: #E2E8F0;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #334155;
            }
            """
        )
        btn_sync.clicked.connect(
            lambda: self._enqueue_cmd("inverter", "sync_time", {}, "Sync Inverter Clock to PC Time")
        )
        bar.addWidget(btn_sync)
        root.addLayout(bar)

    def update_snapshot(self, snapshot: PlantSnapshot) -> None:
        inv = snapshot.inverter
        if not inv.connected:
            return

        self.kv_pv["pv_total_w"].setText(f"{inv.p_pv_total:,} W")
        self.kv_pv["pv1_w"].setText(f"{inv.p_pv1:,} W")
        self.kv_pv["pv1_vi"].setText(f"{inv.v_pv1:.1f} V  /  {inv.i_pv1:.2f} A")
        self.kv_pv["pv1_day"].setText(f"{inv.e_pv1_day:.1f} kWh")
        self.kv_pv["pv2_w"].setText(f"{inv.p_pv2:,} W")
        self.kv_pv["pv2_vi"].setText(f"{inv.v_pv2:.1f} V  /  {inv.i_pv2:.2f} A")
        self.kv_pv["pv2_day"].setText(f"{inv.e_pv2_day:.1f} kWh")
        self.kv_pv["pv_today"].setText(f"{inv.e_pv_generation_today:.1f} kWh")
        self.kv_pv["pv_total"].setText(f"{inv.e_pv_generation_total:,.1f} kWh")

        flow_dir = "Exporting" if inv.p_grid_out > 0 else ("Importing" if inv.p_grid_out < 0 else "Balanced")
        self.kv_grid["grid_flow"].setText(f"{abs(inv.p_grid_out):,} W ({flow_dir})")
        self.kv_grid["grid_import_export"].setText(f"In: {inv.grid_import_power} W  |  Out: {inv.grid_export_power} W")
        self.kv_grid["grid_apparent"].setText(f"{inv.p_grid_apparent:,} VA")
        self.kv_grid["grid_vf"].setText(f"{inv.v_ac1:.1f} V  @  {inv.f_ac1:.2f} Hz")
        self.kv_grid["grid_current"].setText(f"{inv.i_grid_port:.2f} A  (Max {inv.grid_port_max_power_output} W)")
        self.kv_grid["inv_out_vf"].setText(f"{inv.v_ac1_output:.1f} V  @  {inv.f_ac1_output:.2f} Hz")
        self.kv_grid["inv_out_pi"].setText(f"{inv.p_inverter_out:,} W  /  {inv.i_ac1:.2f} A (Rated {inv.inverter_max_power} W)")
        self.kv_grid["load_demand"].setText(f"{inv.p_load_demand:,} W")
        self.kv_grid["eps_backup"].setText(f"{inv.p_backup:,} W")
        self.kv_grid["dc_bus"].setText(f"P-Bus: {inv.v_p_bus:.1f} V  |  HB-Bus: {inv.v_highbrigh_bus:.1f} V")

        self.kv_energy["cons_today"].setText(f"{inv.e_consumption_today:.1f} kWh")
        self.kv_energy["self_today"].setText(f"{inv.e_self_consumption_today:.1f} kWh")
        self.kv_energy["grid_in_day"].setText(f"{inv.e_grid_in_day:.1f} kWh")
        self.kv_energy["grid_out_day"].setText(f"{inv.e_grid_out_day:.1f} kWh")
        self.kv_energy["bat_chg_day"].setText(f"{inv.e_battery_charge_today:.1f} kWh")
        self.kv_energy["bat_dis_day"].setText(f"{inv.e_battery_discharge_today:.1f} kWh")
        self.kv_energy["ac_chg_day"].setText(f"{inv.e_ac_charge_today:.1f} kWh")
        self.kv_energy["grid_in_tot"].setText(f"{inv.e_grid_in_total:,.1f} kWh")
        self.kv_energy["grid_out_tot"].setText(f"{inv.e_grid_out_total:,.1f} kWh")
        self.kv_energy["bat_thru_tot"].setText(f"{inv.e_battery_throughput:,.1f} kWh")

        self.kv_sys["serial_model"].setText(f"{inv.serial_number}  •  {inv.model_name} (DTC {inv.device_type_code})")
        self.kv_sys["firmware"].setText(f"{inv.firmware_version}  (BMS v{inv.bms_firmware_version})")
        self.kv_sys["status_fault"].setText(f"{inv.status}  (Fault: {inv.fault_code}, Warn: {inv.warning_code})")
        self.kv_sys["temps"].setText(
            f"Heatsink: {inv.t_inverter_heatsink:.1f}°C  |  Charger: {inv.t_charger:.1f}°C  |  Bat: {inv.t_battery:.1f}°C"
        )
        self.kv_sys["work_hours"].setText(f"{inv.work_time_hours:,} hours ({inv.work_time_hours / 24.0:,.0f} days)")
        self.kv_sys["sys_time"].setText(
            inv.system_time.strftime("%Y-%m-%d %H:%M:%S") if inv.system_time else "—"
        )
        self.kv_sys["mode_summary"].setText(inv.operating_mode_summary)
        self.kv_sys["soc_limits"].setText(
            f"Target: {inv.charge_target_soc}% ({'ON' if inv.enable_charge_target else 'OFF'})  |  Reserve: {inv.battery_soc_reserve}%"
        )
        cs1_str = f"{inv.charge_slot_1_start.strftime('%H:%M')}–{inv.charge_slot_1_end.strftime('%H:%M')}"
        cs2_str = f"{inv.charge_slot_2_start.strftime('%H:%M')}–{inv.charge_slot_2_end.strftime('%H:%M')}"
        self.kv_sys["chg_slots"].setText(
            f"S1: {cs1_str}  |  S2: {cs2_str}  ({'Enabled' if inv.enable_charge else 'Disabled'})"
        )
        ds1_str = f"{inv.discharge_slot_1_start.strftime('%H:%M')}–{inv.discharge_slot_1_end.strftime('%H:%M')}"
        ds2_str = f"{inv.discharge_slot_2_start.strftime('%H:%M')}–{inv.discharge_slot_2_end.strftime('%H:%M')}"
        self.kv_sys["dis_slots"].setText(
            f"S1: {ds1_str}  |  S2: {ds2_str}  ({'Enabled' if inv.enable_discharge else 'Disabled'})"
        )


class EVChargerDetailsView(QWidget):
    """Detailed view of the GivEnergy EV Charger via Local Modbus TCP (Port 502)."""

    def __init__(self, enqueue_cmd_cb: Callable[[str, str, dict, str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._enqueue_cmd = enqueue_cmd_cb
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        grid = QGridLayout()
        grid.setSpacing(12)

        f_live, self.kv_live = _build_kv_group(
            "🚗  EV CHARGER LIVE STATUS & ELECTRICALS",
            "#22D3EE",
            [
                ("serial", "EV Charger Serial Number"),
                ("state", "EVSE Operational State"),
                ("cable", "Vehicle Cable Connection"),
                ("error", "Hardware Fault / Error Status"),
                ("power", "Active Charging Power"),
                ("voltage", "AC Supply Voltage (L1)"),
                ("current", "Charging Current Draw (L1)"),
                ("limits", "Configured Current Limit (Min / Max)"),
            ],
        )

        f_session, self.kv_session = _build_kv_group(
            "⚡  CHARGING SESSION & LIFETIME METER",
            "#10B981",
            [
                ("plug_and_go", "Plug & Go Autonomous Mode"),
                ("charge_ctrl", "Manual Charge Control Register"),
                ("session_kwh", "Current / Last Session Energy"),
                ("session_dur", "Current / Last Session Duration"),
                ("session_start", "Last Session Start Timestamp"),
                ("session_end", "Last Session End Timestamp"),
                ("lifetime_kwh", "Total Lifetime Meter Energy"),
                ("sys_time", "EV Charger System Clock"),
            ],
        )

        grid.addWidget(f_live, 0, 0)
        grid.addWidget(f_session, 0, 1)
        root.addLayout(grid, 1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        btn_sync = QPushButton("🕒 Sync EV Charger Clock to PC Time")
        btn_sync.setStyleSheet(
            """
            QPushButton {
                background-color: #1E293B;
                color: #E2E8F0;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #334155;
            }
            """
        )
        btn_sync.clicked.connect(
            lambda: self._enqueue_cmd("evc", "sync_time", {}, "Sync EV Charger Clock to PC Time")
        )
        bar.addWidget(btn_sync)
        root.addLayout(bar)

    def update_snapshot(self, snapshot: PlantSnapshot) -> None:
        evc = snapshot.ev_charger
        if not evc.connected:
            return

        self.kv_live["serial"].setText(evc.serial_number)
        self.kv_live["state"].setText(f"{evc.charging_state} (Code {evc.charging_state_code})")
        self.kv_live["cable"].setText(evc.connection_status)
        self.kv_live["error"].setText(f"{evc.error_description} (Code {evc.error_code})")
        self.kv_live["power"].setText(f"{evc.active_power_w:,} W  ({evc.active_power_w / 1000.0:.2f} kW)")
        self.kv_live["voltage"].setText(f"{evc.voltage_l1_v:.1f} V")
        self.kv_live["current"].setText(f"{evc.current_l1_a:.1f} A")
        self.kv_live["limits"].setText(
            f"{evc.charge_limit_a:.1f} A  (Range: {evc.evse_min_current_a:.0f}A – {evc.evse_max_current_a:.0f}A)"
        )

        self.kv_session["plug_and_go"].setText("Enabled (Auto-Start on Plug)" if evc.plug_and_go_enabled else "Disabled")
        self.kv_session["charge_ctrl"].setText(evc.charge_control_state)
        self.kv_session["session_kwh"].setText(f"{evc.session_energy_kwh:.1f} kWh")
        self.kv_session["session_dur"].setText(evc.session_duration_formatted)
        self.kv_session["session_start"].setText(evc.session_start_time)
        self.kv_session["session_end"].setText(evc.session_end_time)
        self.kv_session["lifetime_kwh"].setText(f"{evc.total_meter_energy_kwh:,.1f} kWh")
        self.kv_session["sys_time"].setText(
            evc.system_time.strftime("%Y-%m-%d %H:%M:%S") if evc.system_time else "—"
        )
