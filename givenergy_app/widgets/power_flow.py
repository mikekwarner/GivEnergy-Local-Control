"""Animated QPainter Energy Flow Diagram and Live Power History Chart."""

from __future__ import annotations

import math
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from givenergy_app.models import PlantSnapshot


def _fmt_power(watts: int | float) -> str:
    w = abs(float(watts))
    if w >= 1000:
        return f"{w / 1000.0:.2f} kW"
    return f"{int(round(w))} W"


class PowerFlowCanvas(QWidget):
    """Hardware-accelerated QPainter schematic showing live power flows across all GivEnergy hardware."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(720, 450)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._snapshot = PlantSnapshot()
        self._phase = 0.0

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick_animation)
        self._anim_timer.start(33)  # ~30 FPS

    def update_snapshot(self, snapshot: PlantSnapshot) -> None:
        self._snapshot = snapshot
        self.update()

    def _tick_animation(self) -> None:
        self._phase = (self._phase + 0.015) % 1.0
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w = float(self.width())
        h = float(self.height())

        # Background card with subtle radial glow
        bg_rect = QRectF(2, 2, w - 4, h - 4)
        bg_grad = QLinearGradient(0, 0, 0, h)
        bg_grad.setColorAt(0.0, QColor("#0F172A"))
        bg_grad.setColorAt(1.0, QColor("#090E1A"))
        painter.setPen(QPen(QColor("#1E293B"), 1.5))
        painter.setBrush(QBrush(bg_grad))
        painter.drawRoundedRect(bg_rect, 14, 14)

        # Subtle background grid dots
        grid_pen = QPen(QColor(51, 65, 85, 65), 1.5)
        painter.setPen(grid_pen)
        step_x = 32
        step_y = 32
        for gx in range(24, int(w - 16), step_x):
            for gy in range(24, int(h - 16), step_y):
                painter.drawPoint(gx, gy)

        inv = self._snapshot.inverter
        bat = self._snapshot.battery
        evc = self._snapshot.ev_charger

        # Node centers
        cx, cy = w * 0.48, h * 0.46  # Center: Hybrid Gen 3 Inverter
        pv_pt = QPointF(w * 0.16, h * 0.22)  # Top-Left: Solar PV
        bat_pt = QPointF(w * 0.16, h * 0.76)  # Bottom-Left: 9.5kWh Battery
        grid_pt = QPointF(w * 0.82, h * 0.22)  # Top-Right: Utility Grid
        home_pt = QPointF(w * 0.50, h * 0.82)  # Bottom-Center: Home Load
        evc_pt = QPointF(w * 0.82, h * 0.76)  # Bottom-Right: EV Charger
        inv_pt = QPointF(cx, cy)

        # Determine flow quantities & directions
        # 1. Solar -> Inverter
        pv_w = inv.p_pv_total
        self._draw_flow_conduit(
            painter,
            start=pv_pt,
            end=inv_pt,
            watts=pv_w,
            color=QColor("#F59E0B"),
            active=pv_w > 5,
            reverse=False,
            ctrl_offset=QPointF(40, -20),
        )

        # 2. Battery <-> Inverter (p_battery > 0 = discharging to inverter, < 0 = charging from inverter)
        bat_w = abs(inv.p_battery)
        bat_discharging = inv.p_battery > 5
        bat_charging = inv.p_battery < -5
        bat_color = QColor("#06B6D4") if bat_discharging else QColor("#10B981")
        self._draw_flow_conduit(
            painter,
            start=bat_pt if bat_discharging else inv_pt,
            end=inv_pt if bat_discharging else bat_pt,
            watts=bat_w,
            color=bat_color,
            active=(bat_discharging or bat_charging),
            reverse=False,
            ctrl_offset=QPointF(40, 20) if bat_discharging else QPointF(-40, 20),
            label_prefix="Discharging " if bat_discharging else ("Charging " if bat_charging else ""),
        )

        # 3. Inverter <-> Grid (p_grid_out > 0 = exporting to grid, < 0 = importing from grid)
        grid_w = abs(inv.p_grid_out)
        grid_exporting = inv.p_grid_out > 10
        grid_importing = inv.p_grid_out < -10
        grid_color = QColor("#10B981") if grid_exporting else QColor("#F43F5E")
        self._draw_flow_conduit(
            painter,
            start=inv_pt if grid_exporting else grid_pt,
            end=grid_pt if grid_exporting else inv_pt,
            watts=grid_w,
            color=grid_color,
            active=(grid_exporting or grid_importing),
            reverse=False,
            ctrl_offset=QPointF(20, -25) if grid_exporting else QPointF(-20, -25),
            label_prefix="Export " if grid_exporting else ("Import " if grid_importing else ""),
        )

        # 4. Inverter -> Home Load
        home_w = max(0, inv.p_load_demand)
        self._draw_flow_conduit(
            painter,
            start=inv_pt,
            end=home_pt,
            watts=home_w,
            color=QColor("#818CF8"),
            active=home_w > 5,
            reverse=False,
            ctrl_offset=QPointF(0, 0),
        )

        # 5. Inverter / AC Bus -> EV Charger
        ev_w = max(0, evc.active_power_w)
        ev_active = ev_w > 10 or evc.charging_state == "Charging"
        self._draw_flow_conduit(
            painter,
            start=inv_pt,
            end=evc_pt,
            watts=ev_w,
            color=QColor("#22D3EE"),
            active=ev_active,
            reverse=False,
            ctrl_offset=QPointF(35, 25),
        )

        # Draw Nodes
        # 1. Solar PV Node
        self._draw_node_card(
            painter,
            center=pv_pt,
            width=224,
            height=114,
            title="SOLAR PV",
            icon_text="☀",
            accent=QColor("#F59E0B"),
            primary_text=_fmt_power(pv_w),
            sub_lines=[
                f"PV1: {inv.p_pv1}W ({inv.v_pv1:.0f}V)  |  PV2: {inv.p_pv2}W ({inv.v_pv2:.0f}V)",
                f"Today: {inv.e_pv_generation_today:.1f} kWh  |  Total: {inv.e_pv_generation_total:,.0f} kWh",
            ],
            badge_text="GENERATING" if pv_w > 10 else "NIGHT / IDLE",
            badge_color=QColor("#F59E0B") if pv_w > 10 else QColor("#64748B"),
        )

        # 2. 9.5 kWh Battery Node (with integrated SoC bar)
        soc = bat.soc if bat.present else inv.battery_soc
        rem_kwh = (bat.remaining_energy_measured_wh / 1000.0) if bat.present else (inv.battery_capacity_kwh * soc / 100.0)
        bat_v = bat.v_out if bat.present and bat.v_out > 0 else inv.v_battery
        bat_i = bat.i_battery if bat.present else inv.i_battery
        bat_temp = bat.t_max if bat.present and bat.t_max > 0 else inv.t_battery
        bat_state_str = "DISCHARGING" if bat_discharging else ("CHARGING" if bat_charging else "STANDBY")
        bat_accent = QColor("#06B6D4") if bat_discharging else (QColor("#10B981") if bat_charging else QColor("#34D399"))
        self._draw_battery_node(
            painter,
            center=bat_pt,
            width=224,
            height=128,
            soc=soc,
            watts=bat_w,
            state_str=bat_state_str,
            accent=bat_accent,
            sub_lines=[
                f"{rem_kwh:.2f} / 9.52 kWh  •  {bat_v:.2f}V  {bat_i:+.1f}A",
                f"Temp: {bat_temp:.1f}°C  •  Cycles: {bat.num_cycles if bat.present else '—'}  •  Δ {bat.cell_delta_mv:.0f}mV",
            ],
        )

        # 3. Center Node: Hybrid Gen 3 Inverter
        inv_accent = QColor("#38BDF8") if inv.connected else QColor("#EF4444")
        inv_badge = inv.status if inv.connected else "OFFLINE"
        self._draw_node_card(
            painter,
            center=inv_pt,
            width=244,
            height=130,
            title=inv.model_name.upper(),
            icon_text="⚡",
            accent=inv_accent,
            primary_text=inv.serial_number if inv.connected else "Connecting...",
            sub_lines=[
                f"Mode: {inv.operating_mode_summary}",
                f"AC Out: {inv.v_ac1_output:.1f}V @ {inv.f_ac1_output:.2f}Hz  ({inv.i_ac1:.1f}A)",
                f"Heatsink: {inv.t_inverter_heatsink:.1f}°C  |  Charger: {inv.t_charger:.1f}°C",
            ],
            badge_text=inv_badge,
            badge_color=QColor("#10B981") if inv.connected and inv.fault_code == "0000" else QColor("#EF4444"),
            is_hub=True,
        )

        # 4. Utility Grid Node
        grid_state = "EXPORTING" if grid_exporting else ("IMPORTING" if grid_importing else "BALANCED")
        grid_node_accent = QColor("#10B981") if grid_exporting else (QColor("#F43F5E") if grid_importing else QColor("#94A3B8"))
        self._draw_node_card(
            painter,
            center=grid_pt,
            width=224,
            height=114,
            title="UTILITY GRID",
            icon_text="🗼",
            accent=grid_node_accent,
            primary_text=_fmt_power(grid_w),
            sub_lines=[
                f"Grid: {inv.v_ac1:.1f}V  •  {inv.f_ac1:.2f}Hz  •  {inv.i_grid_port:.1f}A",
                f"In: {inv.e_grid_in_day:.1f} kWh  |  Out: {inv.e_grid_out_day:.1f} kWh",
            ],
            badge_text=grid_state,
            badge_color=grid_node_accent,
        )

        # 5. Home Load Node
        self._draw_node_card(
            painter,
            center=home_pt,
            width=220,
            height=110,
            title="HOME LOAD",
            icon_text="🏠",
            accent=QColor("#818CF8"),
            primary_text=_fmt_power(home_w),
            sub_lines=[
                f"Used Today: {inv.e_consumption_today:.1f} kWh",
                f"Self-Consumed: {inv.e_self_consumption_today:.1f} kWh  |  EPS: {inv.p_backup}W",
            ],
            badge_text="ACTIVE LOAD",
            badge_color=QColor("#818CF8"),
        )

        # 6. EV Charger Node
        ev_accent = (
            QColor("#10B981")
            if evc.charging_state == "Charging"
            else (QColor("#22D3EE") if evc.cable_connected else QColor("#38BDF8"))
        )
        if not evc.connected:
            ev_accent = QColor("#EF4444")
        ev_badge = evc.charging_state.upper() if evc.connected else "OFFLINE"
        p_and_g = "Plug&Go ON" if evc.plug_and_go_enabled else "Plug&Go OFF"
        self._draw_node_card(
            painter,
            center=evc_pt,
            width=224,
            height=128,
            title="EV CHARGER",
            icon_text="🚗",
            accent=ev_accent,
            primary_text=_fmt_power(ev_w),
            sub_lines=[
                f"Cable: {evc.connection_status}  •  {p_and_g}",
                f"{evc.voltage_l1_v:.1f}V  •  {evc.current_l1_a:.1f}A / {evc.charge_limit_a:.0f}A Limit",
                f"Session: {evc.session_energy_kwh:.1f} kWh  |  Total: {evc.total_meter_energy_kwh:,.1f} kWh",
            ],
            badge_text=ev_badge,
            badge_color=ev_accent,
        )

        # Top-left title overlay
        painter.setPen(QColor("#94A3B8"))
        title_font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        painter.setFont(title_font)
        painter.drawText(QRectF(18, 12, 320, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "LIVE SYSTEM POWER TOPOLOGY")

    def _draw_flow_conduit(
        self,
        painter: QPainter,
        start: QPointF,
        end: QPointF,
        watts: int | float,
        color: QColor,
        active: bool,
        reverse: bool = False,
        ctrl_offset: QPointF = QPointF(0, 0),
        label_prefix: str = "",
    ) -> None:
        mid = QPointF((start.x() + end.x()) * 0.5 + ctrl_offset.x(), (start.y() + end.y()) * 0.5 + ctrl_offset.y())
        path = QPainterPath(start)
        path.quadTo(mid, end)

        # Base conduit track
        track_pen = QPen(QColor(51, 65, 85, 140), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        if not active:
            return

        # Outer glow
        glow_color = QColor(color)
        glow_color.setAlpha(55)
        glow_pen = QPen(glow_color, 9.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(glow_pen)
        painter.drawPath(path)

        # Active colored core line
        core_color = QColor(color)
        core_color.setAlpha(190)
        core_pen = QPen(core_color, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(core_pen)
        painter.drawPath(path)

        # Animated particles along path
        num_particles = 4
        speed_mult = min(3.0, max(0.8, math.log10(max(10.0, float(watts))) - 0.5))
        for i in range(num_particles):
            t = ((self._phase * speed_mult) + (i / num_particles)) % 1.0
            if reverse:
                t = 1.0 - t
            pt = path.pointAtPercent(t)

            rad = QRadialGradient(pt, 7.0)
            rad.setColorAt(0.0, QColor("#FFFFFF"))
            rad.setColorAt(0.45, color)
            end_c = QColor(color)
            end_c.setAlpha(0)
            rad.setColorAt(1.0, end_c)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(rad))
            painter.drawEllipse(pt, 7.0, 7.0)

        # Floating wattage badge at path midpoint
        label_pt = path.pointAtPercent(0.5)
        badge_text = f"{label_prefix}{_fmt_power(watts)}"
        font = QFont("Segoe UI", 8, QFont.Weight.Bold)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(badge_text) + 16
        th = 20
        pill_rect = QRectF(label_pt.x() - tw / 2, label_pt.y() - th / 2, tw, th)

        painter.setPen(QPen(color, 1.2))
        painter.setBrush(QColor(15, 23, 42, 230))
        painter.drawRoundedRect(pill_rect, 10, 10)

        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

    def _draw_node_card(
        self,
        painter: QPainter,
        center: QPointF,
        width: float,
        height: float,
        title: str,
        icon_text: str,
        accent: QColor,
        primary_text: str,
        sub_lines: list[str],
        badge_text: str,
        badge_color: QColor,
        is_hub: bool = False,
    ) -> None:
        rect = QRectF(center.x() - width / 2, center.y() - height / 2, width, height)

        # Subtle outer glow for hub or active cards
        glow = QColor(accent)
        glow.setAlpha(45 if is_hub else 25)
        painter.setPen(QPen(glow, 5.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 14, 14)

        # Card body
        card_grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        card_grad.setColorAt(0.0, QColor(30, 41, 59, 242))
        card_grad.setColorAt(1.0, QColor(15, 23, 42, 248))

        border_color = QColor(accent)
        border_color.setAlpha(180 if is_hub else 130)
        painter.setPen(QPen(border_color, 1.8 if is_hub else 1.3))
        painter.setBrush(QBrush(card_grad))
        painter.drawRoundedRect(rect, 12, 12)

        # Top header strip
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.setPen(accent)
        painter.drawText(
            QRectF(rect.left() + 12, rect.top() + 8, width - 90, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{icon_text}  {title}",
        )

        # Status Pill Badge in top-right
        badge_font = QFont("Segoe UI", 7, QFont.Weight.Bold)
        painter.setFont(badge_font)
        fm = painter.fontMetrics()
        bw = fm.horizontalAdvance(badge_text) + 12
        bh = 16
        b_rect = QRectF(rect.right() - bw - 10, rect.top() + 8, bw, bh)
        b_bg = QColor(badge_color)
        b_bg.setAlpha(40)
        painter.setPen(QPen(badge_color, 1.0))
        painter.setBrush(b_bg)
        painter.drawRoundedRect(b_rect, 8, 8)
        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(b_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

        # Primary metric
        painter.setFont(QFont("Segoe UI", 15 if not is_hub else 13, QFont.Weight.Bold))
        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(
            QRectF(rect.left() + 12, rect.top() + 28, width - 24, 26),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            primary_text,
        )

        # Sub-lines
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#94A3B8"))
        y_offset = rect.top() + 58
        for line in sub_lines:
            painter.drawText(
                QRectF(rect.left() + 12, y_offset, width - 24, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                line,
            )
            y_offset += 16

    def _draw_battery_node(
        self,
        painter: QPainter,
        center: QPointF,
        width: float,
        height: float,
        soc: int,
        watts: int | float,
        state_str: str,
        accent: QColor,
        sub_lines: list[str],
    ) -> None:
        rect = QRectF(center.x() - width / 2, center.y() - height / 2, width, height)

        glow = QColor(accent)
        glow.setAlpha(30)
        painter.setPen(QPen(glow, 5.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 14, 14)

        card_grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        card_grad.setColorAt(0.0, QColor(30, 41, 59, 242))
        card_grad.setColorAt(1.0, QColor(15, 23, 42, 248))

        border_color = QColor(accent)
        border_color.setAlpha(150)
        painter.setPen(QPen(border_color, 1.5))
        painter.setBrush(QBrush(card_grad))
        painter.drawRoundedRect(rect, 12, 12)

        # Header
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.setPen(accent)
        painter.drawText(
            QRectF(rect.left() + 12, rect.top() + 8, width - 95, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "🔋  9.5 kWh BATTERY",
        )

        # State badge
        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        fm = painter.fontMetrics()
        bw = fm.horizontalAdvance(state_str) + 12
        b_rect = QRectF(rect.right() - bw - 10, rect.top() + 8, bw, 16)
        b_bg = QColor(accent)
        b_bg.setAlpha(40)
        painter.setPen(QPen(accent, 1.0))
        painter.setBrush(b_bg)
        painter.drawRoundedRect(b_rect, 8, 8)
        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(b_rect, Qt.AlignmentFlag.AlignCenter, state_str)

        # SoC % + Power
        painter.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(
            QRectF(rect.left() + 12, rect.top() + 27, width - 24, 24),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{soc}%   •   {_fmt_power(watts)}",
        )

        # Animated SoC Progress Bar
        bar_rect = QRectF(rect.left() + 12, rect.top() + 55, width - 24, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0F172A"))
        painter.drawRoundedRect(bar_rect, 5, 5)

        fill_w = max(6.0, (width - 24) * max(0.0, min(100.0, float(soc))) / 100.0)
        fill_rect = QRectF(rect.left() + 12, rect.top() + 55, fill_w, 10)
        bar_grad = QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
        if soc > 25:
            bar_grad.setColorAt(0.0, QColor("#059669"))
            bar_grad.setColorAt(1.0, QColor("#34D399"))
        else:
            bar_grad.setColorAt(0.0, QColor("#D97706"))
            bar_grad.setColorAt(1.0, QColor("#F59E0B"))
        painter.setBrush(QBrush(bar_grad))
        painter.drawRoundedRect(fill_rect, 5, 5)

        # Sub-lines
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#94A3B8"))
        y_offset = rect.top() + 71
        for line in sub_lines:
            painter.drawText(
                QRectF(rect.left() + 12, y_offset, width - 24, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                line,
            )
            y_offset += 16


class PowerHistoryChart(QWidget):
    """Live multi-series time-series chart tracking Solar, Battery, Home, Grid, and EV Power."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(185)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._snapshot = PlantSnapshot()

    def update_snapshot(self, snapshot: PlantSnapshot) -> None:
        self._snapshot = snapshot
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

        # Header & Legend
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(QColor("#94A3B8"))
        painter.drawText(QRectF(16, 10, 260, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "LIVE TELEMETRY TIMELINE (WATTS)")

        series_meta = [
            ("Solar PV", QColor("#F59E0B")),
            ("Home Load", QColor("#818CF8")),
            ("Battery", QColor("#10B981")),
            ("EV Charger", QColor("#22D3EE")),
            ("Grid Import", QColor("#F43F5E")),
        ]
        lx = w - 470
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        for label, col in series_meta:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(col)
            painter.drawEllipse(QPointF(lx, 20), 4.5, 4.5)
            painter.setPen(QColor("#CBD5E1"))
            painter.drawText(QRectF(lx + 8, 10, 75, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            lx += 90

        history = self._snapshot.history
        plot_left = 58.0
        plot_right = w - 20.0
        plot_top = 38.0
        plot_bottom = h - 24.0
        plot_w = max(10.0, plot_right - plot_left)
        plot_h = max(10.0, plot_bottom - plot_top)

        # Determine Y scale
        max_w = 1000.0
        for pt in history:
            max_w = max(
                max_w,
                float(pt.solar_w),
                float(pt.home_w),
                abs(float(pt.battery_w)),
                float(pt.ev_w),
                abs(float(pt.grid_w)),
            )
        max_w = math.ceil(max_w / 500.0) * 500.0

        # Horizontal grid lines
        painter.setFont(QFont("Segoe UI", 7))
        for i in range(5):
            frac = i / 4.0
            y = plot_bottom - frac * plot_h
            val = int(round(frac * max_w))
            painter.setPen(QPen(QColor(51, 65, 85, 90), 1.0, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(plot_left, y), QPointF(plot_right, y))
            painter.setPen(QColor("#64748B"))
            painter.drawText(
                QRectF(6, y - 8, plot_left - 12, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{val}W",
            )

        if not history:
            painter.setPen(QColor("#64748B"))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(
                QRectF(plot_left, plot_top, plot_w, plot_h),
                Qt.AlignmentFlag.AlignCenter,
                "Collecting live telemetry samples...",
            )
            return

        def _draw_series(values: list[float], color: QColor, fill_alpha: int = 25) -> None:
            if not values:
                return
            path = QPainterPath()
            fill_path = QPainterPath()
            n = len(values)
            for idx, v in enumerate(values):
                x = plot_left + (idx / max(1, n - 1)) * plot_w if n > 1 else plot_left + plot_w * 0.5
                y = plot_bottom - (max(0.0, v) / max_w) * plot_h
                if idx == 0:
                    path.moveTo(x, y)
                    fill_path.moveTo(x, plot_bottom)
                    fill_path.lineTo(x, y)
                else:
                    path.lineTo(x, y)
                    fill_path.lineTo(x, y)
            last_x = plot_left + plot_w if n > 1 else plot_left + plot_w * 0.5
            fill_path.lineTo(last_x, plot_bottom)
            fill_path.closeSubpath()

            if n > 1:
                fill_col = QColor(color)
                fill_col.setAlpha(fill_alpha)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill_col)
                painter.drawPath(fill_path)

                painter.setPen(QPen(color, 2.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)

            # Draw latest point dot
            latest_v = max(0.0, values[-1])
            lx_pt = plot_left + plot_w if n > 1 else plot_left + plot_w * 0.5
            ly_pt = plot_bottom - (latest_v / max_w) * plot_h
            painter.setPen(QPen(QColor("#0F172A"), 1.5))
            painter.setBrush(color)
            painter.drawEllipse(QPointF(lx_pt, ly_pt), 4.0, 4.0)

        _draw_series([float(p.solar_w) for p in history], QColor("#F59E0B"), 28)
        _draw_series([float(p.home_w) for p in history], QColor("#818CF8"), 22)
        _draw_series([abs(float(p.battery_w)) for p in history], QColor("#10B981"), 22)
        _draw_series([float(p.ev_w) for p in history], QColor("#22D3EE"), 25)
        _draw_series([max(0.0, -float(p.grid_w)) for p in history], QColor("#F43F5E"), 18)

        # Time axis labels
        first_ts = history[0].timestamp.strftime("%H:%M:%S")
        last_ts = history[-1].timestamp.strftime("%H:%M:%S")
        painter.setPen(QColor("#64748B"))
        painter.setFont(QFont("Segoe UI", 7))
        painter.drawText(QRectF(plot_left, plot_bottom + 4, 80, 16), Qt.AlignmentFlag.AlignLeft, first_ts)
        painter.drawText(QRectF(plot_right - 80, plot_bottom + 4, 80, 16), Qt.AlignmentFlag.AlignRight, f"{last_ts} ({len(history)} pts)")
