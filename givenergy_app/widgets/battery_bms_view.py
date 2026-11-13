"""Detailed 9.5kWh Battery & 16-Cell LiFePO4 BMS Inspector Tab."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from givenergy_app.models import PlantSnapshot


class CellVoltageBarChart(QWidget):
    """Custom QPainter bar chart showing all 16 LiFePO4 cell voltages with min/max highlighting."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._voltages: list[float] = [0.0] * 16

    def set_voltages(self, voltages: list[float]) -> None:
        if len(voltages) == 16:
            self._voltages = list(voltages)
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w = float(self.width())
        h = float(self.height())

        # Card background
        bg_rect = QRectF(2, 2, w - 4, h - 4)
        painter.setPen(QPen(QColor("#1E293B"), 1.5))
        painter.setBrush(QColor("#0F172A"))
        painter.drawRoundedRect(bg_rect, 12, 12)

        valid = [v for v in self._voltages if v > 0.1]
        min_v = min(valid) if valid else 3.000
        max_v = max(valid) if valid else 3.400
        avg_v = (sum(valid) / len(valid)) if valid else 3.300
        delta_mv = (max_v - min_v) * 1000.0 if valid else 0.0

        # Header
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(
            QRectF(18, 12, 340, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "16-CELL LiFePO4 VOLTAGE BALANCE (C01 – C16)",
        )

        # Stats summary in top-right
        stats_text = (
            f"Min: {min_v:.3f} V   •   Avg: {avg_v:.3f} V   •   Max: {max_v:.3f} V   •   Spread Δ: {delta_mv:.1f} mV"
        )
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        painter.setPen(QColor("#34D399") if delta_mv < 25 else QColor("#F59E0B"))
        painter.drawText(
            QRectF(w - 520, 12, 500, 22),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            stats_text,
        )

        plot_left = 54.0
        plot_right = w - 20.0
        plot_top = 48.0
        plot_bottom = h - 34.0
        plot_w = max(20.0, plot_right - plot_left)
        plot_h = max(20.0, plot_bottom - plot_top)

        # Dynamic zoomed scale around cell voltages so millivolt differences are clearly visible
        scale_min = max(2.80, min_v - 0.03) if valid else 3.00
        scale_max = min(3.65, max_v + 0.03) if valid else 3.50
        if scale_max - scale_min < 0.04:
            mid = (scale_min + scale_max) * 0.5
            scale_min = mid - 0.025
            scale_max = mid + 0.025

        # Reference lines
        painter.setFont(QFont("Segoe UI", 7))
        for i in range(5):
            frac = i / 4.0
            y = plot_bottom - frac * plot_h
            v_label = scale_min + frac * (scale_max - scale_min)
            painter.setPen(QPen(QColor(51, 65, 85, 90), 1.0, Qt.PenStyle.DashLine))
            painter.drawLine(int(plot_left), int(y), int(plot_right), int(y))
            painter.setPen(QColor("#64748B"))
            painter.drawText(
                QRectF(4, y - 8, plot_left - 8, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{v_label:.3f}V",
            )

        # Draw 16 cell bars
        slot_w = plot_w / 16.0
        bar_w = min(42.0, slot_w * 0.66)

        for idx, cv in enumerate(self._voltages):
            cx = plot_left + (idx + 0.5) * slot_w
            norm = (cv - scale_min) / max(0.001, (scale_max - scale_min)) if cv > 0 else 0.0
            norm = max(0.05, min(1.0, norm))
            bh = norm * plot_h
            bar_rect = QRectF(cx - bar_w / 2, plot_bottom - bh, bar_w, bh)

            is_max = valid and abs(cv - max_v) < 0.0005 and delta_mv > 0.5
            is_min = valid and abs(cv - min_v) < 0.0005 and delta_mv > 0.5

            grad = QLinearGradient(bar_rect.topLeft(), bar_rect.bottomLeft())
            if is_max:
                grad.setColorAt(0.0, QColor("#38BDF8"))
                grad.setColorAt(1.0, QColor("#0284C7"))
                border_col = QColor("#7DD3FC")
            elif is_min:
                grad.setColorAt(0.0, QColor("#FBBF24"))
                grad.setColorAt(1.0, QColor("#D97706"))
                border_col = QColor("#FDE68A")
            else:
                grad.setColorAt(0.0, QColor("#34D399"))
                grad.setColorAt(1.0, QColor("#059669"))
                border_col = QColor("#10B981")

            painter.setPen(QPen(border_col, 1.2))
            painter.setBrush(QBrush(grad))
            painter.drawRoundedRect(bar_rect, 5, 5)

            # Value label above bar
            painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            painter.setPen(QColor("#E2E8F0"))
            painter.drawText(
                QRectF(cx - slot_w / 2, bar_rect.top() - 18, slot_w, 16),
                Qt.AlignmentFlag.AlignCenter,
                f"{cv:.3f}" if cv > 0 else "—",
            )

            # Cell index label below axis
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
            painter.setPen(QColor("#94A3B8"))
            painter.drawText(
                QRectF(cx - slot_w / 2, plot_bottom + 6, slot_w, 18),
                Qt.AlignmentFlag.AlignCenter,
                f"C{idx + 1:02d}",
            )


class BatteryBmsView(QWidget):
    """Tab widget presenting comprehensive 9.5kWh Battery Pack & BMS diagnostics."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # Top KPI row (6 cards)
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(10)

        self.card_soc = self._make_metric_card("PACK STATE OF CHARGE", "— %", "Remaining: — Wh", "#10B981")
        self.card_voltage = self._make_metric_card("PACK & CELL VOLTAGE", "— V", "Cells Sum: — V", "#38BDF8")
        self.card_current = self._make_metric_card("BMS CURRENT & POWER", "— A", "Power: — W", "#06B6D4")
        self.card_capacity = self._make_metric_card("CALIBRATED CAPACITY", "— Ah", "Design: 186.0 Ah (9.52 kWh)", "#A78BFA")
        self.card_cycles = self._make_metric_card("CYCLE COUNT & HEALTH", "— Cycles", "Cell Spread: — mV", "#F59E0B")
        self.card_lifetime = self._make_metric_card("LIFETIME THROUGHPUT", "— kWh In", "Discharged: — kWh", "#EC4899")

        cards = [
            self.card_soc,
            self.card_voltage,
            self.card_current,
            self.card_capacity,
            self.card_cycles,
            self.card_lifetime,
        ]
        for idx, card in enumerate(cards):
            kpi_grid.addWidget(card["frame"], 0, idx)

        root.addLayout(kpi_grid)

        # Middle: 16-Cell Voltage Bar Chart
        self.cell_chart = CellVoltageBarChart()
        root.addWidget(self.cell_chart, stretch=3)

        # Bottom: Thermal Zones & BMS Identity Row
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        self.zone_cards = []
        zone_titles = [
            "THERMAL ZONE 1 (C01–C04)",
            "THERMAL ZONE 2 (C05–C08)",
            "THERMAL ZONE 3 (C09–C12)",
            "THERMAL ZONE 4 (C13–C16)",
            "BMS MOSFET TEMPERATURE",
            "BATTERY PACK IDENTITY",
        ]
        for title in zone_titles:
            c = self._make_metric_card(title, "—", "—", "#38BDF8")
            self.zone_cards.append(c)
            bottom_row.addWidget(c["frame"])

        root.addLayout(bottom_row, stretch=1)

    def _make_metric_card(self, title: str, primary: str, sub: str, accent_hex: str) -> dict:
        frame = QFrame()
        frame.setStyleSheet(
            f"""
            QFrame {{
                background-color: #0F172A;
                border: 1px solid #1E293B;
                border-top: 3px solid {accent_hex};
                border-radius: 10px;
            }}
            """
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(f"color: {accent_hex}; font-size: 10px; font-weight: 700; border: none;")

        lbl_primary = QLabel(primary)
        lbl_primary.setStyleSheet("color: #F8FAFC; font-size: 16px; font-weight: 700; border: none;")

        lbl_sub = QLabel(sub)
        lbl_sub.setStyleSheet("color: #94A3B8; font-size: 10px; border: none;")

        lay.addWidget(lbl_title)
        lay.addWidget(lbl_primary)
        lay.addWidget(lbl_sub)

        return {"frame": frame, "title": lbl_title, "primary": lbl_primary, "sub": lbl_sub}

    def update_snapshot(self, snapshot: PlantSnapshot) -> None:
        bat = snapshot.battery
        inv = snapshot.inverter

        if not bat.present:
            return

        self.card_soc["primary"].setText(f"{bat.soc}% ({bat.cap_remaining:.1f} Ah)")
        self.card_soc["sub"].setText(f"{bat.remaining_energy_measured_wh:,} Wh ({bat.remaining_energy_nominal_wh:,} Wh nom)")

        self.card_voltage["primary"].setText(f"{bat.v_out:.3f} V")
        self.card_voltage["sub"].setText(f"Sum: {bat.v_cells_sum:.2f}V • Inv: {inv.v_battery:.2f}V")

        state_word = "Discharging" if inv.p_battery > 5 else ("Charging" if inv.p_battery < -5 else "Idle")
        self.card_current["primary"].setText(f"{bat.i_battery:+.2f} A")
        self.card_current["sub"].setText(f"{state_word}: {abs(inv.p_battery)} W ({inv.i_battery:.1f}A)")

        health_pct = (bat.cap_calibrated / max(1.0, bat.cap_design)) * 100.0
        self.card_capacity["primary"].setText(f"{bat.cap_calibrated:.1f} Ah ({health_pct:.0f}%)")
        self.card_capacity["sub"].setText(f"Design: {bat.cap_design:.0f} Ah • {inv.battery_capacity_kwh:.2f} kWh")

        self.card_cycles["primary"].setText(f"{bat.num_cycles:,} Cycles")
        self.card_cycles["sub"].setText(f"Δ {bat.cell_delta_mv:.1f}mV ({bat.cell_min_v:.3f}–{bat.cell_max_v:.3f}V)")

        self.card_lifetime["primary"].setText(f"{bat.e_battery_charge_total:,.1f} kWh In")
        self.card_lifetime["sub"].setText(f"Out: {bat.e_battery_discharge_total:,.1f} kWh")

        self.cell_chart.set_voltages(bat.cell_voltages)

        zones = [
            (bat.t_cells_01_04, "Cells 01–04"),
            (bat.t_cells_05_08, "Cells 05–08"),
            (bat.t_cells_09_12, "Cells 09–12"),
            (bat.t_cells_13_16, "Cells 13–16"),
        ]
        for idx, (temp, desc) in enumerate(zones):
            self.zone_cards[idx]["primary"].setText(f"{temp:.1f} °C")
            self.zone_cards[idx]["sub"].setText(f"{desc} ({bat.t_min:.1f}–{bat.t_max:.1f}°C)")

        self.zone_cards[4]["primary"].setText(f"{bat.t_bms_mosfet:.1f} °C")
        self.zone_cards[4]["sub"].setText(f"Status: {'Normal' if bat.warning_1 == 0 else 'Warning'}")

        self.zone_cards[5]["primary"].setText(bat.serial_number)
        self.zone_cards[5]["sub"].setText(f"BMS v{bat.bms_firmware_version} • {bat.num_cells}S LiFePO4")
