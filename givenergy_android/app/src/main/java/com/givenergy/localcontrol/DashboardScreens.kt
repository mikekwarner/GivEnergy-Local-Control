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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

private val BgCard = Color(0xFF111827)
private val BorderCard = Color(0xFF1F2937)
private val TextMain = Color(0xFFF1F5F9)
private val TextMuted = Color(0xFF94A3B8)
private val AccentSolar = Color(0xFFFACC15)
private val AccentBat = Color(0xFF34D399)
private val AccentLoad = Color(0xFFA78BFA)
private val AccentGridImport = Color(0xFFF87171)
private val AccentGridExport = Color(0xFF38BDF8)
private val AccentEvc = Color(0xFFF472B6)
private val AccentPrimary = Color(0xFF2563EB)

private fun fmtWatts(w: Double): String {
    val a = abs(w)
    return if (a >= 1000.0) String.format("%.2f kW", w / 1000.0) else "${w.roundToInt()} W"
}

@Composable
fun SectionCard(
    title: String,
    subtitle: String? = null,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = BgCard),
        border = BorderStroke(1.dp, BorderCard),
        shape = RoundedCornerShape(14.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text(title, color = TextMain, fontSize = 15.sp, fontWeight = FontWeight.Bold)
            if (!subtitle.isNullOrBlank()) {
                Text(subtitle, color = TextMuted, fontSize = 12.sp, modifier = Modifier.padding(top = 2.dp))
            }
            Spacer(Modifier.height(10.dp))
            content()
        }
    }
}

@Composable
fun LiveScreen(state: PlantUiState) {
    val inv = state.inverter
    val bat = state.battery
    val evc = state.evc

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item { Spacer(Modifier.height(2.dp)) }

        if (!state.errorBanner.isNullOrBlank()) {
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = Color(0xFF450A0A)),
                    border = BorderStroke(1.dp, AccentGridImport),
                    shape = RoundedCornerShape(10.dp),
                ) {
                    Text(
                        state.errorBanner,
                        color = Color(0xFFFECACA),
                        fontSize = 12.5.sp,
                        modifier = Modifier.padding(12.dp),
                    )
                }
            }
        }

        // 2x2 Power Flow Grid
        item {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                    PowerTile(
                        title = if (state.hasSolar) "SOLAR PV" else "INVERTER",
                        value = if (state.hasSolar) fmtWatts(inv.pPvTotal) else inv.operatingMode,
                        sub = if (state.hasSolar) "${String.format("%.1f", inv.ePvToday)} kWh today" else "${String.format("%.0f", inv.vGrid)}V • ${String.format("%.2f", inv.fGrid)}Hz",
                        accent = AccentSolar,
                        modifier = Modifier.weight(1f),
                    )
                    PowerTile(
                        title = "HOME LOAD",
                        value = fmtWatts(inv.pLoad),
                        sub = "${String.format("%.1f", inv.eLoadToday)} kWh today",
                        accent = AccentLoad,
                        modifier = Modifier.weight(1f),
                    )
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                    val batDir = when {
                        inv.pBattery > 30 -> "Discharging ${fmtWatts(inv.pBattery)}"
                        inv.pBattery < -30 -> "Charging ${fmtWatts(abs(inv.pBattery))}"
                        else -> "Idle"
                    }
                    PowerTile(
                        title = "BATTERY",
                        value = "${bat.soc.roundToInt()}%",
                        sub = batDir,
                        accent = AccentBat,
                        modifier = Modifier.weight(1f),
                    )
                    val gridImporting = inv.pGrid < -20
                    val gridExporting = inv.pGrid > 20
                    val gridLabel = when {
                        gridImporting -> "Importing ${fmtWatts(abs(inv.pGrid))}"
                        gridExporting -> "Exporting ${fmtWatts(inv.pGrid)}"
                        else -> "Balanced (0 W)"
                    }
                    PowerTile(
                        title = "GRID",
                        value = fmtWatts(abs(inv.pGrid)),
                        sub = gridLabel,
                        accent = if (gridExporting) AccentGridExport else AccentGridImport,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }

        // Battery SoC progress bar + mode summary
        item {
            SectionCard(
                title = "Operating Status",
                subtitle = "${inv.operatingMode} • Inverter ${if (inv.connected) "Online" else "Offline"}",
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Battery State of Charge", color = TextMuted, fontSize = 12.5.sp)
                    Text("${bat.soc.roundToInt()}% (Reserve ${inv.batterySocReserve}%)", color = AccentBat, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                }
                Spacer(Modifier.height(6.dp))
                LinearProgressIndicator(
                    progress = { (bat.soc / 100.0).toFloat().coerceIn(0f, 1f) },
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(8.dp),
                    color = AccentBat,
                    trackColor = BorderCard,
                )
            }
        }

        // EV Charger Summary Card (if enabled)
        if (evc.enabled) {
            item {
                SectionCard(
                    title = "🚗 EV Charger",
                    subtitle = "${evc.chargeStatus} • ${evc.chargeControlState}",
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Column {
                            Text("Power", color = TextMuted, fontSize = 11.sp)
                            Text(fmtWatts(evc.powerW), color = AccentEvc, fontSize = 17.sp, fontWeight = FontWeight.Bold)
                        }
                        Column {
                            Text("Session", color = TextMuted, fontSize = 11.sp)
                            Text("${String.format("%.1f", evc.sessionEnergyKwh)} kWh", color = TextMain, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            Text("Limit", color = TextMuted, fontSize = 11.sp)
                            Text("${evc.chargeLimitA.roundToInt()} A", color = TextMain, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
            }
        }

        // Live 15-Min Power Chart
        if (state.liveRing.size >= 2) {
            item {
                SectionCard(
                    title = "Live Power Trend (Last 15 min)",
                    subtitle = "Yellow: PV • Purple: Load • Green: Battery • Red: Grid",
                ) {
                    LiveSparklineChart(points = state.liveRing)
                }
            }
        }

        // Today's Energy Totals
        item {
            SectionCard(title = "Today's Energy Totals") {
                StatRow("Solar Generation", "${String.format("%.2f", inv.ePvToday)} kWh", AccentSolar)
                StatRow("Home Consumption", "${String.format("%.2f", inv.eLoadToday)} kWh", AccentLoad)
                StatRow("Grid Import", "${String.format("%.2f", inv.eGridImportToday)} kWh", AccentGridImport)
                StatRow("Grid Export", "${String.format("%.2f", inv.eGridExportToday)} kWh", AccentGridExport)
                StatRow("Battery Charged", "${String.format("%.2f", inv.eBatChargeToday)} kWh", AccentBat)
                StatRow("Battery Discharged", "${String.format("%.2f", inv.eBatDischargeToday)} kWh", AccentBat)
            }
        }

        item { Spacer(Modifier.height(8.dp)) }
    }
}

@Composable
private fun PowerTile(
    title: String,
    value: String,
    sub: String,
    accent: Color,
    modifier: Modifier = Modifier,
) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = BgCard),
        border = BorderStroke(1.dp, BorderCard),
        shape = RoundedCornerShape(14.dp),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(8.dp)
                        .background(accent, CircleShape)
                )
                Spacer(Modifier.size(6.dp))
                Text(title, color = TextMuted, fontSize = 10.5.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(6.dp))
            Text(value, color = TextMain, fontSize = 20.sp, fontWeight = FontWeight.Bold, maxLines = 1)
            Spacer(Modifier.height(2.dp))
            Text(sub, color = TextMuted, fontSize = 11.5.sp, maxLines = 1)
        }
    }
}

