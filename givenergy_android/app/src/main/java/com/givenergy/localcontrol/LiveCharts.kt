package com.givenergy.localcontrol

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

private val FlowSolar = Color(0xFFF59E0B)
private val FlowGridExport = Color(0xFF06B6D4)
private val FlowGridImport = Color(0xFFF43F5E)
private val FlowInverter = Color(0xFF3B82F6)
private val FlowHome = Color(0xFF8B5CF6)
private val FlowBatChg = Color(0xFF10B981)
private val FlowBatDis = Color(0xFF2DD4BF)
private val FlowEvc = Color(0xFFEC4899)
private val FlowSoc = Color(0xFF38BDF8)

private fun fmtCompactWatts(w: Double): String {
    val a = abs(w)
    return if (a >= 1000.0) String.format("%.2f kW", w / 1000.0) else "${w.roundToInt()} W"
}

@Composable
fun LivePowerFlowSchematic(state: PlantUiState) {
    val inv = state.inverter
    val bat = state.battery
    val evc = state.evc

    val pPv = inv.pPvTotal
    val pBat = inv.pBattery
    val pGrid = inv.pGrid
    val pEvc = if (evc.enabled) evc.powerW else 0.0
    val pHome = max(0.0, inv.pLoad - pEvc)

    val batDir = when {
        pBat > 25 -> -1   // Battery -> Inverter (discharging)
        pBat < -25 -> 1   // Inverter -> Battery (charging)
        else -> 0
    }
    val batCol = if (pBat >= 0) FlowBatDis else FlowBatChg

    val gridDir = when {
        pGrid > 25 -> 1   // Inverter -> Grid (exporting)
        pGrid < -25 -> -1 // Grid -> Inverter (importing)
        else -> 0
    }
    val gridCol = if (pGrid >= 0) FlowGridExport else FlowGridImport

    val infiniteTransition = rememberInfiniteTransition(label = "powerFlow")
    val phase by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 1800, easing = LinearEasing),
            repeatMode = RepeatMode.Restart,
        ),
        label = "flowPhase",
    )

    val textMeasurer = rememberTextMeasurer()

    Canvas(
        modifier = Modifier
            .fillMaxWidth()
            .height(330.dp)
            .background(Color(0xFF0B101D), RoundedCornerShape(10.dp))
    ) {
        val w = size.width
        val h = size.height

        // Subtle schematic grid
        val gridStep = 28.dp.toPx()
        var gx = gridStep
        while (gx < w) {
            drawLine(Color(0xFF1E293B).copy(alpha = 0.4f), Offset(gx, 0f), Offset(gx, h), strokeWidth = 1f)
            gx += gridStep
        }
        var gy = gridStep
        while (gy < h) {
            drawLine(Color(0xFF1E293B).copy(alpha = 0.4f), Offset(0f, gy), Offset(w, gy), strokeWidth = 1f)
            gy += gridStep
        }

        val boxW = w * 0.36f
        val boxH = 56.dp.toPx()
        val lx = w * 0.21f
        val rx = w * 0.79f
        val y1 = h * 0.13f
        val y2 = h * 0.50f
        val y3 = h * 0.87f

        val ptPv = Offset(lx, y1)
        val ptGrid = Offset(rx, y1)
        val ptInv = Offset(lx, y2)
        val ptHome = Offset(rx, y2)
        val ptBat = Offset(lx, y3)
        val ptEvc = Offset(rx, y3)

        // 1. Flow lines underneath node boxes
        if (state.hasSolar) {
            drawAnimatedFlow(
                start = ptPv,
                end = ptInv,
                color = FlowSolar,
                dir = if (pPv > 15) 1 else 0,
                phase = phase,
                label = fmtCompactWatts(pPv),
                textMeasurer = textMeasurer,
            )
        }
        drawAnimatedFlow(
            start = ptInv,
            end = ptGrid,
            color = gridCol,
            dir = gridDir,
            phase = phase,
            label = fmtCompactWatts(abs(pGrid)),
            textMeasurer = textMeasurer,
        )
        drawAnimatedFlow(
            start = ptInv,
            end = ptHome,
            color = FlowHome,
            dir = if (pHome > 15) 1 else 0,
            phase = phase,
            label = fmtCompactWatts(pHome),
            textMeasurer = textMeasurer,
        )
        drawAnimatedFlow(
            start = ptInv,
            end = ptBat,
            color = batCol,
            dir = batDir,
            phase = phase,
            label = fmtCompactWatts(abs(pBat)),
            textMeasurer = textMeasurer,
        )
        if (evc.enabled) {
            drawAnimatedFlow(
                start = ptHome,
                end = ptEvc,
                color = FlowEvc,
                dir = if (pEvc > 15) 1 else 0,
                phase = phase,
                label = fmtCompactWatts(pEvc),
                textMeasurer = textMeasurer,
            )
        }

        // 2. Node boxes
        if (state.hasSolar) {
            drawNodeCard(
                center = ptPv,
                boxW = boxW,
                boxH = boxH,
                accent = FlowSolar,
                title = "☀ SOLAR PV",
                value = fmtCompactWatts(pPv),
                sub = "${String.format("%.1f", inv.ePvToday)} kWh today",
                textMeasurer = textMeasurer,
            )
        } else {
            drawNodeCard(
                center = ptPv,
                boxW = boxW,
                boxH = boxH,
                accent = Color(0xFF475569),
                title = "☀ SOLAR PV",
                value = "N/A",
                sub = "Battery-only site",
                textMeasurer = textMeasurer,
            )
        }

        val gridValueText = when {
            pGrid > 25 -> "Exp ${fmtCompactWatts(pGrid)}"
            pGrid < -25 -> "Imp ${fmtCompactWatts(abs(pGrid))}"
            else -> "0 W"
        }
        drawNodeCard(
            center = ptGrid,
            boxW = boxW,
            boxH = boxH,
            accent = gridCol,
            title = "🗼 GRID",
            value = gridValueText,
            sub = "${String.format("%.0f", inv.vGrid)}V • ${String.format("%.2f", inv.fGrid)}Hz",
            textMeasurer = textMeasurer,
        )

        drawNodeCard(
            center = ptInv,
            boxW = boxW,
            boxH = boxH,
            accent = FlowInverter,
            title = "⚡ INVERTER",
            value = inv.operatingMode,
            sub = if (inv.connected) "${String.format("%.1f", inv.inverterTempC)}°C • Online" else "Offline",
            textMeasurer = textMeasurer,
        )

        drawNodeCard(
            center = ptHome,
            boxW = boxW,
            boxH = boxH,
            accent = FlowHome,
            title = "🏠 HOME",
            value = fmtCompactWatts(pHome),
            sub = "${String.format("%.1f", inv.eLoadToday)} kWh today",
            textMeasurer = textMeasurer,
        )

        val batSub = when {
            pBat > 25 -> "Dis ${fmtCompactWatts(pBat)}"
            pBat < -25 -> "Chg ${fmtCompactWatts(abs(pBat))}"
            else -> "Idle"
        }
        drawNodeCard(
            center = ptBat,
            boxW = boxW,
            boxH = boxH,
            accent = FlowBatChg,
            title = "🔋 BATTERY",
            value = "${bat.soc.roundToInt()}%",
            sub = "$batSub • ${String.format("%.1f", bat.voltage)}V",
            textMeasurer = textMeasurer,
        )

        if (evc.enabled) {
            drawNodeCard(
                center = ptEvc,
                boxW = boxW,
                boxH = boxH,
                accent = FlowEvc,
                title = "🚗 EV CHARGER",
                value = fmtCompactWatts(pEvc),
                sub = "${evc.chargeStatus} • ${String.format("%.1f", evc.sessionEnergyKwh)} kWh",
                textMeasurer = textMeasurer,
            )
        } else {
            val cs = state.costSummary
            val sym = cs.currencySymbol
            val prefix = if (cs.todayNetCostGbp < 0) "-" else ""
            drawNodeCard(
                center = ptEvc,
                boxW = boxW,
                boxH = boxH,
                accent = if (cs.todayNetCostGbp <= 0) FlowBatChg else FlowSolar,
                title = "💷 TODAY COST",
                value = "$prefix$sym${String.format("%.2f", abs(cs.todayNetCostGbp))}",
                sub = "In $sym${String.format("%.2f", cs.todayImportCostGbp)} • Out $sym${String.format("%.2f", cs.todayExportIncomeGbp)}",
                textMeasurer = textMeasurer,
            )
        }
    }
}

