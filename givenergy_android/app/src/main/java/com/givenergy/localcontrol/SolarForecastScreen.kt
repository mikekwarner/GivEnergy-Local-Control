package com.givenergy.localcontrol

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.roundToInt

private val FcBgCard = Color(0xFF111827)
private val FcBorderCard = Color(0xFF1F2937)
private val FcTextMain = Color(0xFFF1F5F9)
private val FcTextMuted = Color(0xFF94A3B8)
private val FcAmber = Color(0xFFF59E0B)
private val FcGreen = Color(0xFF10B981)
private val FcBlue = Color(0xFF38BDF8)
private val FcPurple = Color(0xFFA78BFA)
private val FcPrimary = Color(0xFF2563EB)

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SolarForecastScreen(
    state: PlantUiState,
    onRefreshForecast: () -> Unit,
    onSyncOpenMeteo: () -> Unit,
    onSaveConfig: (JSONObject) -> Unit,
) {
    val fc = state.solarForecast
    var showConfig by remember(fc.configured) { mutableStateOf(!fc.configured) }
    var windowMode by remember { mutableIntStateOf(0) } // 0 = 24h+48h, 1 = Today+72h, 2 = All

    var latText by remember(fc.latitude) { mutableStateOf(fc.latitude?.toString() ?: "") }
    var lonText by remember(fc.longitude) { mutableStateOf(fc.longitude?.toString() ?: "") }
    var capText by remember(fc.capacityKwp) { mutableStateOf(fc.capacityKwp.toString()) }
    var tiltText by remember(fc.tiltDeg) { mutableStateOf(fc.tiltDeg.roundToInt().toString()) }
    var azText by remember(fc.azimuthDeg) { mutableStateOf(fc.azimuthDeg.roundToInt().toString()) }
    var a2Enabled by remember(fc.array2Enabled) { mutableStateOf(fc.array2Enabled) }
    var cap2Text by remember(fc.array2CapacityKwp) { mutableStateOf((if (fc.array2CapacityKwp > 0) fc.array2CapacityKwp else 2.0).toString()) }
    var tilt2Text by remember(fc.array2TiltDeg) { mutableStateOf(fc.array2TiltDeg.roundToInt().toString()) }
    var az2Text by remember(fc.array2AzimuthDeg) { mutableStateOf(fc.array2AzimuthDeg.roundToInt().toString()) }
    var baseEffText by remember(fc.baseEfficiency) { mutableStateOf((fc.baseEfficiency * 100.0).roundToInt().toString()) }

    LaunchedEffect(fc.loaded) {
        if (!fc.loaded) onRefreshForecast()
    }

    val tfColors = OutlinedTextFieldDefaults.colors(
        focusedTextColor = FcTextMain,
        unfocusedTextColor = FcTextMain,
        focusedBorderColor = FcBlue,
        unfocusedBorderColor = FcBorderCard,
        focusedLabelColor = FcBlue,
        unfocusedLabelColor = FcTextMuted,
    )

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        "☀ Solar Forecast (Open-Meteo)",
                        color = FcTextMain,
                        fontWeight = FontWeight.Bold,
                        fontSize = 18.sp,
                    )
                    Text(
                        if (fc.lastSyncError.isNotBlank()) "Sync error: ${fc.lastSyncError}"
                        else if (fc.lastSyncAt.isNotBlank()) "Last sync: ${fc.lastSyncAt.replace('T', ' ')}"
                        else "Calibrated by actual PV telemetry",
                        color = FcTextMuted,
                        fontSize = 12.sp,
                    )
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = onSyncOpenMeteo) {
                        Text("⟳ Sync", color = FcTextMain, fontSize = 12.sp)
                    }
                    OutlinedButton(onClick = { showConfig = !showConfig }) {
                        Text(if (showConfig) "✕ Close" else "⚙ Setup", color = FcTextMain, fontSize = 12.sp)
                    }
                }
            }
        }

        if (showConfig) {
            item {
                SectionCard(
                    title = "Solar Forecast Parameters",
                    subtitle = "Open Google Maps, right-click your roof, and copy coordinates (paste 'lat, lon' into Latitude).",
                ) {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedTextField(
                                value = latText,
                                onValueChange = { v ->
                                    if (v.contains(",")) {
                                        val parts = v.split(",", limit = 2).map { it.trim() }
                                        if (parts.size == 2 && parts[0].toDoubleOrNull() != null && parts[1].toDoubleOrNull() != null) {
                                            latText = parts[0]
                                            lonText = parts[1]
                                            return@OutlinedTextField
                                        }
                                    }
                                    latText = v
                                },
                                label = { Text("Latitude") },
                                singleLine = true,
                                colors = tfColors,
                                modifier = Modifier.weight(1f),
                            )
                            OutlinedTextField(
                                value = lonText,
                                onValueChange = { lonText = it },
                                label = { Text("Longitude") },
                                singleLine = true,
                                colors = tfColors,
                                modifier = Modifier.weight(1f),
                            )
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedTextField(
                                value = capText,
                                onValueChange = { capText = it },
                                label = { Text("Capacity (kWp)") },
                                singleLine = true,
                                colors = tfColors,
                                modifier = Modifier.weight(1f),
                            )
                            OutlinedTextField(
                                value = tiltText,
                                onValueChange = { tiltText = it },
                                label = { Text("Tilt (0-90°)") },
                                singleLine = true,
                                colors = tfColors,
                                modifier = Modifier.weight(1f),
                            )
                            OutlinedTextField(
                                value = azText,
                                onValueChange = { azText = it },
                                label = { Text("Azimuth (180=S)") },
                                singleLine = true,
                                colors = tfColors,
                                modifier = Modifier.weight(1f),
                            )
                        }
                        FlowRow(
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            listOf("S 180°" to "180", "SE 135°" to "135", "SW 225°" to "225", "E 90°" to "90", "W 270°" to "270").forEach { (lbl, deg) ->
                                FilterChip(
                                    selected = azText.trim() == deg,
                                    onClick = { azText = deg },
                                    label = { Text(lbl, fontSize = 11.sp) },
                                    colors = FilterChipDefaults.filterChipColors(
                                        selectedContainerColor = FcPrimary,
                                        selectedLabelColor = Color.White,
                                        labelColor = FcTextMuted,
                                    ),
                                )
                            }
                        }

                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text("Enable Second Array / Split Roof", color = FcTextMain, fontSize = 13.sp)
                            Switch(checked = a2Enabled, onCheckedChange = { a2Enabled = it })
                        }

                        if (a2Enabled) {
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                OutlinedTextField(
                                    value = cap2Text,
                                    onValueChange = { cap2Text = it },
                                    label = { Text("Str 2 kWp") },
                                    singleLine = true,
                                    colors = tfColors,
                                    modifier = Modifier.weight(1f),
                                )
                                OutlinedTextField(
                                    value = tilt2Text,
                                    onValueChange = { tilt2Text = it },
                                    label = { Text("Str 2 Tilt°") },
                                    singleLine = true,
                                    colors = tfColors,
                                    modifier = Modifier.weight(1f),
                                )
                                OutlinedTextField(
                                    value = az2Text,
                                    onValueChange = { az2Text = it },
                                    label = { Text("Str 2 Az°") },
                                    singleLine = true,
                                    colors = tfColors,
                                    modifier = Modifier.weight(1f),
                                )
                            }
                        }

                        OutlinedTextField(
                            value = baseEffText,
                            onValueChange = { baseEffText = it },
                            label = { Text("Baseline Efficiency (%)") },
                            singleLine = true,
                            colors = tfColors,
                            modifier = Modifier.fillMaxWidth(),
                        )

                        Button(
                            onClick = {
                                val lat = latText.trim().toDoubleOrNull() ?: return@Button
                                val lon = lonText.trim().toDoubleOrNull() ?: return@Button
                                val payload = JSONObject()
                                    .put("enabled", true)
                                    .put("latitude", lat)
                                    .put("longitude", lon)
                                    .put("capacity_kwp", capText.trim().toDoubleOrNull() ?: 5.0)
                                    .put("tilt_deg", tiltText.trim().toDoubleOrNull() ?: 35.0)
                                    .put("azimuth_deg", azText.trim().toDoubleOrNull() ?: 180.0)
                                    .put("array2_enabled", a2Enabled)
                                    .put("array2_capacity_kwp", cap2Text.trim().toDoubleOrNull() ?: 0.0)
                                    .put("array2_tilt_deg", tilt2Text.trim().toDoubleOrNull() ?: 35.0)
                                    .put("array2_azimuth_deg", az2Text.trim().toDoubleOrNull() ?: 270.0)
                                    .put("base_efficiency", ((baseEffText.trim().toDoubleOrNull() ?: 85.0) / 100.0))
                                    .put("forecast_days", 7)
                                onSaveConfig(payload)
                                showConfig = false
                            },
                            colors = ButtonDefaults.buttonColors(containerColor = FcPrimary),
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text("Save & Sync Open-Meteo Forecast", color = Color.White, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
            }
        }

        // KPI Cards
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
                FcKpiCard(
                    title = "Today Forecast",
                    value = if (fc.configured) String.format("%.1f kWh", fc.todayForecastKwh) else "—",
                    sub = if (fc.configured) String.format("Rem: %.1f kWh | Peak %.2f kW", fc.todayRemainingKwh, fc.todayPeakKw) else "Not configured",
                    accent = FcAmber,
                    modifier = Modifier.weight(1f),
                )
                FcKpiCard(
                    title = "Today Actual",
                    value = String.format("%.1f kWh", fc.todayActualKwh),
                    sub = if (fc.configured && fc.todayForecastKwh > 0) "${((fc.todayActualKwh / fc.todayForecastKwh) * 100.0).roundToInt()}% of forecast" else "Measured PV",
                    accent = FcGreen,
                    modifier = Modifier.weight(1f),
                )
            }
        }

        item {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
                FcKpiCard(
                    title = "Tomorrow Forecast",
                    value = fc.tomorrowForecastKwh?.let { String.format("%.1f kWh", it) } ?: "—",
                    sub = "Efficiency-calibrated",
                    accent = FcBlue,
                    modifier = Modifier.weight(1f),
                )
                FcKpiCard(
                    title = "Efficiency Factor",
                    value = String.format("%.1f%%", fc.efficiencyPct),
                    sub = fc.maeKw?.let { "${fc.confidence} | MAE ${String.format("%.2f", it)} kW" } ?: fc.confidence,
                    accent = FcPurple,
                    modifier = Modifier.weight(1f),
                )
            }
        }

        // Hourly Forecast vs Actual Chart
        item {
            SectionCard(
                title = "Hourly Forecast vs Actual Trend (kW)",
                subtitle = "Amber = Adjusted Forecast | Green = Actual PV | Dashed = Unadjusted",
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf("24h + 48h", "Today + 72h", "Full 9d").forEachIndexed { idx, lbl ->
                            FilterChip(
                                selected = windowMode == idx,
                                onClick = { windowMode = idx },
                                label = { Text(lbl, fontSize = 11.sp) },
                                colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = FcPrimary,
                                    selectedLabelColor = Color.White,
                                    labelColor = FcTextMuted,
                                ),
                            )
                        }
                    }

                    val nowHourIso = remember {
                        SimpleDateFormat("yyyy-MM-dd'T'HH:00:00", Locale.US).format(Date())
                    }
                    val todayStr = nowHourIso.substring(0, 10)
                    val filteredHours = remember(fc.hourly, windowMode) {
                        when (windowMode) {
                            0 -> {
                                val minIso = SimpleDateFormat("yyyy-MM-dd'T'HH:00:00", Locale.US)
                                    .format(Date(System.currentTimeMillis() - 24L * 3600L * 1000L))
                                val maxIso = SimpleDateFormat("yyyy-MM-dd'T'HH:00:00", Locale.US)
                                    .format(Date(System.currentTimeMillis() + 48L * 3600L * 1000L))
                                fc.hourly.filter { it.ts in minIso..maxIso }
                            }
                            1 -> {
                                val maxIso = SimpleDateFormat("yyyy-MM-dd'T'HH:00:00", Locale.US)
                                    .format(Date(System.currentTimeMillis() + 72L * 3600L * 1000L))
                                fc.hourly.filter { it.dateStr >= todayStr && it.ts <= maxIso }
                            }
                            else -> fc.hourly
                        }
                    }

                    HourlyForecastChart(points = filteredHours, nowHourIso = nowHourIso)
                }
            }
        }

        // Daily Forecast vs Actual Bar Chart
        item {
            SectionCard(
                title = "Daily Yield: Forecast vs Actual (kWh)",
                subtitle = "Amber = Adjusted Forecast | Green = Actual Solar PV",
            ) {
                DailyForecastBarChart(days = fc.daily)
            }
        }

        // Daily Ledger
        item {
            SectionCard(title = "Daily Forecast & Actuals Ledger") {
                if (fc.daily.isEmpty()) {
                    Text("No forecast data yet — configure parameters and sync Open-Meteo.", color = FcTextMuted, fontSize = 12.sp)
                } else {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        fc.daily.forEach { d ->
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(
                                        "${d.dateStr}${if (d.isToday) " (Today)" else ""}",
                                        color = if (d.isToday) FcBlue else FcTextMain,
                                        fontWeight = FontWeight.SemiBold,
                                        fontSize = 13.sp,
                                    )
                                    Text(
                                        "Peak ${String.format("%.2f", d.peakForecastKw)} kW · Cloud ${d.cloudCoverAvgPct.roundToInt()}%",
                                        color = FcTextMuted,
                                        fontSize = 11.sp,
                                    )
                                }
                                Column(horizontalAlignment = Alignment.End) {
                                    Text(
                                        "Fc: ${String.format("%.2f", d.forecastKwh)} kWh",
                                        color = FcAmber,
                                        fontWeight = FontWeight.SemiBold,
                                        fontSize = 13.sp,
                                    )
                                    Text(
                                        d.actualKwh?.let { "Act: ${String.format("%.2f", it)} kWh" } ?: "Act: —",
                                        color = FcGreen,
                                        fontSize = 11.sp,
                                    )
                                }
                            }
                            HorizontalDivider(color = FcBorderCard)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun FcKpiCard(
    title: String,
    value: String,
    sub: String,
    accent: Color,
    modifier: Modifier = Modifier,
) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = FcBgCard),
        border = BorderStroke(1.dp, FcBorderCard),
        shape = RoundedCornerShape(12.dp),
    ) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Box(modifier = Modifier.size(8.dp).background(accent, CircleShape))
                Text(title, color = FcTextMuted, fontSize = 11.sp)
            }
            Text(value, color = FcTextMain, fontWeight = FontWeight.Bold, fontSize = 18.sp)
            Text(sub, color = FcTextMuted, fontSize = 10.sp, maxLines = 1)
        }
    }
}