@Composable
private fun StatRow(label: String, value: String, valueColor: Color = TextMain) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, color = TextMuted, fontSize = 13.sp)
        Text(value, color = valueColor, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun LiveSparklineChart(points: List<LivePoint>) {
    Canvas(
        modifier = Modifier
            .fillMaxWidth()
            .height(150.dp)
    ) {
        val w = size.width
        val h = size.height
        var maxAbs = 500.0
        for (p in points) {
            maxAbs = max(maxAbs, max(abs(p.pvW), max(abs(p.loadW), max(abs(p.gridW), abs(p.batW)))))
        }
        val minVal = -maxAbs
        val range = max(1.0, maxAbs - minVal)

        fun yFor(v: Double): Float = (h - ((v - minVal) / range) * h).toFloat()

        // Zero baseline
        val y0 = yFor(0.0)
        drawLine(Color(0xFF334155), Offset(0f, y0), Offset(w, y0), strokeWidth = 1.5f)

        fun drawSeries(color: Color, selector: (LivePoint) -> Double) {
            val path = Path()
            points.forEachIndexed { idx, pt ->
                val x = if (points.size == 1) 0f else (idx.toFloat() / (points.size - 1)) * w
                val y = yFor(selector(pt))
                if (idx == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            drawPath(path, color = color, style = Stroke(width = 3f, cap = StrokeCap.Round))
        }

        drawSeries(AccentGridImport) { it.gridW }
        drawSeries(AccentBat) { it.batW }
        drawSeries(AccentLoad) { it.loadW }
        drawSeries(AccentSolar) { it.pvW }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ControlsScreen(
    state: PlantUiState,
    onControl: (target: String, action: String, params: Map<String, Any>, desc: String) -> Unit,
) {
    val inv = state.inverter
    val evc = state.evc

    var targetSoc by remember { mutableFloatStateOf(inv.chargeTargetSoc.toFloat()) }
    var reserveSoc by remember { mutableFloatStateOf(inv.batterySocReserve.toFloat()) }
    var evcAmps by remember { mutableFloatStateOf(evc.chargeLimitA.toFloat()) }

    LaunchedEffect(inv.chargeTargetSoc) { targetSoc = inv.chargeTargetSoc.toFloat() }
    LaunchedEffect(inv.batterySocReserve) { reserveSoc = inv.batterySocReserve.toFloat() }
    LaunchedEffect(evc.chargeLimitA) { evcAmps = evc.chargeLimitA.toFloat() }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item { Spacer(Modifier.height(2.dp)) }

        item {
            SectionCard(
                title = "⚡ Inverter Mode & Battery",
                subtitle = "Current: ${inv.operatingMode}",
            ) {
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    FilterChip(
                        selected = inv.ecoMode,
                        onClick = {
                            onControl("inverter", "set_mode_eco", mapOf("enabled" to !inv.ecoMode), "Toggle Eco Mode")
                        },
                        label = { Text("Eco Mode") },
                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = AccentPrimary),
                    )
                    FilterChip(
                        selected = false,
                        onClick = { onControl("inverter", "set_mode_timed_discharge", emptyMap(), "Set Timed Discharge") },
                        label = { Text("Timed Discharge") },
                    )
                    FilterChip(
                        selected = false,
                        onClick = { onControl("inverter", "set_mode_timed_export", emptyMap(), "Set Timed Export") },
                        label = { Text("Timed Export") },
                    )
                }

                HorizontalDivider(color = BorderCard, modifier = Modifier.padding(vertical = 10.dp))

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Enable AC Charge", color = TextMain, fontSize = 14.sp)
                    Switch(
                        checked = inv.enableCharge,
                        onCheckedChange = {
                            onControl("inverter", "set_enable_charge", mapOf("enabled" to it), "AC Charge ${if (it) "ON" else "OFF"}")
                        },
                    )
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Enable Discharge", color = TextMain, fontSize = 14.sp)
                    Switch(
                        checked = inv.enableDischarge,
                        onCheckedChange = {
                            onControl("inverter", "set_enable_discharge", mapOf("enabled" to it), "Discharge ${if (it) "ON" else "OFF"}")
                        },
                    )
                }

                HorizontalDivider(color = BorderCard, modifier = Modifier.padding(vertical = 10.dp))

                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("Charge Target SoC", color = TextMuted, fontSize = 13.sp)
                    Text("${targetSoc.roundToInt()}%", color = AccentBat, fontWeight = FontWeight.Bold)
                }
                Slider(
                    value = targetSoc,
                    onValueChange = { targetSoc = it },
                    valueRange = 4f..100f,
                    colors = SliderDefaults.colors(thumbColor = AccentBat, activeTrackColor = AccentBat),
                )
                OutlinedButton(
                    onClick = {
                        val v = targetSoc.roundToInt()
                        onControl("inverter", "set_charge_target_soc", mapOf("soc" to v), "Set Target SoC to $v%")
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("Apply Target SoC (${targetSoc.roundToInt()}%)")
                }

                Spacer(Modifier.height(10.dp))

                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("Battery Reserve SoC", color = TextMuted, fontSize = 13.sp)
                    Text("${reserveSoc.roundToInt()}%", color = AccentSolar, fontWeight = FontWeight.Bold)
                }
                Slider(
                    value = reserveSoc,
                    onValueChange = { reserveSoc = it },
                    valueRange = 4f..100f,
                    colors = SliderDefaults.colors(thumbColor = AccentSolar, activeTrackColor = AccentSolar),
                )
                OutlinedButton(
                    onClick = {
                        val v = reserveSoc.roundToInt()
                        onControl("inverter", "set_battery_soc_reserve", mapOf("soc" to v), "Set Reserve SoC to $v%")
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("Apply Reserve SoC (${reserveSoc.roundToInt()}%)")
                }
            }
        }

        if (evc.enabled) {
            item {
                SectionCard(
                    title = "🚗 EV Charger Controls",
                    subtitle = "${evc.chargeStatus} • ${evc.chargeControlState}",
                ) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        Button(
                            onClick = { onControl("evc", "charge_control", mapOf("action" to "start"), "Start EV Charge") },
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF059669)),
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("Start Charge")
                        }
                        Button(
                            onClick = { onControl("evc", "charge_control", mapOf("action" to "stop"), "Stop EV Charge") },
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFDC2626)),
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("Stop Charge")
                        }
                    }

                    Spacer(Modifier.height(10.dp))

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Plug & Go Mode", color = TextMain, fontSize = 14.sp)
                        Switch(
                            checked = evc.plugAndGo,
                            onCheckedChange = {
                                onControl("evc", "plug_and_go", mapOf("enabled" to it), "Plug & Go ${if (it) "ON" else "OFF"}")
                            },
                        )
                    }

                    Spacer(Modifier.height(8.dp))

                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text("Charge Current Limit", color = TextMuted, fontSize = 13.sp)
                        Text("${evcAmps.roundToInt()} A", color = AccentEvc, fontWeight = FontWeight.Bold)
                    }
                    Slider(
                        value = evcAmps.coerceIn(evc.minCurrentA.toFloat(), evc.maxCurrentA.toFloat()),
                        onValueChange = { evcAmps = it },
                        valueRange = evc.minCurrentA.toFloat()..evc.maxCurrentA.toFloat(),
                        colors = SliderDefaults.colors(thumbColor = AccentEvc, activeTrackColor = AccentEvc),
                    )
                    OutlinedButton(
                        onClick = {
                            val amps = evcAmps.roundToInt()
                            onControl("evc", "current_limit", mapOf("amps" to amps), "Set EV Limit to ${amps}A")
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Apply Current Limit (${evcAmps.roundToInt()} A)")
                    }
                }
            }
        }

        item { Spacer(Modifier.height(8.dp)) }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun HistoryScreen(
    state: PlantUiState,
    onSelectRange: (Int) -> Unit,
    onReloadHistory: () -> Unit,
) {
    var showSolar by remember { mutableStateOf(true) }
    var showLoad by remember { mutableStateOf(true) }
    var showBat by remember { mutableStateOf(true) }
    var showGrid by remember { mutableStateOf(true) }
    var showSoc by remember { mutableStateOf(true) }
    var selectedIdx by remember { mutableStateOf<Int?>(null) }

    val series = state.historySeries
    LaunchedEffect(series.size, state.historyRangeDays) {
        selectedIdx = null
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item { Spacer(Modifier.height(2.dp)) }

        item {
            SectionCard(
                title = "📈 Historical Time Series",
                subtitle = when (state.historyRangeDays) {
                    1 -> "Today (1-min resolution, ${series.size} pts)"
                    else -> "Last ${state.historyRangeDays} Days (${series.size} pts)"
                },
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    listOf(1 to "Today", 7 to "7 Days", 30 to "30 Days").forEach { (days, label) ->
                        FilterChip(
                            selected = state.historyRangeDays == days,
                            onClick = { onSelectRange(days) },
                            label = { Text(label, fontSize = 12.sp) },
                            colors = FilterChipDefaults.filterChipColors(selectedContainerColor = AccentPrimary),
                        )
                    }
                    Spacer(Modifier.weight(1f))
                    OutlinedButton(onClick = onReloadHistory) { Text("↻", fontSize = 14.sp) }
                }

                Spacer(Modifier.height(4.dp))

                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    FilterChip(
                        selected = showSolar,
                        onClick = { showSolar = !showSolar },
                        label = { Text("Solar", fontSize = 11.sp, color = if (showSolar) AccentSolar else TextMuted) },
                    )
                    FilterChip(
                        selected = showLoad,
                        onClick = { showLoad = !showLoad },
                        label = { Text("Load", fontSize = 11.sp, color = if (showLoad) AccentLoad else TextMuted) },
                    )
                    FilterChip(
                        selected = showBat,
                        onClick = { showBat = !showBat },
                        label = { Text("Battery", fontSize = 11.sp, color = if (showBat) AccentBat else TextMuted) },
                    )
                    FilterChip(
                        selected = showGrid,
                        onClick = { showGrid = !showGrid },
                        label = { Text("Grid", fontSize = 11.sp, color = if (showGrid) AccentGridImport else TextMuted) },
                    )
                    FilterChip(
                        selected = showSoc,
                        onClick = { showSoc = !showSoc },
                        label = { Text("SoC %", fontSize = 11.sp, color = if (showSoc) AccentGridExport else TextMuted) },
                    )
                }

                Spacer(Modifier.height(8.dp))

                if (series.size < 2) {
                    Text(
                        "Loading historical series…",
                        color = TextMuted,
                        fontSize = 13.sp,
                        modifier = Modifier.padding(vertical = 24.dp),
                    )
                } else {
                    HistoricalTimeSeriesChart(
                        points = series,
                        showSolar = showSolar,
                        showLoad = showLoad,
                        showBat = showBat,
                        showGrid = showGrid,
                        showSoc = showSoc,
                        selectedIndex = selectedIdx,
                        onSelectIndex = { selectedIdx = it },
                    )

                    val activePt = selectedIdx?.let { series.getOrNull(it) } ?: series.lastOrNull()
                    if (activePt != null) {
                        Spacer(Modifier.height(8.dp))
                        val prettyTs = activePt.ts.replace("T", " ").take(16)
                        Card(
                            colors = CardDefaults.cardColors(containerColor = Color(0xFF090D16)),
                            border = BorderStroke(1.dp, BorderCard),
                            shape = RoundedCornerShape(8.dp),
                        ) {
                            Column(modifier = Modifier.padding(10.dp)) {
                                Text(
                                    if (selectedIdx != null) "Selected: $prettyTs" else "Latest: $prettyTs (drag chart to inspect)",
                                    color = TextMuted,
                                    fontSize = 11.sp,
                                )
                                Spacer(Modifier.height(4.dp))
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                ) {
                                    Text("PV: ${fmtWatts(activePt.pvW)}", color = AccentSolar, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                                    Text("Load: ${fmtWatts(activePt.loadW)}", color = AccentLoad, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                                    Text("Bat: ${fmtWatts(activePt.batW)}", color = AccentBat, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                                }
                                Spacer(Modifier.height(2.dp))
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                ) {
                                    Text("Grid: ${fmtWatts(activePt.gridW)}", color = AccentGridImport, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                                    Text("SoC: ${activePt.soc.roundToInt()}%", color = AccentGridExport, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                                }
                            }
                        }
                    }
                }
            }
        }

        item {
            Text(
                "Daily Energy Ledger",
                color = TextMain,
                fontSize = 15.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(top = 4.dp),
            )
        }

        if (state.dailyHistory.isEmpty()) {
            item {
                SectionCard(title = "No Daily Records Yet") {
                    Text("Historic daily summaries will appear here once recorded by the Pi.", color = TextMuted, fontSize = 13.sp)
                }
            }
        } else {
            items(state.dailyHistory) { day ->
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = BgCard),
                    border = BorderStroke(1.dp, BorderCard),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text(day.date, color = TextMain, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(6.dp))
                        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("PV: ${String.format("%.1f", day.pvKwh)} kWh", color = AccentSolar, fontSize = 12.5.sp)
                            Text("Load: ${String.format("%.1f", day.loadKwh)} kWh", color = AccentLoad, fontSize = 12.5.sp)
                        }
                        Spacer(Modifier.height(3.dp))
                        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("Import: ${String.format("%.1f", day.gridImportKwh)} kWh", color = AccentGridImport, fontSize = 12.5.sp)
                            Text("Export: ${String.format("%.1f", day.gridExportKwh)} kWh", color = AccentGridExport, fontSize = 12.5.sp)
                        }
                    }
                }
            }
        }

        item { Spacer(Modifier.height(8.dp)) }
    }
}

