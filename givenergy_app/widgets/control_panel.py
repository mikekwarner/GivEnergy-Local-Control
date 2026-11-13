"""Active Hardware Control Sidebar for Inverter/Battery and EV Charger."""

from __future__ import annotations

from datetime import time
from typing import Callable

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from givenergy_app.models import PlantSnapshot


class ActiveControlPanel(QScrollArea):
    """Scrollable sidebar containing live hardware controls for Inverter, Battery, and EV Charger."""

    def __init__(
        self,
        enqueue_cmd_cb: Callable[[str, str, dict, str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._enqueue_cmd = enqueue_cmd_cb
        self._synced_once_inv = False
        self._synced_once_evc = False

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(340)
        self.setMaximumWidth(390)
        self.setStyleSheet(
            """
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            """
        )

        container = QWidget()
        container.setStyleSheet("background-color: transparent;")
        self._root_layout = QVBoxLayout(container)
        self._root_layout.setContentsMargins(0, 0, 4, 0)
        self._root_layout.setSpacing(12)

        self._build_evc_section()
        self._build_inverter_battery_section()
        self._root_layout.addStretch(1)

        self.setWidget(container)

    def _make_section_card(self, title: str, accent_hex: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setStyleSheet(
            f"""
            QFrame#SectionCard {{
                background-color: #0F172A;
                border: 1px solid #1E293B;
                border-top: 3px solid {accent_hex};
                border-radius: 12px;
            }}
            """
        )
        card.setObjectName("SectionCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(10)

        hdr = QLabel(title)
        hdr.setStyleSheet(f"color: {accent_hex}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;")
        lay.addWidget(hdr)
        return card, lay

    def _btn_style(self, bg: str, hover: str, text_col: str = "#F8FAFC", border: str = "none") -> str:
        return f"""
            QPushButton {{
                background-color: {bg};
                color: {text_col};
                border: {border};
                border-radius: 7px;
                padding: 7px 10px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {hover};
            }}
            QPushButton:pressed {{
                background-color: #0F172A;
            }}
        """

    def _build_evc_section(self) -> None:
        card, lay = self._make_section_card("🚗  EV CHARGER ACTIVE CONTROLS", "#22D3EE")

        self.lbl_evc_live_state = QLabel("State: —  •  Cable: —")
        self.lbl_evc_live_state.setStyleSheet("color: #CBD5E1; font-size: 11px; font-weight: 600;")
        lay.addWidget(self.lbl_evc_live_state)

        # Start / Stop / Ready row
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)

        btn_start = QPushButton("▶ Start Charge")
        btn_start.setStyleSheet(self._btn_style("#059669", "#10B981"))
        btn_start.clicked.connect(
            lambda: self._enqueue_cmd("evc", "charge_control", {"mode": "Start"}, "Start EV Charging")
        )

        btn_stop = QPushButton("⏹ Stop Charge")
        btn_stop.setStyleSheet(self._btn_style("#E11D48", "#F43F5E"))
        btn_stop.clicked.connect(
            lambda: self._enqueue_cmd("evc", "charge_control", {"mode": "Stop"}, "Stop EV Charging")
        )

        btn_ready = QPushButton("Ready")
        btn_ready.setStyleSheet(self._btn_style("#1E293B", "#334155", border="1px solid #334155"))
        btn_ready.clicked.connect(
            lambda: self._enqueue_cmd("evc", "charge_control", {"mode": "Ready"}, "Set EV Charger Ready")
        )

        ctrl_row.addWidget(btn_start, 2)
        ctrl_row.addWidget(btn_stop, 2)
        ctrl_row.addWidget(btn_ready, 1)
        lay.addLayout(ctrl_row)

        # Plug & Go toggle row
        png_row = QHBoxLayout()
        png_row.setSpacing(6)
        lbl_png = QLabel("Plug & Go Mode:")
        lbl_png.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.btn_png_on = QPushButton("Enable")
        self.btn_png_on.setStyleSheet(self._btn_style("#1E293B", "#0284C7", border="1px solid #334155"))
        self.btn_png_on.clicked.connect(
            lambda: self._enqueue_cmd("evc", "plug_and_go", {"enabled": True}, "Enable Plug & Go")
        )
        self.btn_png_off = QPushButton("Disable")
        self.btn_png_off.setStyleSheet(self._btn_style("#1E293B", "#475569", border="1px solid #334155"))
        self.btn_png_off.clicked.connect(
            lambda: self._enqueue_cmd("evc", "plug_and_go", {"enabled": False}, "Disable Plug & Go")
        )
        png_row.addWidget(lbl_png)
        png_row.addWidget(self.btn_png_on)
        png_row.addWidget(self.btn_png_off)
        lay.addLayout(png_row)

        # Current Limit Slider (6A .. 32A)
        lim_hdr = QHBoxLayout()
        lbl_lim_title = QLabel("Charging Current Limit:")
        lbl_lim_title.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.lbl_evc_amp_val = QLabel("32 A (~7.7 kW)")
        self.lbl_evc_amp_val.setStyleSheet("color: #22D3EE; font-size: 12px; font-weight: 700;")
        lim_hdr.addWidget(lbl_lim_title)
        lim_hdr.addStretch()
        lim_hdr.addWidget(self.lbl_evc_amp_val)
        lay.addLayout(lim_hdr)

        slider_row = QHBoxLayout()
        slider_row.setSpacing(8)
        self.slider_evc_amps = QSlider(Qt.Orientation.Horizontal)
        self.slider_evc_amps.setRange(6, 32)
        self.slider_evc_amps.setValue(32)
        self.slider_evc_amps.valueChanged.connect(self._on_evc_slider_changed)

        btn_apply_amps = QPushButton("Set Limit")
        btn_apply_amps.setStyleSheet(self._btn_style("#0284C7", "#0EA5E9"))
        btn_apply_amps.clicked.connect(self._apply_evc_current_limit)

        slider_row.addWidget(self.slider_evc_amps, 1)
        slider_row.addWidget(btn_apply_amps)
        lay.addLayout(slider_row)

        # Quick current preset buttons
        preset_row = QHBoxLayout()
        preset_row.setSpacing(5)
        for amps, kw_txt in [(6, "6A (1.4kW)"), (16, "16A (3.8kW)"), (24, "24A (5.8kW)"), (32, "32A (7.7kW)")]:
            b = QPushButton(kw_txt)
            b.setStyleSheet(self._btn_style("#1E293B", "#334155", "#CBD5E1", "1px solid #334155"))
            b.clicked.connect(lambda checked=False, a=amps: self._quick_set_evc_amps(a))
            preset_row.addWidget(b)
        lay.addLayout(preset_row)

        self._root_layout.addWidget(card)

    def _build_inverter_battery_section(self) -> None:
        card, lay = self._make_section_card("🔋  INVERTER & 9.5kWh BATTERY CONTROLS", "#10B981")

        self.lbl_inv_mode = QLabel("Mode: —")
        self.lbl_inv_mode.setStyleSheet("color: #CBD5E1; font-size: 11px; font-weight: 600;")
        lay.addWidget(self.lbl_inv_mode)

        # Operating mode buttons
        mode_grid = QGridLayout()
        mode_grid.setSpacing(6)

        self.btn_mode_eco = QPushButton("🌿 Eco / Dynamic")
        self.btn_mode_eco.setToolTip("Maximise solar self-consumption; battery matches home load demand.")
        self.btn_mode_eco.setStyleSheet(self._btn_style("#059669", "#10B981"))
        self.btn_mode_eco.clicked.connect(
            lambda: self._enqueue_cmd("inverter", "set_mode_eco", {}, "Set Eco / Dynamic Self-Consumption Mode")
        )

        self.btn_mode_timed_dis = QPushButton("⏱ Timed Discharge")
        self.btn_mode_timed_dis.setToolTip("Enable scheduled discharge slots matching home load.")
        self.btn_mode_timed_dis.setStyleSheet(self._btn_style("#1E293B", "#334155", border="1px solid #334155"))
        self.btn_mode_timed_dis.clicked.connect(
            lambda: self._enqueue_cmd(
                "inverter", "set_mode_timed_discharge", {}, "Set Timed Discharge (Match Demand)"
            )
        )

        self.btn_mode_export = QPushButton("⚡ Timed Export (Max)")
        self.btn_mode_export.setToolTip("Discharge at full power during scheduled slot, exporting surplus to grid.")
        self.btn_mode_export.setStyleSheet(self._btn_style("#1E293B", "#334155", border="1px solid #334155"))
        self.btn_mode_export.clicked.connect(
            lambda: self._enqueue_cmd("inverter", "set_mode_timed_export", {}, "Set Timed Export (Max Power)")
        )

        mode_grid.addWidget(self.btn_mode_eco, 0, 0, 1, 2)
        mode_grid.addWidget(self.btn_mode_timed_dis, 1, 0)
        mode_grid.addWidget(self.btn_mode_export, 1, 1)
        lay.addLayout(mode_grid)

        # Scheduled AC Charge & Discharge toggles
        sched_toggle_grid = QGridLayout()
        sched_toggle_grid.setSpacing(6)

        lbl_ac_chg = QLabel("Scheduled AC Charge:")
        lbl_ac_chg.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.btn_chg_enable = QPushButton("Enable")
        self.btn_chg_enable.setStyleSheet(self._btn_style("#1E293B", "#059669", border="1px solid #334155"))
        self.btn_chg_enable.clicked.connect(
            lambda: self._enqueue_cmd("inverter", "set_enable_charge", {"enabled": True}, "Enable Scheduled Charge")
        )
        self.btn_chg_disable = QPushButton("Disable")
        self.btn_chg_disable.setStyleSheet(self._btn_style("#1E293B", "#475569", border="1px solid #334155"))
        self.btn_chg_disable.clicked.connect(
            lambda: self._enqueue_cmd("inverter", "set_enable_charge", {"enabled": False}, "Disable Scheduled Charge")
        )

        lbl_dc_dis = QLabel("Scheduled Discharge:")
        lbl_dc_dis.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.btn_dis_enable = QPushButton("Enable")
        self.btn_dis_enable.setStyleSheet(self._btn_style("#1E293B", "#0284C7", border="1px solid #334155"))
        self.btn_dis_enable.clicked.connect(
            lambda: self._enqueue_cmd(
                "inverter", "set_enable_discharge", {"enabled": True}, "Enable Scheduled Discharge"
            )
        )
        self.btn_dis_disable = QPushButton("Disable")
        self.btn_dis_disable.setStyleSheet(self._btn_style("#1E293B", "#475569", border="1px solid #334155"))
        self.btn_dis_disable.clicked.connect(
            lambda: self._enqueue_cmd(
                "inverter", "set_enable_discharge", {"enabled": False}, "Disable Scheduled Discharge"
            )
        )

        sched_toggle_grid.addWidget(lbl_ac_chg, 0, 0)
        sched_toggle_grid.addWidget(self.btn_chg_enable, 0, 1)
        sched_toggle_grid.addWidget(self.btn_chg_disable, 0, 2)
        sched_toggle_grid.addWidget(lbl_dc_dis, 1, 0)
        sched_toggle_grid.addWidget(self.btn_dis_enable, 1, 1)
        sched_toggle_grid.addWidget(self.btn_dis_disable, 1, 2)
        lay.addLayout(sched_toggle_grid)

        # SoC Target & Reserve Spinboxes
        soc_grid = QGridLayout()
        soc_grid.setSpacing(6)

        lbl_target = QLabel("Charge Target SoC:")
        lbl_target.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.spin_target_soc = self._make_spinbox(4, 100, 95, "%")
        btn_set_target = QPushButton("Apply")
        btn_set_target.setStyleSheet(self._btn_style("#0284C7", "#0EA5E9"))
        btn_set_target.clicked.connect(
            lambda: self._enqueue_cmd(
                "inverter",
                "set_charge_target_soc",
                {"soc": self.spin_target_soc.value()},
                f"Set Charge Target SoC to {self.spin_target_soc.value()}%",
            )
        )

        lbl_reserve = QLabel("Min Reserve SoC:")
        lbl_reserve.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.spin_reserve_soc = self._make_spinbox(4, 100, 4, "%")
        btn_set_reserve = QPushButton("Apply")
        btn_set_reserve.setStyleSheet(self._btn_style("#0284C7", "#0EA5E9"))
        btn_set_reserve.clicked.connect(
            lambda: self._enqueue_cmd(
                "inverter",
                "set_battery_soc_reserve",
                {"soc": self.spin_reserve_soc.value()},
                f"Set Minimum Reserve SoC to {self.spin_reserve_soc.value()}%",
            )
        )

        lbl_chg_rate = QLabel("Charge Limit (C/100):")
        lbl_chg_rate.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.spin_chg_limit = self._make_spinbox(0, 50, 38, "")
        btn_set_chg_rate = QPushButton("Apply")
        btn_set_chg_rate.setStyleSheet(self._btn_style("#1E293B", "#334155", border="1px solid #334155"))
        btn_set_chg_rate.clicked.connect(
            lambda: self._enqueue_cmd(
                "inverter",
                "set_battery_charge_limit",
                {"limit": self.spin_chg_limit.value()},
                f"Set Battery Charge Rate Limit to {self.spin_chg_limit.value()}",
            )
        )

        lbl_dis_rate = QLabel("Discharge Limit (C/100):")
        lbl_dis_rate.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.spin_dis_limit = self._make_spinbox(0, 50, 38, "")
        btn_set_dis_rate = QPushButton("Apply")
        btn_set_dis_rate.setStyleSheet(self._btn_style("#1E293B", "#334155", border="1px solid #334155"))
        btn_set_dis_rate.clicked.connect(
            lambda: self._enqueue_cmd(
                "inverter",
                "set_battery_discharge_limit",
                {"limit": self.spin_dis_limit.value()},
                f"Set Battery Discharge Rate Limit to {self.spin_dis_limit.value()}",
            )
        )

        soc_grid.addWidget(lbl_target, 0, 0)
        soc_grid.addWidget(self.spin_target_soc, 0, 1)
        soc_grid.addWidget(btn_set_target, 0, 2)

        soc_grid.addWidget(lbl_reserve, 1, 0)
        soc_grid.addWidget(self.spin_reserve_soc, 1, 1)
        soc_grid.addWidget(btn_set_reserve, 1, 2)

        soc_grid.addWidget(lbl_chg_rate, 2, 0)
        soc_grid.addWidget(self.spin_chg_limit, 2, 1)
        soc_grid.addWidget(btn_set_chg_rate, 2, 2)

        soc_grid.addWidget(lbl_dis_rate, 3, 0)
        soc_grid.addWidget(self.spin_dis_limit, 3, 1)
        soc_grid.addWidget(btn_set_dis_rate, 3, 2)
        lay.addLayout(soc_grid)

        # Time Slot 1 Editors
        slot_grid = QGridLayout()
        slot_grid.setSpacing(6)

        lbl_cs1 = QLabel("Charge Slot 1:")
        lbl_cs1.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.time_cs1_start = self._make_time_edit(0, 0)
        self.time_cs1_end = self._make_time_edit(6, 30)
        btn_save_cs1 = QPushButton("Save")
        btn_save_cs1.setStyleSheet(self._btn_style("#059669", "#10B981"))
        btn_save_cs1.clicked.connect(self._save_charge_slot_1)

        lbl_ds1 = QLabel("Discharge Slot 1:")
        lbl_ds1.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.time_ds1_start = self._make_time_edit(17, 45)
        self.time_ds1_end = self._make_time_edit(19, 0)
        btn_save_ds1 = QPushButton("Save")
        btn_save_ds1.setStyleSheet(self._btn_style("#0284C7", "#0EA5E9"))
        btn_save_ds1.clicked.connect(self._save_discharge_slot_1)

        slot_grid.addWidget(lbl_cs1, 0, 0)
        slot_grid.addWidget(self.time_cs1_start, 0, 1)
        slot_grid.addWidget(self.time_cs1_end, 0, 2)
        slot_grid.addWidget(btn_save_cs1, 0, 3)

        slot_grid.addWidget(lbl_ds1, 1, 0)
        slot_grid.addWidget(self.time_ds1_start, 1, 1)
        slot_grid.addWidget(self.time_ds1_end, 1, 2)
        slot_grid.addWidget(btn_save_ds1, 1, 3)
        lay.addLayout(slot_grid)

        self._root_layout.addWidget(card)

    def _make_spinbox(self, min_v: int, max_v: int, val: int, suffix: str) -> QSpinBox:
        sb = QSpinBox()
        sb.setRange(min_v, max_v)
        sb.setValue(val)
        if suffix:
            sb.setSuffix(f" {suffix}")
        sb.setStyleSheet(
            """
            QSpinBox {
                background-color: #1E293B;
                color: #F8FAFC;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            """
        )
        return sb

    def _make_time_edit(self, h: int, m: int) -> QTimeEdit:
        te = QTimeEdit(QTime(h, m))
        te.setDisplayFormat("HH:mm")
        te.setStyleSheet(
            """
            QTimeEdit {
                background-color: #1E293B;
                color: #F8FAFC;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 4px 6px;
                font-size: 11px;
                font-weight: 600;
            }
            """
        )
        return te

    def _on_evc_slider_changed(self, amps: int) -> None:
        est_kw = (amps * 240.0) / 1000.0
        self.lbl_evc_amp_val.setText(f"{amps} A (~{est_kw:.1f} kW)")

    def _apply_evc_current_limit(self) -> None:
        amps = self.slider_evc_amps.value()
        self._enqueue_cmd("evc", "current_limit", {"amps": amps}, f"Set EV Charge Limit to {amps}A")

    def _quick_set_evc_amps(self, amps: int) -> None:
        self.slider_evc_amps.setValue(amps)
        self._enqueue_cmd("evc", "current_limit", {"amps": amps}, f"Set EV Charge Limit to {amps}A")

    def _save_charge_slot_1(self) -> None:
        qs = self.time_cs1_start.time()
        qe = self.time_cs1_end.time()
        t_start = time(qs.hour(), qs.minute())
        t_end = time(qe.hour(), qe.minute())
        self._enqueue_cmd(
            "inverter",
            "set_charge_slot_1",
            {"start": t_start, "end": t_end},
            f"Set Charge Slot 1 to {t_start.strftime('%H:%M')}–{t_end.strftime('%H:%M')}",
        )

    def _save_discharge_slot_1(self) -> None:
        qs = self.time_ds1_start.time()
        qe = self.time_ds1_end.time()
        t_start = time(qs.hour(), qs.minute())
        t_end = time(qe.hour(), qe.minute())
        self._enqueue_cmd(
            "inverter",
            "set_discharge_slot_1",
            {"start": t_start, "end": t_end},
            f"Set Discharge Slot 1 to {t_start.strftime('%H:%M')}–{t_end.strftime('%H:%M')}",
        )

    def update_snapshot(self, snapshot: PlantSnapshot) -> None:
        inv = snapshot.inverter
        evc = snapshot.ev_charger

        # Update EVC status & button highlights
        if evc.connected:
            self.lbl_evc_live_state.setText(
                f"State: {evc.charging_state}  •  Cable: {evc.connection_status}  •  Limit: {evc.charge_limit_a:.0f}A"
            )
            if evc.plug_and_go_enabled:
                self.btn_png_on.setStyleSheet(self._btn_style("#0284C7", "#0EA5E9"))
                self.btn_png_off.setStyleSheet(self._btn_style("#1E293B", "#475569", border="1px solid #334155"))
            else:
                self.btn_png_on.setStyleSheet(self._btn_style("#1E293B", "#0284C7", border="1px solid #334155"))
                self.btn_png_off.setStyleSheet(self._btn_style("#475569", "#64748B"))

            if not self._synced_once_evc and not self.slider_evc_amps.isSliderDown():
                self.slider_evc_amps.setValue(int(round(evc.charge_limit_a)))
                self._synced_once_evc = True
        else:
            self.lbl_evc_live_state.setText("EV Charger Offline")

        # Update Inverter status & controls
        if inv.connected:
            self.lbl_inv_mode.setText(f"Active Mode: {inv.operating_mode_summary}")

            if inv.enable_charge:
                self.btn_chg_enable.setStyleSheet(self._btn_style("#059669", "#10B981"))
                self.btn_chg_disable.setStyleSheet(self._btn_style("#1E293B", "#475569", border="1px solid #334155"))
            else:
                self.btn_chg_enable.setStyleSheet(self._btn_style("#1E293B", "#059669", border="1px solid #334155"))
                self.btn_chg_disable.setStyleSheet(self._btn_style("#475569", "#64748B"))

            if inv.enable_discharge:
                self.btn_dis_enable.setStyleSheet(self._btn_style("#0284C7", "#0EA5E9"))
                self.btn_dis_disable.setStyleSheet(self._btn_style("#1E293B", "#475569", border="1px solid #334155"))
            else:
                self.btn_dis_enable.setStyleSheet(self._btn_style("#1E293B", "#0284C7", border="1px solid #334155"))
                self.btn_dis_disable.setStyleSheet(self._btn_style("#475569", "#64748B"))

            if not self._synced_once_inv:
                self.spin_target_soc.setValue(inv.charge_target_soc)
                self.spin_reserve_soc.setValue(inv.battery_soc_reserve)
                self.spin_chg_limit.setValue(min(50, inv.battery_charge_limit))
                self.spin_dis_limit.setValue(min(50, inv.battery_discharge_limit))
                self.time_cs1_start.setTime(QTime(inv.charge_slot_1_start.hour, inv.charge_slot_1_start.minute))
                self.time_cs1_end.setTime(QTime(inv.charge_slot_1_end.hour, inv.charge_slot_1_end.minute))
                self.time_ds1_start.setTime(QTime(inv.discharge_slot_1_start.hour, inv.discharge_slot_1_start.minute))
                self.time_ds1_end.setTime(QTime(inv.discharge_slot_1_end.hour, inv.discharge_slot_1_end.minute))
                self._synced_once_inv = True
        else:
            self.lbl_inv_mode.setText("Inverter Offline")