private fun DrawScope.drawAnimatedFlow(
    start: Offset,
    end: Offset,
    color: Color,
    dir: Int,
    phase: Float,
    label: String,
    textMeasurer: TextMeasurer,
) {
    if (dir == 0) {
        drawLine(
            color = Color(0xFF334155),
            start = start,
            end = end,
            strokeWidth = 2.dp.toPx(),
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 10f)),
        )
        return
    }

    drawLine(
        color = color.copy(alpha = 0.28f),
        start = start,
        end = end,
        strokeWidth = 6.dp.toPx(),
        cap = StrokeCap.Round,
    )
    drawLine(
        color = color.copy(alpha = 0.85f),
        start = start,
        end = end,
        strokeWidth = 2.5.dp.toPx(),
        cap = StrokeCap.Round,
    )

    val dotCount = 3
    for (i in 0 until dotCount) {
        val rawT = (phase + i.toFloat() / dotCount) % 1f
        val t = if (dir > 0) rawT else (1f - rawT)
        val dx = start.x + (end.x - start.x) * t
        val dy = start.y + (end.y - start.y) * t
        drawCircle(
            color = Color.White,
            radius = 3.5.dp.toPx(),
            center = Offset(dx, dy),
        )
        drawCircle(
            color = color,
            radius = 5.5.dp.toPx(),
            center = Offset(dx, dy),
            alpha = 0.45f,
        )
    }

    val mid = Offset((start.x + end.x) / 2f, (start.y + end.y) / 2f)
    val measured = textMeasurer.measure(
        text = label,
        style = TextStyle(color = Color.White, fontSize = 9.5.sp, fontWeight = FontWeight.Bold),
    )
    val padX = 5.dp.toPx()
    val padY = 2.dp.toPx()
    val pw = measured.size.width + padX * 2
    val ph = measured.size.height + padY * 2
    drawRoundRect(
        color = Color(0xFF0F172A),
        topLeft = Offset(mid.x - pw / 2f, mid.y - ph / 2f),
        size = Size(pw, ph),
        cornerRadius = CornerRadius(6.dp.toPx(), 6.dp.toPx()),
    )
    drawRoundRect(
        color = color.copy(alpha = 0.7f),
        topLeft = Offset(mid.x - pw / 2f, mid.y - ph / 2f),
        size = Size(pw, ph),
        cornerRadius = CornerRadius(6.dp.toPx(), 6.dp.toPx()),
        style = Stroke(width = 1.dp.toPx()),
    )
    drawText(
        textLayoutResult = measured,
        topLeft = Offset(mid.x - measured.size.width / 2f, mid.y - measured.size.height / 2f),
    )
}