@Composable
private fun HistoricalTimeSeriesChart(
    points: List<LivePoint>,
    showSolar: Boolean,
    showLoad: Boolean,
    showBat: Boolean,
    showGrid: Boolean,
    showSoc: Boolean,
    selectedIndex: Int?,
    onSelectIndex: (Int) -> Unit,
) {
    var maxAbs = 500.0
    for (p in points) {
        if (showSolar) maxAbs = max(maxAbs, abs(p.pvW))
        if (showLoad) maxAbs = max(maxAbs, abs(p.loadW))
        if (showBat) maxAbs = max(maxAbs, abs(p.batW))
        if (showGrid) maxAbs = max(maxAbs, abs(p.gridW))
    }

    Column {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text("+${fmtWatts(maxAbs)}", color = TextMuted, fontSize = 10.sp)
            if (showSoc) Text("SoC 100%", color = AccentGridExport, fontSize = 10.sp)
        }

        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(210.dp)
                .pointerInput(points.size) {
                    detectTapGestures { offset ->
                        val idx = ((offset.x / size.width) * (points.size - 1))
                            .roundToInt()
                            .coerceIn(0, points.size - 1)
                        onSelectIndex(idx)
                    }
                }
                .pointerInput(points.size) {
                    detectHorizontalDragGestures { change, _ ->
                        change.consume()
                        val idx = ((change.position.x / size.width) * (points.size - 1))
                            .roundToInt()
                            .coerceIn(0, points.size - 1)
                        onSelectIndex(idx)
                    }
                }
        ) {
            val w = size.width
            val h = size.height
            val minVal = -maxAbs
            val range = max(1.0, maxAbs - minVal)

            fun yFor(v: Double): Float = (h - ((v - minVal) / range) * h).toFloat()

            // Horizontal reference lines (+50%, 0, -50%)
            listOf(maxAbs * 0.5, 0.0, -maxAbs * 0.5).forEach { ref ->
                val y = yFor(ref)
                drawLine(
                    color = if (ref == 0.0) Color(0xFF475569) else Color(0xFF1E293B),
                    start = Offset(0f, y),
                    end = Offset(w, y),
                    strokeWidth = if (ref == 0.0) 2f else 1f,
                )
            }

            // Downsample for smooth fast drawing when > 300 points
            val step = max(1, points.size / 300)

            fun drawSeries(color: Color, strokeWidth: Float = 2.5f, dashed: Boolean = false, selector: (LivePoint) -> Double) {
                val path = Path()
                var started = false
                var i = 0
                while (i < points.size) {
                    val x = if (points.size == 1) 0f else (i.toFloat() / (points.size - 1)) * w
                    val y = yFor(selector(points[i]))
                    if (!started) {
                        path.moveTo(x, y)
                        started = true
                    } else {
                        path.lineTo(x, y)
                    }
                    i += step
                }
                if (points.size > 1 && (points.size - 1) % step != 0) {
                    path.lineTo(w, yFor(selector(points.last())))
                }
                drawPath(
                    path = path,
                    color = color,
                    style = Stroke(
                        width = strokeWidth,
                        cap = StrokeCap.Round,
                        pathEffect = if (dashed) PathEffect.dashPathEffect(floatArrayOf(10f, 8f), 0f) else null,
                    ),
                )
            }

            if (showGrid) drawSeries(AccentGridImport) { it.gridW }
            if (showBat) drawSeries(AccentBat) { it.batW }
            if (showLoad) drawSeries(AccentLoad) { it.loadW }
            if (showSolar) drawSeries(AccentSolar) { it.pvW }
            if (showSoc) {
                val socPath = Path()
                var started = false
                var i = 0
                while (i < points.size) {
                    val x = if (points.size == 1) 0f else (i.toFloat() / (points.size - 1)) * w
                    val y = (h - (points[i].soc.coerceIn(0.0, 100.0) / 100.0) * h).toFloat()
                    if (!started) {
                        socPath.moveTo(x, y)
                        started = true
                    } else {
                        socPath.lineTo(x, y)
                    }
                    i += step
                }
                drawPath(
                    path = socPath,
                    color = AccentGridExport,
                    style = Stroke(
                        width = 2f,
                        pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 8f), 0f),
                    ),
                )
            }

            if (selectedIndex != null && selectedIndex in points.indices && points.size > 1) {
                val x = (selectedIndex.toFloat() / (points.size - 1)) * w
                drawLine(Color(0xFF94A3B8), Offset(x, 0f), Offset(x, h), strokeWidth = 2f)
            }
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            val firstLabel = points.firstOrNull()?.ts?.let { if (stateLabelIsDate(points)) it.take(10) else it.substringAfter("T").take(5) } ?: ""
            val midLabel = points.getOrNull(points.size / 2)?.ts?.let { if (stateLabelIsDate(points)) it.take(10) else it.substringAfter("T").take(5) } ?: ""
            val lastLabel = points.lastOrNull()?.ts?.let { if (stateLabelIsDate(points)) it.take(10) else it.substringAfter("T").take(5) } ?: ""
            Text(firstLabel, color = TextMuted, fontSize = 10.sp)
            Text(midLabel, color = TextMuted, fontSize = 10.sp)
            Text(lastLabel, color = TextMuted, fontSize = 10.sp)
        }
    }
}

