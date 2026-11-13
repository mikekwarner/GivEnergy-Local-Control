"""Main Application Window for GivEnergy Local Control & Telemetry."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from givenergy_app.models import PlantSnapshot
from givenergy_app.widgets.battery_bms_view import BatteryBmsView
from givenergy_app.widgets.control_panel import ActiveControlPanel
from givenergy_app.widgets.details_views import EVChargerDetailsView, InverterSolarDetailsView
from givenergy_app.widgets.power_flow import PowerFlowCanvas, PowerHistoryChart
from givenergy_app.workers import TelemetryWorker


class MainWindow(QMainWindow):
    """Primary desktop window displaying live GivEnergy Inverter, 9.5kWh Battery, and EV Charger state."""

    def __init__(
        self,
        inverter_ip: str = "192.168.0.10",
        evc_ip: str = "192.168.0.11",
    ) -> None:
        super().__init__()
        self.setWindowTitle("GivEnergy Local Control — Hybrid Gen 3 Inverter, 9.5kWh Battery & EV Charger")
        self.resize(1480, 920)
        self.setMinimumSize(1180, 760)

        self._apply_global_theme()

        self.worker = TelemetryWorker(
            inverter_ip=inverter_ip,
            inverter_port=8899,
            evc_ip=evc_ip,
            evc_port=502,
            poll_interval_s=5.0,
            parent=self,
        )
        self.worker.snapshot_updated.connect(self._on_snapshot_updated)
        self.worker.status_message.connect(self._on_status_message)

        self._build_ui(inverter_ip, evc_ip)
        self.worker.start()

    def _apply_global_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget#CentralRoot {
                background-color: #070B14;
                color: #F8FAFC;
                font-family: 'Segoe UI', sans-serif;
            }
            QTabWidget::pane {
                border: 1px solid #1E293B;
                border-radius: 10px;
                background-color: #0B1120;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #0F172A;
                color: #94A3B8;
                border: 1px solid #1E293B;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 9px 18px;
                margin-right: 4px;
                font-size: 12px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background-color: #1E293B;
                color: #38BDF8;
                border-top: 2px solid #38BDF8;
            }
            QTabBar::tab:hover:!selected {
                background-color: #162032;
                color: #E2E8F0;
            }
            QScrollBar:vertical {
                background: #0F172A;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #334155;
                min-height: 24px;
                border-radius: 4px;
            }
            """
        )

    def _build_ui(self, inverter_ip: str, evc_ip: str) -> None:
        central = QWidget()
        central.setObjectName("CentralRoot")
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(14, 12, 14, 10)
        root_layout.setSpacing(10)

        # 1. Top Header Bar
        root_layout.addWidget(self._build_header_bar(inverter_ip, evc_ip))

        # 2. Top KPI Summary Cards Row
        root_layout.addLayout(self._build_kpi_strip())

        # 3. Main Split Workspace: Left/Center Tabs + Right Active Controls Sidebar
        workspace_row = QHBoxLayout()
        workspace_row.setSpacing(12)

        self.tabs = QTabWidget()

        # Tab 1: Overview (Animated Power Flow + Live History Chart)
        overview_tab = QWidget()
        ov_layout = QVBoxLayout(overview_tab)
        ov_layout.setContentsMargins(10, 10, 10, 10)
        ov_layout.setSpacing(10)

        self.power_flow_canvas = PowerFlowCanvas()
        self.history_chart = PowerHistoryChart()
        ov_layout.addWidget(self.power_flow_canvas, stretch=5)
        ov_layout.addWidget(self.history_chart, stretch=2)

        # Tab 2: 9.5kWh Battery & 16-Cell BMS
        self.battery_bms_view = BatteryBmsView()

        # Tab 3: Inverter & Solar PV Details
        self.inverter_details_view = InverterSolarDetailsView(self.worker.enqueue_command)

        # Tab 4: EV Charger Details
        self.evc_details_view = EVChargerDetailsView(self.worker.enqueue_command)

        self.tabs.addTab(overview_tab, "⚡  Live Power Flow && Timeline")
        self.tabs.addTab(self.battery_bms_view, "🔋  9.5 kWh Battery && 16-Cell BMS")
        self.tabs.addTab(self.inverter_details_view, "☀  Inverter && Solar PV Telemetry")
        self.tabs.addTab(self.evc_details_view, "🚗  EV Charger Telemetry")

        self.control_panel = ActiveControlPanel(self.worker.enqueue_command)

        workspace_row.addWidget(self.tabs, stretch=1)
        workspace_row.addWidget(self.control_panel)

        root_layout.addLayout(workspace_row, stretch=1)

        # 4. Bottom Status / Activity Bar
        root_layout.addWidget(self._build_status_bar())

    def _build_header_bar(self, inverter_ip: str, evc_ip: str) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet(
            """
            QFrame#HeaderBar {
                background-color: #0F172A;
                border: 1px solid #1E293B;
                border-radius: 12px;
            }
            """
        )
        bar.setObjectName("HeaderBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(14)

        # Brand title & subtitle
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("⚡ GIVENERGY LOCAL CONTROL CENTER")
        lbl_title.setStyleSheet("color: #F8FAFC; font-size: 16px; font-weight: 800; letter-spacing: 0.6px;")
        lbl_sub = QLabel("Direct Local Modbus TCP Telemetry & Control  •  Zero Cloud Dependency")
        lbl_sub.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 600;")
        title_col.addWidget(lbl_title)
        title_col.addWidget(lbl_sub)
        lay.addLayout(title_col)

        lay.addStretch(1)

        # Live connection badges
        self.badge_inv = QLabel("● INVERTER: CONNECTING...")
        self.badge_inv.setStyleSheet(self._pill_style("#F59E0B"))
        self.badge_evc = QLabel("● EV CHARGER: CONNECTING...")
        self.badge_evc.setStyleSheet(self._pill_style("#F59E0B"))
        lay.addWidget(self.badge_inv)
        lay.addWidget(self.badge_evc)

        # Endpoint controls
        lbl_inv_ip = QLabel("Inverter IP:")
        lbl_inv_ip.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.edit_inv_ip = QLineEdit(inverter_ip)
        self.edit_inv_ip.setFixedWidth(105)
        self.edit_inv_ip.setStyleSheet(self._input_style())

        lbl_evc_ip = QLabel("EVC IP:")
        lbl_evc_ip.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.edit_evc_ip = QLineEdit(evc_ip)
        self.edit_evc_ip.setFixedWidth(105)
        self.edit_evc_ip.setStyleSheet(self._input_style())

        lbl_poll = QLabel("Poll:")
        lbl_poll.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.spin_poll = QSpinBox()
        self.spin_poll.setRange(2, 60)
        self.spin_poll.setValue(5)
        self.spin_poll.setSuffix(" s")
        self.spin_poll.setStyleSheet(self._input_style())

        btn_apply_net = QPushButton("Apply")
        btn_apply_net.setStyleSheet(
            """
            QPushButton {
                background-color: #1E293B;
                color: #E2E8F0;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #334155; }
            """
        )
        btn_apply_net.clicked.connect(self._on_apply_endpoints)

        btn_refresh = QPushButton("⟳ Poll Now")
        btn_refresh.setStyleSheet(
            """
            QPushButton {
                background-color: #0284C7;
                color: #F8FAFC;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton:hover { background-color: #0EA5E9; }
            """
        )
        btn_refresh.clicked.connect(self.worker.trigger_immediate_refresh)

        lay.addWidget(lbl_inv_ip)
        lay.addWidget(self.edit_inv_ip)
        lay.addWidget(lbl_evc_ip)
        lay.addWidget(self.edit_evc_ip)
        lay.addWidget(lbl_poll)
        lay.addWidget(self.spin_poll)
        lay.addWidget(btn_apply_net)
        lay.addWidget(btn_refresh)

        return bar

    def _build_kpi_strip(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(10)

        self.kpi_solar = self._make_kpi_card("☀ SOLAR GENERATION", "0 W", "Today: — kWh", "#F59E0B")
        self.kpi_battery = self._make_kpi_card("🔋 9.5 kWh BATTERY", "— %", "Power: — W  •  — Wh", "#10B981")
        self.kpi_home = self._make_kpi_card("🏠 HOME CONSUMPTION", "0 W", "Today: — kWh", "#818CF8")
        self.kpi_grid = self._make_kpi_card("🗼 UTILITY GRID", "0 W", "In: — kWh  |  Out: — kWh", "#38BDF8")
        self.kpi_evc = self._make_kpi_card("🚗 EV CHARGER", "0 W", "State: —  •  Limit: — A", "#22D3EE")
        self.kpi_health = self._make_kpi_card("🌡 SYSTEM HEALTH", "NORMAL", "Inv: —°C  •  Bat: —°C", "#A78BFA")

        for idx, card in enumerate(
            [self.kpi_solar, self.kpi_battery, self.kpi_home, self.kpi_grid, self.kpi_evc, self.kpi_health]
        ):
            grid.addWidget(card["frame"], 0, idx)

        return grid

    def _make_kpi_card(self, title: str, primary: str, sub: str, accent: str) -> dict:
        frame = QFrame()
        frame.setStyleSheet(
            f"""
            QFrame {{
                background-color: #0F172A;
                border: 1px solid #1E293B;
                border-left: 4px solid {accent};
                border-radius: 10px;
            }}
            """
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)

        lbl_t = QLabel(title)
        lbl_t.setStyleSheet(f"color: {accent}; font-size: 10px; font-weight: 700; border: none;")
        lbl_p = QLabel(primary)
        lbl_p.setStyleSheet("color: #F8FAFC; font-size: 18px; font-weight: 800; border: none;")
        lbl_s = QLabel(sub)
        lbl_s.setStyleSheet("color: #94A3B8; font-size: 11px; border: none;")

        lay.addWidget(lbl_t)
        lay.addWidget(lbl_p)
        lay.addWidget(lbl_s)
        return {"frame": frame, "title": lbl_t, "primary": lbl_p, "sub": lbl_s}

    def _build_status_bar(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet(
            """
            QFrame {
                background-color: #0F172A;
                border: 1px solid #1E293B;
                border-radius: 8px;
            }
            """
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 6, 12, 6)

        self.lbl_status_msg = QLabel("Initializing local Modbus connections...")
        self.lbl_status_msg.setStyleSheet("color: #94A3B8; font-size: 11px; border: none;")

        self.lbl_last_poll = QLabel("Last Poll: —")
        self.lbl_last_poll.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 600; border: none;")

        lay.addWidget(self.lbl_status_msg, 1)
        lay.addWidget(self.lbl_last_poll)
        return bar

    def _pill_style(self, color_hex: str) -> str:
        return f"""
            QLabel {{
                background-color: #1E293B;
                color: {color_hex};
                border: 1px solid {color_hex};
                border-radius: 11px;
                padding: 4px 10px;
                font-size: 10px;
                font-weight: 700;
            }}
        """

    def _input_style(self) -> str:
        return """
            background-color: #1E293B;
            color: #F8FAFC;
            border: 1px solid #334155;
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 600;
        """

    def _on_apply_endpoints(self) -> None:
        self.worker.update_endpoints(
            self.edit_inv_ip.text(),
            self.edit_evc_ip.text(),
            float(self.spin_poll.value()),
        )

    def _on_status_message(self, level: str, message: str) -> None:
        colors = {
            "info": "#38BDF8",
            "success": "#10B981",
            "warning": "#F59E0B",
            "error": "#EF4444",
        }
        col = colors.get(level, "#94A3B8")
        ts = datetime.now().strftime("%H:%M:%S")
        self.lbl_status_msg.setStyleSheet(f"color: {col}; font-size: 11px; font-weight: 600; border: none;")
        self.lbl_status_msg.setText(f"[{ts}]  {message}")

    def _on_snapshot_updated(self, snapshot: PlantSnapshot) -> None:
        inv = snapshot.inverter
        bat = snapshot.battery
        evc = snapshot.ev_charger

        # Update Connection Badges
        if inv.connected:
            self.badge_inv.setText(f"● INVERTER {inv.serial_number}: ONLINE")
            self.badge_inv.setStyleSheet(self._pill_style("#10B981"))
        else:
            self.badge_inv.setText("● INVERTER: OFFLINE")
            self.badge_inv.setStyleSheet(self._pill_style("#EF4444"))

        if evc.connected:
            self.badge_evc.setText(f"● EV CHARGER ({evc.charging_state.upper()}): ONLINE")
            self.badge_evc.setStyleSheet(self._pill_style("#22D3EE"))
        else:
            self.badge_evc.setText("● EV CHARGER: OFFLINE")
            self.badge_evc.setStyleSheet(self._pill_style("#EF4444"))

        # Update Top KPI Strip
        self.kpi_solar["primary"].setText(f"{inv.p_pv_total:,} W")
        self.kpi_solar["sub"].setText(
            f"Today: {inv.e_pv_generation_today:.1f} kWh  (PV1 {inv.e_pv1_day:.1f} / PV2 {inv.e_pv2_day:.1f})"
        )

        soc = bat.soc if bat.present else inv.battery_soc
        rem_wh = bat.remaining_energy_measured_wh if bat.present else int(inv.battery_capacity_kwh * soc * 10)
        bat_dir = "Discharging" if inv.p_battery > 5 else ("Charging" if inv.p_battery < -5 else "Idle")
        self.kpi_battery["primary"].setText(f"{soc}%  ({abs(inv.p_battery):,} W)")
        self.kpi_battery["sub"].setText(f"{bat_dir}  •  {rem_wh:,} Wh Remaining")

        self.kpi_home["primary"].setText(f"{inv.p_load_demand:,} W")
        self.kpi_home["sub"].setText(
            f"Today: {inv.e_consumption_today:.1f} kWh  (Self: {inv.e_self_consumption_today:.1f} kWh)"
        )

        grid_dir = "Export" if inv.p_grid_out > 10 else ("Import" if inv.p_grid_out < -10 else "Balanced")
        self.kpi_grid["primary"].setText(f"{abs(inv.p_grid_out):,} W ({grid_dir})")
        self.kpi_grid["sub"].setText(f"Today In: {inv.e_grid_in_day:.1f} kWh  |  Out: {inv.e_grid_out_day:.1f} kWh")

        self.kpi_evc["primary"].setText(f"{evc.active_power_w:,} W  ({evc.charging_state})")
        self.kpi_evc["sub"].setText(
            f"Cable: {evc.connection_status}  •  {evc.charge_limit_a:.0f}A  •  Total: {evc.total_meter_energy_kwh:,.0f} kWh"
        )

        bat_t = bat.t_max if bat.present and bat.t_max > 0 else inv.t_battery
        spread_str = f"Δ {bat.cell_delta_mv:.0f}mV" if bat.present else ""
        self.kpi_health["primary"].setText(f"{inv.status}  {spread_str}")
        self.kpi_health["sub"].setText(f"Inv: {inv.t_inverter_heatsink:.1f}°C  •  Bat: {bat_t:.1f}°C  •  {inv.v_ac1:.1f}V")

        # Update Child Views
        self.power_flow_canvas.update_snapshot(snapshot)
        self.history_chart.update_snapshot(snapshot)
        self.battery_bms_view.update_snapshot(snapshot)
        self.inverter_details_view.update_snapshot(snapshot)
        self.evc_details_view.update_snapshot(snapshot)
        self.control_panel.update_snapshot(snapshot)

        now_str = datetime.now().strftime("%H:%M:%S")
        self.lbl_last_poll.setText(f"Last Poll: {now_str}")

    def closeEvent(self, event) -> None:
        self.worker.stop()
        self.worker.wait(3000)
        super().closeEvent(event)