private fun DrawScope.drawNodeCard(
    center: Offset,
    boxW: Float,
    boxH: Float,
    accent: Color,
    title: String,
    value: String,
    sub: String,
    textMeasurer: TextMeasurer,
) {
    val left = center.x - boxW / 2f
    val top = center.y - boxH / 2f
    val radius = CornerRadius(10.dp.toPx(), 10.dp.toPx())

    drawRoundRect(
        color = Color(0xFF0F172A),
        topLeft = Offset(left, top),
        size = Size(boxW, boxH),
        cornerRadius = radius,
    )
    drawRoundRect(
        color = accent.copy(alpha = 0.65f),
        topLeft = Offset(left, top),
        size = Size(boxW, boxH),
        cornerRadius = radius,
        style = Stroke(width = 1.4.dp.toPx()),
    )

    val textConstraintW = (boxW - 10.dp.toPx()).toInt().coerceAtLeast(10)
    val titleRes = textMeasurer.measure(
        text = title,
        style = TextStyle(color = accent, fontSize = 8.5.sp, fontWeight = FontWeight.Bold),
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        constraints = androidx.compose.ui.unit.Constraints(maxWidth = textConstraintW),
    )
    val valRes = textMeasurer.measure(
        text = value,
        style = TextStyle(color = Color(0xFFF8FAFC), fontSize = 12.5.sp, fontWeight = FontWeight.Bold),
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        constraints = androidx.compose.ui.unit.Constraints(maxWidth = textConstraintW),
    )
    val subRes = textMeasurer.measure(
        text = sub,
        style = TextStyle(color = Color(0xFF94A3B8), fontSize = 8.5.sp),
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        constraints = androidx.compose.ui.unit.Constraints(maxWidth = textConstraintW),
    )

    val lineGap = 1.5.dp.toPx()
    val totalTextH = titleRes.size.height + valRes.size.height + subRes.size.height + lineGap * 2f
    var curY = top + (boxH - totalTextH) / 2f
    drawText(titleRes, topLeft = Offset(center.x - titleRes.size.width / 2f, curY))
    curY += titleRes.size.height + lineGap
    drawText(valRes, topLeft = Offset(center.x - valRes.size.width / 2f, curY))
    curY += valRes.size.height + lineGap
    drawText(subRes, topLeft = Offset(center.x - subRes.size.width / 2f, curY))
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun LiveTimelineChart(
    points: List<LivePoint>,
    hasSolar: Boolean,
) {
    var showSolar by remember { mutableStateOf(hasSolar) }
    var showLoad by remember { mutableStateOf(true) }
    var showBat by remember { mutableStateOf(true) }
    var showGrid by remember { mutableStateOf(true) }
    var showSoc by remember { mutableStateOf(true) }
    var selectedIdx by remember { mutableStateOf<Int?>(null) }

    val activeIdx = selectedIdx?.coerceIn(0, (points.size - 1).coerceAtLeast(0)) ?: (points.size - 1)
    val activePt = points.getOrNull(activeIdx)

    Column {
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            if (hasSolar) {
                TimelineToggleChip("Solar", showSolar, FlowSolar) { showSolar = !showSolar }
            }
            TimelineToggleChip("Load", showLoad, FlowHome) { showLoad = !showLoad }
            TimelineToggleChip("Bat", showBat, FlowBatChg) { showBat = !showBat }
            TimelineToggleChip("Grid", showGrid, FlowGridImport) { showGrid = !showGrid }
            TimelineToggleChip("SoC %", showSoc, FlowSoc) { showSoc = !showSoc }
        }

        if (activePt != null) {
            Spacer(Modifier.height(6.dp))
            Card(
                colors = CardDefaults.cardColors(containerColor = Color(0xFF0F172A)),
                border = BorderStroke(1.dp, Color(0xFF1F2937)),
                shape = RoundedCornerShape(10.dp),
            ) {
                Column(modifier = Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(
                            if (selectedIdx != null) "Selected: ${formatLiveTs(activePt.ts)}" else "Latest: ${formatLiveTs(activePt.ts)}",
                            color = Color(0xFFF1F5F9),
                            fontSize = 11.5.sp,
                            fontWeight = FontWeight.Bold,
                        )
                        Text(
                            "SoC ${activePt.soc.roundToInt()}%",
                            color = FlowSoc,
                            fontSize = 11.5.sp,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                    Spacer(Modifier.height(4.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        if (hasSolar) {
                            Text("PV ${fmtCompactWatts(activePt.pvW)}", color = FlowSolar, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                        }
                        Text("Load ${fmtCompactWatts(activePt.loadW)}", color = FlowHome, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                        Text("Bat ${fmtCompactWatts(activePt.batW)}", color = FlowBatChg, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                        Text("Grid ${fmtCompactWatts(activePt.gridW)}", color = FlowGridImport, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }

        Spacer(Modifier.height(8.dp))

        var maxAbs = 500.0
        for (p in points) {
            if (showSolar) maxAbs = max(maxAbs, abs(p.pvW))
            if (showLoad) maxAbs = max(maxAbs, abs(p.loadW))
            if (showBat) maxAbs = max(maxAbs, abs(p.batW))
            if (showGrid) maxAbs = max(maxAbs, abs(p.gridW))
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text("+${fmtCompactWatts(maxAbs)}", color = Color(0xFF94A3B8), fontSize = 10.sp)
            if (showSoc) Text("SoC 100%", color = FlowSoc, fontSize = 10.sp)
        }

        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(220.dp)
                .pointerInput(points) {
                    detectTapGestures { offset ->
                        if (points.isNotEmpty() && size.width > 0f) {
                            val idx = ((offset.x / size.width) * (points.size - 1))
                                .roundToInt()
                                .coerceIn(0, points.size - 1)
                            selectedIdx = idx
                        }
                    }
                }
                .pointerInput(points) {
                    detectHorizontalDragGestures { change, _ ->
                        change.consume()
                        if (points.isNotEmpty() && size.width > 0f) {
                            val idx = ((change.position.x / size.width) * (points.size - 1))
                                .roundToInt()
                                .coerceIn(0, points.size - 1)
                            selectedIdx = idx
                        }
                    }
                }
        ) {
            val w = size.width
            val h = size.height
            val minVal = -maxAbs
            val range = max(1.0, maxAbs - minVal)

            fun yForW(v: Double): Float = (h - ((v - minVal) / range) * h).toFloat()
            fun yForSoc(s: Double): Float = (h - (s.coerceIn(0.0, 100.0) / 100.0) * h).toFloat()
            fun xFor(i: Int): Float = if (points.size <= 1) 0f else (i.toFloat() / (points.size - 1)) * w

            // Subtle horizontal grid lines
            listOf(0.25f, 0.5f, 0.75f).forEach { frac ->
                val y = h * frac
                drawLine(
                    color = Color(0xFF1E293B),
                    start = Offset(0f, y),
                    end = Offset(w, y),
                    strokeWidth = 1f,
                    pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 6f)),
                )
            }

            // Zero baseline
            val y0 = yForW(0.0)
            drawLine(Color(0xFF334155), Offset(0f, y0), Offset(w, y0), strokeWidth = 1.5f)

            fun drawLineSeries(color: Color, strokePx: Float = 3.5f, selector: (LivePoint) -> Double) {
                val path = Path()
                points.forEachIndexed { idx, pt ->
                    val x = xFor(idx)
                    val y = yForW(selector(pt))
                    if (idx == 0) path.moveTo(x, y) else path.lineTo(x, y)
                }
                drawPath(path, color = color, style = Stroke(width = strokePx, cap = StrokeCap.Round))
            }

            if (showGrid) drawLineSeries(FlowGridImport) { it.gridW }
            if (showBat) drawLineSeries(FlowBatChg) { it.batW }
            if (showLoad) drawLineSeries(FlowHome) { it.loadW }
            if (showSolar) drawLineSeries(FlowSolar) { it.pvW }

            if (showSoc) {
                val socPath = Path()
                points.forEachIndexed { idx, pt ->
                    val x = xFor(idx)
                    val y = yForSoc(pt.soc)
                    if (idx == 0) socPath.moveTo(x, y) else socPath.lineTo(x, y)
                }
                drawPath(
                    socPath,
                    color = FlowSoc,
                    style = Stroke(
                        width = 3f,
                        cap = StrokeCap.Round,
                        pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 6f)),
                    ),
                )
            }

            // Scrubbing crosshair
            if (selectedIdx != null && activeIdx in points.indices) {
                val cx = xFor(activeIdx)
                drawLine(
                    color = Color(0xFFCBD5E1).copy(alpha = 0.65f),
                    start = Offset(cx, 0f),
                    end = Offset(cx, h),
                    strokeWidth = 1.5.dp.toPx(),
                )
                val pt = points[activeIdx]
                if (showSolar) drawCircle(FlowSolar, 4.dp.toPx(), Offset(cx, yForW(pt.pvW)))
                if (showLoad) drawCircle(FlowHome, 4.dp.toPx(), Offset(cx, yForW(pt.loadW)))
                if (showBat) drawCircle(FlowBatChg, 4.dp.toPx(), Offset(cx, yForW(pt.batW)))
                if (showGrid) drawCircle(FlowGridImport, 4.dp.toPx(), Offset(cx, yForW(pt.gridW)))
                if (showSoc) drawCircle(FlowSoc, 4.dp.toPx(), Offset(cx, yForSoc(pt.soc)))
            }
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text("-${fmtCompactWatts(maxAbs)}", color = Color(0xFF94A3B8), fontSize = 10.sp)
            if (points.isNotEmpty()) {
                Text(
                    "${formatLiveTs(points.first().ts)}  →  ${formatLiveTs(points.last().ts)}",
                    color = Color(0xFF94A3B8),
                    fontSize = 10.sp,
                )
            }
            if (showSoc) Text("SoC 0%", color = FlowSoc, fontSize = 10.sp)
        }
    }
}

@Composable
private fun TimelineToggleChip(
    label: String,
    selected: Boolean,
    accent: Color,
    onClick: () -> Unit,
) {
    FilterChip(
        selected = selected,
        onClick = onClick,
        label = {
            Text(
                label,
                fontSize = 11.sp,
                color = if (selected) Color.White else Color(0xFF94A3B8),
            )
        },
        colors = FilterChipDefaults.filterChipColors(
            containerColor = Color(0xFF0F172A),
            selectedContainerColor = accent.copy(alpha = 0.35f),
        ),
        border = FilterChipDefaults.filterChipBorder(
            enabled = true,
            selected = selected,
            borderColor = Color(0xFF1F2937),
            selectedBorderColor = accent,
        ),
    )
}

private fun formatLiveTs(raw: String): String {
    if (raw.isBlank()) return "—"
    // Handles ISO strings like "2026-11-20T11:05:30" or "2026-11-20 11:05:30"
    val timePart = raw.substringAfter('T', raw.substringAfter(' ', raw))
    return timePart.take(8)
}