private fun stateLabelIsDate(points: List<LivePoint>): Boolean {
    val first = points.firstOrNull()?.ts?.take(10) ?: return false
    val last = points.lastOrNull()?.ts?.take(10) ?: return false
    return first != last
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SystemScreen(
    state: PlantUiState,
    onReconnectDevices: () -> Unit,
    onOpenWebFallback: () -> Unit,
    onOpenPairing: () -> Unit,
) {
    val bat = state.battery
    val sys = state.system

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item { Spacer(Modifier.height(2.dp)) }

        item {
            SectionCard(title = "🔋 Battery & BMS") {
                StatRow("State of Charge", "${bat.soc.roundToInt()}%", AccentBat)
                StatRow("Pack Voltage / Current", "${String.format("%.2f", bat.voltage)} V  •  ${String.format("%.1f", bat.current)} A")
                StatRow("BMS Temperature", "${String.format("%.1f", bat.tempC)} °C")
                if (bat.cellVoltages.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    Text("Cell Voltages", color = TextMuted, fontSize = 12.sp)
                    Spacer(Modifier.height(4.dp))
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        bat.cellVoltages.forEachIndexed { idx, v ->
                            Card(
                                colors = CardDefaults.cardColors(containerColor = Color(0xFF090D16)),
                                border = BorderStroke(1.dp, BorderCard),
                            ) {
                                Text(
                                    "C${idx + 1}: ${String.format("%.3f", v)}V",
                                    color = AccentBat,
                                    fontSize = 11.sp,
                                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                                )
                            }
                        }
                    }
                }
            }
        }

        item {
            SectionCard(title = "🍓 Raspberry Pi Health") {
                StatRow("Hostname", sys.hostname.ifBlank { "—" })
                StatRow("Hardware", sys.hardwareModel.ifBlank { "Raspberry Pi" })
                StatRow("App Version", if (sys.appVersion.isNotBlank()) "v${sys.appVersion}" else "—")
                StatRow("CPU Temperature", sys.cpuTempC?.let { "$it °C" } ?: "—")
                StatRow("RAM / Disk Usage", "${sys.memPct.roundToInt()}% RAM  •  ${sys.diskPct.roundToInt()}% Disk")
                StatRow("Service Uptime", sys.uptimeStr.ifBlank { "—" })
                Spacer(Modifier.height(10.dp))
                OutlinedButton(onClick = onReconnectDevices, modifier = Modifier.fillMaxWidth()) {
                    Text("Reconnect Inverter / EV Charger")
                }
            }
        }

        item {
            SectionCard(title = "🔗 App Connection") {
                StatRow("Active Route", state.connectionMode, if (state.connectionMode == "LAN") AccentBat else AccentGridExport)
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                    OutlinedButton(onClick = onOpenPairing, modifier = Modifier.weight(1f)) {
                        Text("Pairing / QR")
                    }
                    OutlinedButton(onClick = onOpenWebFallback, modifier = Modifier.weight(1f)) {
                        Text("Web Dashboard")
                    }
                }
            }
        }

        item { Spacer(Modifier.height(8.dp)) }
    }
}