@Composable
private fun HourlyForecastChart(
    points: List<SolarForecastHour>,
    nowHourIso: String,
) {
    var selectedIdx by remember(points) { mutableStateOf<Int?>(null) }

    if (points.isEmpty()) {
        Box(modifier = Modifier.fillMaxWidth().height(160.dp), contentAlignment = Alignment.Center) {
            Text("No hourly forecast points yet.", color = FcTextMuted, fontSize = 12.sp)
        }
        return
    }

    val selPt = selectedIdx?.let { points.getOrNull(it) }
    if (selPt != null) {
        Text(
            "${selPt.dateStr} ${selPt.hourStr} · Fc: ${String.format("%.2f", selPt.forecastKw)} kW" +
                (selPt.actualKw?.let { " · Act: ${String.format("%.2f", it)} kW" } ?: "") +
                " · Cloud ${selPt.cloudCoverPct.roundToInt()}%",
            color = FcBlue,
            fontSize = 11.sp,
            fontWeight = FontWeight.SemiBold,
        )
    }

    Canvas(
        modifier = Modifier
            .fillMaxWidth()
            .height(200.dp)
            .background(Color(0xFF0B101D), RoundedCornerShape(8.dp))
            .pointerInput(points) {
                detectTapGestures { pos ->
                    val idx = ((pos.x / size.width.coerceAtLeast(1)) * (points.size - 1))
                        .roundToInt()
                        .coerceIn(0, points.lastIndex)
                    selectedIdx = idx
                }
            }
            .pointerInput(points) {
                detectHorizontalDragGestures { change, _ ->
                    val idx = ((change.position.x / size.width.coerceAtLeast(1)) * (points.size - 1))
                        .roundToInt()
                        .coerceIn(0, points.lastIndex)
                    selectedIdx = idx
                }
            }
    ) {
        val padL = 12f
        val padR = 12f
        val padT = 14f
        val padB = 14f
        val cw = size.width - padL - padR
        val ch = size.height - padT - padB

        val maxKw = max(
            1.0,
            points.maxOf { max(it.forecastKw, max(it.baselineKw, it.actualKw ?: 0.0)) },
        )
        fun xAt(i: Int) = padL + (if (points.size > 1) i.toFloat() / (points.size - 1) * cw else cw / 2f)
        fun yAt(kw: Double) = padT + ch - ((kw.coerceAtLeast(0.0) / maxKw).toFloat() * ch)

        // Grid lines
        for (g in 0..4) {
            val gy = padT + ch * g / 4f
            drawLine(Color(0x2294A3B8), Offset(padL, gy), Offset(padL + cw, gy), strokeWidth = 1f)
        }

        // NOW line
        val nowIdx = points.indexOfFirst { it.ts >= nowHourIso }
        if (nowIdx >= 0) {
            val nx = xAt(nowIdx)
            drawLine(
                color = FcBlue.copy(alpha = 0.7f),
                start = Offset(nx, padT),
                end = Offset(nx, padT + ch),
                strokeWidth = 2f,
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 8f)),
            )
        }

        // Unadjusted Baseline (dashed)
        val basePath = Path()
        points.forEachIndexed { i, p ->
            val pt = Offset(xAt(i), yAt(p.baselineKw))
            if (i == 0) basePath.moveTo(pt.x, pt.y) else basePath.lineTo(pt.x, pt.y)
        }
        drawPath(
            path = basePath,
            color = FcTextMuted.copy(alpha = 0.55f),
            style = Stroke(width = 2.5f, pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 6f))),
        )

        // Adjusted Forecast
        val fcPath = Path()
        points.forEachIndexed { i, p ->
            val pt = Offset(xAt(i), yAt(p.forecastKw))
            if (i == 0) fcPath.moveTo(pt.x, pt.y) else fcPath.lineTo(pt.x, pt.y)
        }
        drawPath(path = fcPath, color = FcAmber, style = Stroke(width = 4f, cap = StrokeCap.Round))

        // Actual Solar PV
        val actPath = Path()
        var started = false
        points.forEachIndexed { i, p ->
            val a = p.actualKw
            if (a == null) {
                started = false
            } else {
                val pt = Offset(xAt(i), yAt(a))
                if (!started) {
                    actPath.moveTo(pt.x, pt.y)
                    started = true
                } else {
                    actPath.lineTo(pt.x, pt.y)
                }
            }
        }
        drawPath(path = actPath, color = FcGreen, style = Stroke(width = 4.5f, cap = StrokeCap.Round))

        selectedIdx?.let { idx ->
            val sx = xAt(idx)
            drawLine(Color.White.copy(alpha = 0.5f), Offset(sx, padT), Offset(sx, padT + ch), strokeWidth = 2f)
        }
    }
}