@Composable
fun CostsScreen(
    state: PlantUiState,
    onRefreshCosts: () -> Unit,
    onSelectCostPeriod: (String) -> Unit,
    onOpenTariffWebSetup: () -> Unit,
) {
    val cs = state.costSummary
    val sym = cs.currencySymbol
    val sub = cs.subUnitSymbol

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item { Spacer(Modifier.height(4.dp)) }

        item {
            SectionCard(title = "💷 Today's Estimated Cost & Earnings") {
                val netPrefix = if (cs.todayNetCostGbp < 0) "-" else ""
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    PowerTile(
                        title = "Today Net",
                        value = "$netPrefix$sym${String.format("%.2f", kotlin.math.abs(cs.todayNetCostGbp))}",
                        sub = "Standing: $sym${String.format("%.2f", cs.todayStandingChargeGbp)}",
                        accent = AccentGridExport,
                        modifier = Modifier.weight(1f),
                    )
                    PowerTile(
                        title = "Self-Use Saved",
                        value = "$sym${String.format("%.2f", cs.todaySavingsGbp)}",
                        sub = "30d: $sym${String.format("%.2f", cs.thirtyDaySavingsGbp)}",
                        accent = AccentSolar,
                        modifier = Modifier.weight(1f),
                    )
                }
                Spacer(Modifier.height(8.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    PowerTile(
                        title = "Import Outgoing",
                        value = "$sym${String.format("%.2f", cs.todayImportCostGbp)}",
                        sub = "Now: ${String.format("%.1f", cs.importRateP)}$sub/kWh",
                        accent = AccentGridImport,
                        modifier = Modifier.weight(1f),
                    )
                    PowerTile(
                        title = "Export Income",
                        value = "$sym${String.format("%.2f", cs.todayExportIncomeGbp)}",
                        sub = "Now: ${String.format("%.1f", cs.exportRateP)}$sub/kWh",
                        accent = AccentBat,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }

        item {
            SectionCard(title = "⚡ Active UK Tariff Plan (Region ${cs.region})") {
                StatRow("Import Plan", cs.importLabel, AccentGridImport)
                StatRow("Export Plan", cs.exportLabel, AccentBat)
                StatRow("Standing Charge", "${String.format("%.2f", cs.standingChargeP)}$sub/day")
                StatRow(
                    "30-Day Net / Export",
                    "$sym${String.format("%.2f", cs.thirtyDayNetCostGbp)} net  •  $sym${String.format("%.2f", cs.thirtyDayExportIncomeGbp)} earned",
                )
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                    OutlinedButton(onClick = onRefreshCosts, modifier = Modifier.weight(1f)) {
                        Text("⟳ Refresh")
                    }
                    Button(onClick = onOpenTariffWebSetup, modifier = Modifier.weight(1f)) {
                        Text("Configure Plan")
                    }
                }
            }
        }

        item {
            val periodTitles = mapOf(
                "days" to "📅 Daily Cost & Export Ledger (30d)",
                "weeks" to "📅 Weekly Cost & Export Ledger",
                "months" to "📅 Monthly Cost & Export Ledger",
                "years" to "📅 Annual Cost & Export Ledger",
                "all" to "📅 All-Time Cost & Export Summary",
            )
            SectionCard(title = periodTitles[cs.selectedPeriod] ?: "📅 Cost & Export Ledger") {
                val periods = listOf(
                    "days" to "Days",
                    "weeks" to "Weeks",
                    "months" to "Months",
                    "years" to "Years",
                    "all" to "All",
                )
                Row(
                    modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    periods.forEach { (key, lbl) ->
                        val active = cs.selectedPeriod == key
                        FilterChip(
                            selected = active,
                            onClick = { onSelectCostPeriod(key) },
                            label = { Text(lbl, fontSize = 11.sp) },
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
                if (cs.periodDaysCount > 0) {
                    val pNetPrefix = if (cs.thirtyDayNetCostGbp < 0) "-" else ""
                    val pAvgPrefix = if (cs.periodAvgDailyNetGbp < 0) "-" else ""
                    Text(
                        "Totals (${cs.periodDaysCount}d):  In ${String.format("%.1f", cs.periodImportKwh)}kWh (${sym}${String.format("%.2f", cs.periodImportCostGbp)})  •  Out ${String.format("%.1f", cs.periodExportKwh)}kWh (${sym}${String.format("%.2f", cs.thirtyDayExportIncomeGbp)})",
                        color = TextMain,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Medium,
                    )
                    Text(
                        "Standing ${sym}${String.format("%.2f", cs.periodStandingChargeGbp)}  •  Net $pNetPrefix$sym${String.format("%.2f", kotlin.math.abs(cs.thirtyDayNetCostGbp))}  •  Avg $pAvgPrefix$sym${String.format("%.2f", kotlin.math.abs(cs.periodAvgDailyNetGbp))}/d  •  Saved $sym${String.format("%.2f", cs.thirtyDaySavingsGbp)}",
                        color = TextMuted,
                        fontSize = 11.sp,
                    )
                    val capSign = if (cs.periodSavingsVsCapGbp >= 0) "+" else "-"
                    Text(
                        "vs Ofgem Price Cap: $capSign$sym${String.format("%.2f", kotlin.math.abs(cs.periodSavingsVsCapGbp))} saved (cap $sym${String.format("%.2f", cs.periodPriceCapCostGbp)})",
                        color = if (cs.periodSavingsVsCapGbp >= 0) AccentBat else AccentLoad,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                }
                if (cs.dailyCosts.isEmpty()) {
                    Text("No cost data yet.", color = TextMuted, fontSize = 13.sp)
                } else {
                    val runningTotals = DoubleArray(cs.dailyCosts.size)
                    var cum = 0.0
                    for (i in cs.dailyCosts.indices.reversed()) {
                        cum += cs.dailyCosts[i].netCostGbp
                        runningTotals[i] = cum
                    }
                    cs.dailyCosts.take(24).forEachIndexed { idx, d ->
                        if (idx > 0) HorizontalDivider(color = BorderCard, modifier = Modifier.padding(vertical = 6.dp))
                        val dNetPrefix = if (d.netCostGbp < 0) "-" else ""
                        val dCum = runningTotals[idx]
                        val dCumPrefix = if (dCum < 0) "-" else ""
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                val daysTag = if (d.daysCount > 1) " (${d.daysCount}d)" else ""
                                Text("${d.date}$daysTag", color = TextMain, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                                Text(
                                    "In ${String.format("%.1f", d.importKwh)}kWh (${sym}${String.format("%.2f", d.importCostGbp)})  •  Out ${String.format("%.1f", d.exportKwh)}kWh (${sym}${String.format("%.2f", d.exportIncomeGbp)})  •  SC ${sym}${String.format("%.2f", d.standingChargeGbp)}",
                                    color = TextMuted,
                                    fontSize = 11.sp,
                                )
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text(
                                    "Net $dNetPrefix$sym${String.format("%.2f", kotlin.math.abs(d.netCostGbp))}",
                                    color = if (d.netCostGbp <= 0) AccentBat else TextMain,
                                    fontSize = 13.sp,
                                    fontWeight = FontWeight.Bold,
                                )
                                Text(
                                    "Solar/Batt $sym${String.format("%.2f", d.savingsGbp)}  •  Total $dCumPrefix$sym${String.format("%.2f", kotlin.math.abs(dCum))}",
                                    color = AccentSolar,
                                    fontSize = 11.sp,
                                )
                            }
                        }
                    }
                }
            }
        }

        item { Spacer(Modifier.height(8.dp)) }
    }
}