@Composable
private fun DailyForecastBarChart(days: List<SolarForecastDay>) {
    if (days.isEmpty()) {
        Box(modifier = Modifier.fillMaxWidth().height(130.dp), contentAlignment = Alignment.Center) {
            Text("No daily forecast data yet.", color = FcTextMuted, fontSize = 12.sp)
        }
        return
    }
    Canvas(
        modifier = Modifier
            .fillMaxWidth()
            .height(160.dp)
            .background(Color(0xFF0B101D), RoundedCornerShape(8.dp))
    ) {
        val padL = 12f
        val padR = 12f
        val padT = 14f
        val padB = 14f
        val cw = size.width - padL - padR
        val ch = size.height - padT - padB
        val maxKwh = ceil(max(5.0, days.maxOf { max(it.forecastKwh, it.actualKwh ?: 0.0) }) / 5.0) * 5.0
        val groupW = cw / days.size
        val barW = (groupW * 0.36f).coerceIn(6f, 28f)

        days.forEachIndexed { i, d ->
            val gx = padL + i * groupW + groupW / 2f
            val hasAct = d.actualKwh != null
            val fcX = if (hasAct) gx - barW else gx - barW / 2f
            val fcH = ((d.forecastKwh / maxKwh) * ch).toFloat()
            drawRect(
                color = if (d.isToday) Color(0xFFFBBF24) else FcAmber,
                topLeft = Offset(fcX, padT + ch - fcH),
                size = Size(barW - 2f, fcH),
            )
            if (d.actualKwh != null) {
                val actH = ((d.actualKwh / maxKwh) * ch).toFloat()
                drawRect(
                    color = FcGreen,
                    topLeft = Offset(gx + 1f, padT + ch - actH),
                    size = Size(barW - 2f, actH),
                )
            }
        }
    }
}
