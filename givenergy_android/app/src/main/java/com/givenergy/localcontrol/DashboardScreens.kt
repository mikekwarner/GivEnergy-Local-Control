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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
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
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import java.io.File
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
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

        // Live Power Flow Schematic (replaces 3x2 tile grid with animated Web-UI-style flow)
        item {
            SectionCard(
                title = "Real-Time Energy Flow",
                subtitle = "Animated directional power routing",
            ) {
                LivePowerFlowSchematic(state = state)
            }
        }

        // Live 2-Hour Power & SoC Timeline Chart
        if (state.liveRing.size >= 2) {
            item {
                SectionCard(
                    title = "Real-Time Power & SoC Timeline",
                    subtitle = "Last 2 hours • Tap or drag to inspect",
                ) {
                    LiveTimelineChart(
                        points = state.liveRing,
                        hasSolar = state.hasSolar,
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

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun EvcModeChipRow(
    selectedMode: String,
    onSelectMode: (String) -> Unit,
) {
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        listOf("Solar", "Hybrid", "Grid").forEach { mode ->
            FilterChip(
                selected = selectedMode.equals(mode, ignoreCase = true),
                onClick = { onSelectMode(mode) },
                label = { Text(mode) },
                colors = FilterChipDefaults.filterChipColors(selectedContainerColor = AccentPrimary),
            )
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ControlsScreen(
    state: PlantUiState,
    onControl: (target: String, action: String, params: Map<String, Any>, desc: String) -> Unit,
) {
    val inv = state.inverter

    var targetSoc by remember { mutableFloatStateOf(inv.chargeTargetSoc.toFloat()) }
    var reserveSoc by remember { mutableFloatStateOf(inv.batterySocReserve.toFloat()) }

    LaunchedEffect(inv.chargeTargetSoc) { targetSoc = inv.chargeTargetSoc.toFloat() }
    LaunchedEffect(inv.batterySocReserve) { reserveSoc = inv.batterySocReserve.toFloat() }

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
                val isEcoMode = inv.connected && inv.operatingMode.startsWith("Eco")
                val isTimedDisMode = inv.connected && inv.operatingMode.startsWith("Timed Discharge")
                val isTimedExpMode = inv.connected && inv.operatingMode.startsWith("Timed Export")
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    FilterChip(
                        selected = isEcoMode,
                        onClick = {
                            onControl("inverter", "set_mode_eco", mapOf("enabled" to !inv.ecoMode), "Toggle Eco Mode")
                        },
                        label = { Text("Eco Mode") },
                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = AccentBat),
                    )
                    FilterChip(
                        selected = isTimedDisMode,
                        onClick = { onControl("inverter", "set_mode_timed_discharge", emptyMap(), "Set Timed Discharge") },
                        label = { Text("Timed Discharge") },
                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = AccentPrimary),
                    )
                    FilterChip(
                        selected = isTimedExpMode,
                        onClick = { onControl("inverter", "set_mode_timed_export", emptyMap(), "Set Timed Export") },
                        label = { Text("Timed Export") },
                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = AccentSolar),
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

        item { Spacer(Modifier.height(8.dp)) }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun EvChargerScreen(
    state: PlantUiState,
    onControl: (target: String, action: String, params: Map<String, Any>, desc: String) -> Unit,
) {
    val evc = state.evc

    var maxBatDischargeW by remember { mutableFloatStateOf(evc.maxBatteryDischargeW.toFloat()) }
    var solarExportSplitPct by remember { mutableFloatStateOf(evc.solarExportSplitPct.toFloat()) }
    var evcAmps by remember { mutableFloatStateOf(evc.chargeLimitA.toFloat()) }

    val isDynamicSolar = evc.chargingMode.equals("Solar", ignoreCase = true) ||
        evc.chargingMode.equals("Hybrid", ignoreCase = true)

    LaunchedEffect(evc.maxBatteryDischargeW) { maxBatDischargeW = evc.maxBatteryDischargeW.toFloat() }
    LaunchedEffect(evc.solarExportSplitPct) { solarExportSplitPct = evc.solarExportSplitPct.toFloat() }
    LaunchedEffect(evc.chargeLimitA, isDynamicSolar) {
        if (!isDynamicSolar) {
            evcAmps = evc.chargeLimitA.toFloat()
        }
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item { Spacer(Modifier.height(2.dp)) }

        if (!evc.enabled) {
            item {
                SectionCard(title = "🚗 EV Charger Disabled") {
                    Text(
                        "EV Charger monitoring is currently disabled in Property Setup.",
                        color = TextMuted,
                        fontSize = 13.sp,
                    )
                }
            }
        } else {
            item {
                SectionCard(
                    title = "🚗 EV Charger Controls",
                    subtitle = "${evc.chargeStatus} • ${evc.chargeControlState}",
                ) {
                    val evcCharging = (
                        evc.chargeStatus == "Charging"
                            || evc.chargeStatus == "Starting"
                            || evc.chargeControlState == "Start"
                            || evc.powerW > 150.0
                    )
                    val canStartEvc = evc.connected && evc.cableConnected && !evcCharging
                    val canStopEvc = evc.connected && evcCharging
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        Button(
                            onClick = { onControl("evc", "charge_control", mapOf("action" to "start"), "Start EV Charge") },
                            enabled = canStartEvc,
                            colors = ButtonDefaults.buttonColors(
                                containerColor = Color(0xFF059669),
                                contentColor = Color.White,
                                disabledContainerColor = Color(0xFF1E293B),
                                disabledContentColor = Color(0xFF64748B),
                            ),
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("Start Charge")
                        }
                        Button(
                            onClick = { onControl("evc", "charge_control", mapOf("action" to "stop"), "Stop EV Charge") },
                            enabled = canStopEvc,
                            colors = ButtonDefaults.buttonColors(
                                containerColor = Color(0xFFDC2626),
                                contentColor = Color.White,
                                disabledContainerColor = Color(0xFF1E293B),
                                disabledContentColor = Color(0xFF64748B),
                            ),
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("Stop Charge")
                        }
                    }

                    Spacer(Modifier.height(10.dp))

                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text("Charge Current Limit", color = TextMuted, fontSize = 13.sp)
                        Text("${evcAmps.roundToInt()} A", color = AccentEvc, fontWeight = FontWeight.Bold)
                    }
                    Slider(
                        value = evcAmps.coerceIn(evc.minCurrentA.toFloat(), evc.maxCurrentA.toFloat()),
                        onValueChange = { evcAmps = it },
                        enabled = !isDynamicSolar,
                        valueRange = evc.minCurrentA.toFloat()..evc.maxCurrentA.toFloat(),
                        colors = SliderDefaults.colors(thumbColor = AccentEvc, activeTrackColor = AccentEvc),
                    )
                    OutlinedButton(
                        onClick = {
                            val amps = evcAmps.roundToInt()
                            onControl("evc", "current_limit", mapOf("amps" to amps), "Set EV Limit to ${amps}A")
                        },
                        enabled = !isDynamicSolar,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(
                            if (isDynamicSolar) "Current Limit Managed by ${evc.chargingMode} Mode"
                            else "Apply Current Limit (${evcAmps.roundToInt()} A)"
                        )
                    }
                }
            }

            item {
                SectionCard(
                    title = "🚗 EV Charger Status",
                    subtitle = "${evc.chargeStatus} • ${evc.chargeControlState} • Mode: ${evc.chargingMode}",
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
                    Spacer(Modifier.height(6.dp))
                    StatRow("Total Meter Energy", "${String.format("%.1f", evc.totalMeterKwh)} kWh")
                }
            }

            item {
                SectionCard(
                    title = "⚡ Mode & Solar / Battery Settings",
                    subtitle = "Mode: ${evc.chargingMode}",
                ) {
                    Text("Mode: Solar, Hybrid or Grid", color = TextMain, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(6.dp))
                    EvcModeChipRow(selectedMode = evc.chargingMode) { mode ->
                        onControl("evc", "set_charging_mode", mapOf("mode" to mode), "Set EV Mode to $mode")
                    }

                    HorizontalDivider(color = BorderCard, modifier = Modifier.padding(vertical = 10.dp))

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

                    HorizontalDivider(color = BorderCard, modifier = Modifier.padding(vertical = 10.dp))

                    val roundedBatW = ((maxBatDischargeW / 100f).roundToInt() * 100).coerceIn(0, 5000)
                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text("Max Battery Discharge Power to EV", color = TextMuted, fontSize = 13.sp)
                        Text("$roundedBatW W", color = AccentBat, fontWeight = FontWeight.Bold)
                    }
                    Slider(
                        value = maxBatDischargeW.coerceIn(0f, 5000f),
                        onValueChange = { maxBatDischargeW = it },
                        valueRange = 0f..5000f,
                        colors = SliderDefaults.colors(thumbColor = AccentBat, activeTrackColor = AccentBat),
                    )
                    OutlinedButton(
                        onClick = {
                            onControl("evc", "set_max_battery_discharge", mapOf("power_w" to roundedBatW), "Set Max Battery Discharge Power to EV to $roundedBatW W")
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Apply Max Discharge Power ($roundedBatW W)")
                    }

                    Spacer(Modifier.height(10.dp))

                    val roundedSplit = ((solarExportSplitPct / 5f).roundToInt() * 5).coerceIn(0, 100)
                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text("Solar Export Split %", color = TextMuted, fontSize = 13.sp)
                        Text("$roundedSplit% Export / ${100 - roundedSplit}% EV", color = AccentSolar, fontWeight = FontWeight.Bold)
                    }
                    Slider(
                        value = solarExportSplitPct.coerceIn(0f, 100f),
                        onValueChange = { solarExportSplitPct = it },
                        valueRange = 0f..100f,
                        colors = SliderDefaults.colors(thumbColor = AccentSolar, activeTrackColor = AccentSolar),
                    )
                    OutlinedButton(
                        onClick = {
                            onControl("evc", "set_solar_export_split", mapOf("split_pct" to roundedSplit), "Set Solar Export Split to $roundedSplit%")
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Apply Solar Export Split ($roundedSplit%)")
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
                    val seriesChips = listOf(
                        Triple("Solar", showSolar, AccentSolar) to { showSolar = !showSolar },
                        Triple("Load", showLoad, AccentLoad) to { showLoad = !showLoad },
                        Triple("Battery", showBat, AccentBat) to { showBat = !showBat },
                        Triple("Grid", showGrid, AccentGridImport) to { showGrid = !showGrid },
                        Triple("SoC %", showSoc, AccentGridExport) to { showSoc = !showSoc },
                    )
                    seriesChips.forEach { (meta, toggle) ->
                        val (label, isSelected, accent) = meta
                        FilterChip(
                            selected = isSelected,
                            onClick = toggle,
                            label = { Text(label, fontSize = 11.sp, color = if (isSelected) accent else TextMuted) },
                            colors = FilterChipDefaults.filterChipColors(
                                containerColor = Color(0xFF0F172A),
                                selectedContainerColor = Color(0xFF1E293B),
                            ),
                            border = FilterChipDefaults.filterChipBorder(
                                enabled = true,
                                selected = isSelected,
                                borderColor = BorderCard,
                                selectedBorderColor = accent.copy(alpha = 0.7f),
                            ),
                        )
                    }
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
            SectionCard(title = if (sys.isRaspberryPi) "🍓 Raspberry Pi Health" else "🖥️ Host System Health") {
                StatRow("Hostname", sys.hostname.ifBlank { "—" })
                StatRow("Hardware", sys.hardwareModel.ifBlank { if (sys.isRaspberryPi) "Raspberry Pi" else "Linux Host" })
                StatRow("App Version", if (sys.appVersion.isNotBlank()) "v${sys.appVersion}" else "—")
                StatRow("CPU Temperature", sys.cpuTempC?.let { "$it °C" } ?: "—")
                val diskLabel = sys.storageType.ifBlank { "Disk" }
                StatRow("RAM / $diskLabel Usage", "${sys.memPct.roundToInt()}% RAM  •  ${sys.diskPct.roundToInt()}% $diskLabel")
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

        item {
            AndroidAppUpdateCard()
        }

        item { Spacer(Modifier.height(8.dp)) }
    }
}

@Composable
private fun AndroidAppUpdateCard() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val currentVersion = remember { AppUpdater.getCurrentVersionName(context) }

    var checking by remember { mutableStateOf(false) }
    var downloading by remember { mutableStateOf(false) }
    var downloadPct by remember { mutableIntStateOf(0) }
    var releaseInfo by remember { mutableStateOf<AndroidReleaseInfo?>(null) }
    var downloadedApk by remember { mutableStateOf<File?>(null) }
    var statusText by remember { mutableStateOf<String?>(null) }
    var statusColor by remember { mutableStateOf(TextMuted) }

    SectionCard(title = "📦 Android App Updates (GitHub)") {
        StatRow("Installed Version", "v$currentVersion")
        val latestLabel = when {
            checking -> "Checking…"
            releaseInfo == null -> "Not checked"
            releaseInfo?.ok == true -> "v${releaseInfo?.latestVersion}"
            else -> "Check failed"
        }
        val latestColor = when {
            releaseInfo?.updateAvailable == true -> AccentSolar
            releaseInfo?.ok == true -> AccentBat
            releaseInfo != null && releaseInfo?.ok == false -> AccentGridImport
            else -> TextMain
        }
        StatRow("GitHub Latest", latestLabel, latestColor)

        if (!statusText.isNullOrBlank()) {
            Spacer(Modifier.height(6.dp))
            Text(statusText!!, color = statusColor, fontSize = 12.sp)
        }

        if (downloading) {
            Spacer(Modifier.height(8.dp))
            LinearProgressIndicator(
                progress = { (downloadPct / 100f).coerceIn(0f, 1f) },
                modifier = Modifier.fillMaxWidth(),
                color = AccentPrimary,
                trackColor = Color(0xFF1F2937),
            )
            Spacer(Modifier.height(4.dp))
            Text("Downloading APK… $downloadPct%", color = TextMuted, fontSize = 11.sp)
        }

        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(
                enabled = !checking && !downloading,
                onClick = {
                    scope.launch {
                        checking = true
                        statusText = "Checking GitHub Releases…"
                        statusColor = TextMuted
                        val info = AppUpdater.checkLatestRelease(context)
                        releaseInfo = info
                        checking = false
                        if (!info.ok) {
                            statusText = info.error ?: "Failed to check GitHub."
                            statusColor = AccentGridImport
                        } else if (info.updateAvailable) {
                            statusText = "Update available: v${info.latestVersion}"
                            statusColor = AccentSolar
                        } else {
                            statusText = "You are on the latest version."
                            statusColor = AccentBat
                        }
                    }
                },
                modifier = Modifier.weight(1f),
            ) {
                Text(if (checking) "Checking…" else "Check Updates")
            }

            val apkUrl = releaseInfo?.apkUrl
            val canDownload = !checking && !downloading && (releaseInfo?.updateAvailable == true) && !apkUrl.isNullOrBlank()
            Button(
                enabled = canDownload || (downloadedApk != null && !downloading),
                onClick = {
                    val existing = downloadedApk
                    if (existing != null && existing.exists() && !downloading) {
                        val launched = AppUpdater.installApk(context, existing)
                        if (!launched) {
                            statusText = "Allow 'Install unknown apps' in Settings, then tap Install again."
                            statusColor = AccentSolar
                        }
                        return@Button
                    }
                    if (apkUrl.isNullOrBlank()) return@Button
                    scope.launch {
                        downloading = true
                        downloadPct = 0
                        statusText = "Downloading v${releaseInfo?.latestVersion ?: ""}…"
                        statusColor = TextMuted
                        try {
                            val apkFile = AppUpdater.downloadApk(context, apkUrl) { pct ->
                                downloadPct = pct
                            }
                            downloadedApk = apkFile
                            downloading = false
                            val launched = AppUpdater.installApk(context, apkFile)
                            if (launched) {
                                statusText = "Installer opened. Follow the prompt to update."
                                statusColor = AccentBat
                            } else {
                                statusText = "Allow 'Install unknown apps' in Settings, then tap Install."
                                statusColor = AccentSolar
                            }
                        } catch (e: Exception) {
                            downloading = false
                            statusText = "Download failed: ${e.message ?: "Unknown error"}"
                            statusColor = AccentGridImport
                        }
                    }
                },
                colors = ButtonDefaults.buttonColors(containerColor = AccentPrimary),
                modifier = Modifier.weight(1f),
            ) {
                Text(
                    when {
                        downloading -> "$downloadPct%"
                        downloadedApk != null -> "Install APK"
                        else -> "Update App"
                    }
                )
            }
        }
    }
}

@Composable
fun CostsScreen(
    state: PlantUiState,
    onRefreshCosts: () -> Unit,
    onSelectCostPeriod: (String) -> Unit,
    onOpenTariffWebSetup: () -> Unit,
    onCheckTariff: (region: String, postcode: String, standingChargeP: Double, imp: TariffSideConfigModel, exp: TariffSideConfigModel, days: Int) -> Unit,
    onSaveNewTariff: (region: String, postcode: String, standingChargeP: Double, imp: TariffSideConfigModel, exp: TariffSideConfigModel, effectiveFrom: String) -> Unit,
    onRemoveTariffHistory: (entryId: String) -> Unit,
    onDismissCheckPreview: () -> Unit,
) {
    val cs = state.costSummary
    val te = state.tariffEditor
    val sym = cs.currencySymbol
    val sub = cs.subUnitSymbol
    val context = LocalContext.current

    var showEditor by remember { mutableStateOf(false) }
    var showSaveDateDialog by remember { mutableStateOf(false) }
    var pendingDeleteEntry by remember { mutableStateOf<TariffHistoryEntryModel?>(null) }

    var draftRegion by remember(te.loaded, te.region) { mutableStateOf(te.region) }
    var draftPostcode by remember(te.loaded, te.postcode) { mutableStateOf(te.postcode) }
    var draftStandingP by remember(te.loaded, te.standingChargeP) { mutableStateOf(String.format(Locale.US, "%.2f", te.standingChargeP)) }

    var draftImpMode by remember(te.loaded, te.importTariff) { mutableStateOf(te.importTariff.mode) }
    var draftImpPresetId by remember(te.loaded, te.importTariff) { mutableStateOf(te.importTariff.presetId) }
    var draftImpLabel by remember(te.loaded, te.importTariff) { mutableStateOf(te.importTariff.label) }
    var draftImpDescription by remember(te.loaded, te.importTariff) { mutableStateOf(te.importTariff.description) }
    var draftImpOctopusCode by remember(te.loaded, te.importTariff) { mutableStateOf(te.importTariff.octopusProductCode) }
    var draftImpRateP by remember(te.loaded, te.importTariff) { mutableStateOf(String.format(Locale.US, "%.2f", te.importTariff.defaultRateP)) }
    var draftImpWindows by remember(te.loaded, te.importTariff) { mutableStateOf(te.importTariff.windows) }

    var draftExpMode by remember(te.loaded, te.exportTariff) { mutableStateOf(te.exportTariff.mode) }
    var draftExpPresetId by remember(te.loaded, te.exportTariff) { mutableStateOf(te.exportTariff.presetId) }
    var draftExpLabel by remember(te.loaded, te.exportTariff) { mutableStateOf(te.exportTariff.label) }
    var draftExpDescription by remember(te.loaded, te.exportTariff) { mutableStateOf(te.exportTariff.description) }
    var draftExpOctopusCode by remember(te.loaded, te.exportTariff) { mutableStateOf(te.exportTariff.octopusProductCode) }
    var draftExpRateP by remember(te.loaded, te.exportTariff) { mutableStateOf(String.format(Locale.US, "%.2f", te.exportTariff.defaultRateP)) }
    var draftExpWindows by remember(te.loaded, te.exportTariff) { mutableStateOf(te.exportTariff.windows) }

    var checkDays by remember { mutableIntStateOf(30) }
    val todayIso = remember { SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Calendar.getInstance().time) }
    var effectiveDateInput by remember { mutableStateOf(todayIso) }

    fun buildDraftImport(): TariffSideConfigModel = TariffSideConfigModel(
        mode = draftImpMode,
        presetId = if (draftImpMode == "preset") draftImpPresetId else "",
        label = if (draftImpMode == "custom") draftImpDescription.ifBlank { draftImpLabel.ifBlank { "Custom Tariff" } } else draftImpLabel.ifBlank { "Import Tariff" },
        description = draftImpDescription.trim(),
        octopusProductCode = if (draftImpMode == "octopus_api") draftImpOctopusCode else "",
        defaultRateP = draftImpRateP.toDoubleOrNull() ?: 24.5,
        windows = if (draftImpMode == "octopus_api") emptyList() else draftImpWindows,
    )

    fun buildDraftExport(): TariffSideConfigModel = TariffSideConfigModel(
        mode = draftExpMode,
        presetId = if (draftExpMode == "preset") draftExpPresetId else "",
        label = if (draftExpMode == "custom") draftExpDescription.ifBlank { draftExpLabel.ifBlank { "Custom Export" } } else draftExpLabel.ifBlank { "Export Tariff" },
        description = draftExpDescription.trim(),
        octopusProductCode = if (draftExpMode == "octopus_api") draftExpOctopusCode else "",
        defaultRateP = draftExpRateP.toDoubleOrNull() ?: 15.0,
        windows = if (draftExpMode == "octopus_api") emptyList() else draftExpWindows,
    )

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
                    Button(
                        onClick = { showEditor = !showEditor },
                        modifier = Modifier.weight(1f),
                    ) {
                        Text(if (showEditor) "Hide Editor" else "Edit / Check Tariff")
                    }
                }
            }
        }

        if (showEditor) {
            item {
                SectionCard(title = "🔧 Tariff & Supplier Setup") {
                    OutlinedTextField(
                        value = draftStandingP,
                        onValueChange = { draftStandingP = it },
                        label = { Text("Standing Charge ($sub/day)") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(10.dp))

                    Text("⬇ Import Tariff", color = AccentGridImport, fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                    Spacer(Modifier.height(4.dp))
                    TariffSideEditor(
                        sideLabel = "Import",
                        mode = draftImpMode,
                        presetId = draftImpPresetId,
                        description = draftImpDescription,
                        octopusCode = draftImpOctopusCode,
                        rateP = draftImpRateP,
                        windows = draftImpWindows,
                        presets = te.importPresets,
                        octopusProducts = te.octopusImportProducts,
                        subUnit = sub,
                        onModeChange = { draftImpMode = it },
                        onPresetSelect = { p ->
                            draftImpPresetId = p.id
                            draftImpLabel = p.name
                            draftImpRateP = String.format(Locale.US, "%.2f", p.defaultRateP)
                            draftStandingP = String.format(Locale.US, "%.2f", p.standingChargeP)
                            draftImpWindows = p.windows
                        },
                        onOctopusSelect = { op ->
                            draftImpOctopusCode = op.code
                            draftImpLabel = op.displayName
                        },
                        onRateChange = { draftImpRateP = it },
                        onDescriptionChange = { draftImpDescription = it },
                        onWindowsChange = { draftImpWindows = it },
                    )

                    Spacer(Modifier.height(12.dp))
                    HorizontalDivider(color = BorderCard)
                    Spacer(Modifier.height(10.dp))

                    Text("⬆ Export Tariff", color = AccentBat, fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                    Spacer(Modifier.height(4.dp))
                    TariffSideEditor(
                        sideLabel = "Export",
                        mode = draftExpMode,
                        presetId = draftExpPresetId,
                        description = draftExpDescription,
                        octopusCode = draftExpOctopusCode,
                        rateP = draftExpRateP,
                        windows = draftExpWindows,
                        presets = te.exportPresets,
                        octopusProducts = te.octopusExportProducts,
                        subUnit = sub,
                        onModeChange = { draftExpMode = it },
                        onPresetSelect = { p ->
                            draftExpPresetId = p.id
                            draftExpLabel = p.name
                            draftExpRateP = String.format(Locale.US, "%.2f", p.defaultRateP)
                            draftExpWindows = p.windows
                        },
                        onOctopusSelect = { op ->
                            draftExpOctopusCode = op.code
                            draftExpLabel = op.displayName
                        },
                        onRateChange = { draftExpRateP = it },
                        onDescriptionChange = { draftExpDescription = it },
                        onWindowsChange = { draftExpWindows = it },
                    )

                    Spacer(Modifier.height(12.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        OutlinedButton(
                            onClick = {
                                onCheckTariff(
                                    draftRegion,
                                    draftPostcode,
                                    draftStandingP.toDoubleOrNull() ?: 60.0,
                                    buildDraftImport(),
                                    buildDraftExport(),
                                    checkDays,
                                )
                            },
                            enabled = !te.checkInProgress && !te.savingInProgress,
                            modifier = Modifier.weight(1f),
                        ) {
                            Text(if (te.checkInProgress) "Checking…" else "🔍 Check Tariff")
                        }
                        Button(
                            onClick = {
                                effectiveDateInput = todayIso
                                showSaveDateDialog = true
                            },
                            enabled = !te.checkInProgress && !te.savingInProgress,
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("💾 Save New Tariff")
                        }
                    }

                    te.checkResult?.let { chk ->
                        Spacer(Modifier.height(10.dp))
                        Card(
                            colors = CardDefaults.cardColors(containerColor = Color(0xFF0C1A2E)),
                            border = BorderStroke(1.dp, AccentPrimary.copy(alpha = 0.6f)),
                            shape = RoundedCornerShape(10.dp),
                        ) {
                            Column(modifier = Modifier.padding(12.dp)) {
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Text(
                                        "🔍 Tariff Check Preview (Not Saved)",
                                        color = AccentPrimary,
                                        fontWeight = FontWeight.SemiBold,
                                        fontSize = 12.5.sp,
                                    )
                                    TextButton(onClick = onDismissCheckPreview) {
                                        Text("Dismiss", fontSize = 11.sp)
                                    }
                                }
                                if (chk.error != null) {
                                    Text(chk.error, color = AccentGridImport, fontSize = 12.sp)
                                } else {
                                    val cSym = chk.currencySymbol
                                    val cNetPrefix = if (chk.candidateNetCostGbp < 0) "-" else ""
                                    val bNetPrefix = if (chk.baselineNetCostGbp < 0) "-" else ""
                                    Text(
                                        "Import: $cSym${String.format("%.2f", chk.candidateImportCostGbp)}  •  Export: $cSym${String.format("%.2f", chk.candidateExportIncomeGbp)}  •  Standing: $cSym${String.format("%.2f", chk.candidateStandingChargeGbp)}",
                                        color = TextMain,
                                        fontSize = 11.5.sp,
                                    )
                                    Text(
                                        "Net (${chk.daysCount}d): $cNetPrefix$cSym${String.format("%.2f", abs(chk.candidateNetCostGbp))}  •  Avg: $cSym${String.format("%.2f", abs(chk.candidateAvgDailyNetGbp))}/d",
                                        color = TextMain,
                                        fontSize = 11.5.sp,
                                        fontWeight = FontWeight.Medium,
                                    )
                                    val diffColor = when {
                                        chk.diffGbp < 0 -> AccentBat
                                        chk.diffGbp > 0 -> AccentGridImport
                                        else -> TextMuted
                                    }
                                    val diffText = when {
                                        chk.diffGbp < 0 -> "Saves $cSym${String.format("%.2f", abs(chk.diffGbp))} over ${chk.daysCount}d (est. $cSym${String.format("%.2f", abs(chk.annualEstDiffGbp))}/yr)"
                                        chk.diffGbp > 0 -> "Costs +$cSym${String.format("%.2f", chk.diffGbp)} more over ${chk.daysCount}d (est. +$cSym${String.format("%.2f", chk.annualEstDiffGbp)}/yr)"
                                        else -> "Same net cost as current applied ($bNetPrefix$cSym${String.format("%.2f", abs(chk.baselineNetCostGbp))})"
                                    }
                                    Spacer(Modifier.height(4.dp))
                                    Text(diffText, color = diffColor, fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
                                }
                            }
                        }
                    }

                    Spacer(Modifier.height(6.dp))
                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                        TextButton(onClick = onOpenTariffWebSetup) {
                            Text("Open Full Web Setup ↗", fontSize = 11.5.sp, color = TextMuted)
                        }
                    }
                }
            }
        }

        item {
            SectionCard(title = "📅 Applied Tariffs & Change History") {
                val history = te.tariffHistory
                if (history.isEmpty()) {
                    Text(
                        "Initial / Default Tariff applied to all dates. Use 'Save New Tariff' above to record a tariff change from a specific date.",
                        color = TextMuted,
                        fontSize = 11.5.sp,
                    )
                } else {
                    val ordered = history.sortedBy { it.effectiveFrom }
                    val originalId = ordered.first().id
                    var activeId = ordered.first().id
                    for (item in ordered) {
                        if (item.effectiveFrom <= todayIso) activeId = item.id
                    }
                    ordered.reversed().forEachIndexed { idx, entry ->
                        if (idx > 0) HorizontalDivider(color = BorderCard, modifier = Modifier.padding(vertical = 8.dp))
                        val effLabel = if (entry.effectiveFrom <= "1970-01-01") "Initial (Baseline)" else "From ${entry.effectiveFrom}"
                        val statusBadge = when {
                            entry.id == activeId -> "Active" to AccentBat
                            entry.effectiveFrom > todayIso -> "Scheduled" to AccentSolar
                            else -> "Historical" to TextMuted
                        }
                        val impDisplay = entry.importTariff.description.ifBlank { entry.importTariff.label }
                        val expDisplay = entry.exportTariff.description.ifBlank { entry.exportTariff.label }
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Row(
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(effLabel, color = TextMain, fontWeight = FontWeight.SemiBold, fontSize = 12.5.sp)
                                Text(
                                    statusBadge.first,
                                    color = statusBadge.second,
                                    fontSize = 10.5.sp,
                                    fontWeight = FontWeight.SemiBold,
                                )
                            }
                            if (entry.id != originalId) {
                                TextButton(
                                    onClick = { pendingDeleteEntry = entry },
                                    colors = ButtonDefaults.textButtonColors(contentColor = AccentGridImport),
                                ) {
                                    Text("🗑 Remove", fontSize = 11.5.sp)
                                }
                            } else {
                                Text("Original", color = TextMuted, fontSize = 11.sp)
                            }
                        }
                        Text(
                            "⬇ $impDisplay (${String.format("%.2f", entry.importTariff.defaultRateP)}$sub)  •  ⬆ $expDisplay (${String.format("%.2f", entry.exportTariff.defaultRateP)}$sub)",
                            color = TextMuted,
                            fontSize = 11.sp,
                        )
                        Text(
                            "Standing: ${String.format("%.2f", entry.standingChargeP)}$sub/day",
                            color = TextMuted,
                            fontSize = 11.sp,
                        )
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
                FlowRow(
                    modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    periods.forEach { (key, lbl) ->
                        val active = cs.selectedPeriod == key
                        FilterChip(
                            selected = active,
                            onClick = { onSelectCostPeriod(key) },
                            label = {
                                Text(
                                    lbl,
                                    fontSize = 11.5.sp,
                                    maxLines = 1,
                                    softWrap = false,
                                    color = if (active) Color.White else TextMuted,
                                )
                            },
                            colors = FilterChipDefaults.filterChipColors(
                                containerColor = Color(0xFF0F172A),
                                selectedContainerColor = AccentPrimary,
                            ),
                            border = FilterChipDefaults.filterChipBorder(
                                enabled = true,
                                selected = active,
                                borderColor = BorderCard,
                                selectedBorderColor = AccentPrimary,
                            ),
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

    if (showSaveDateDialog) {
        AlertDialog(
            onDismissRequest = { showSaveDateDialog = false },
            containerColor = BgCard,
            title = { Text("💾 Save New Tariff", color = TextMain) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "Specify the date from which this tariff should be applied. Costs before this date will keep their recorded tariff(s).",
                        color = TextMuted,
                        fontSize = 12.sp,
                    )
                    OutlinedTextField(
                        value = effectiveDateInput,
                        onValueChange = { effectiveDateInput = it },
                        label = { Text("Apply From Date (YYYY-MM-DD)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedButton(
                        onClick = {
                            val cal = Calendar.getInstance()
                            runCatching {
                                val parts = effectiveDateInput.split("-")
                                if (parts.size == 3) {
                                    cal.set(parts[0].toInt(), parts[1].toInt() - 1, parts[2].toInt())
                                }
                            }
                            android.app.DatePickerDialog(
                                context,
                                { _, y, m, d ->
                                    effectiveDateInput = String.format(Locale.US, "%04d-%02d-%02d", y, m + 1, d)
                                },
                                cal.get(Calendar.YEAR),
                                cal.get(Calendar.MONTH),
                                cal.get(Calendar.DAY_OF_MONTH),
                            ).show()
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("📅 Pick Date")
                    }
                }
            },
            confirmButton = {
                Button(
                    onClick = {
                        showSaveDateDialog = false
                        onSaveNewTariff(
                            draftRegion,
                            draftPostcode,
                            draftStandingP.toDoubleOrNull() ?: 60.0,
                            buildDraftImport(),
                            buildDraftExport(),
                            effectiveDateInput.trim(),
                        )
                    },
                ) {
                    Text("Confirm & Save")
                }
            },
            dismissButton = {
                TextButton(onClick = { showSaveDateDialog = false }) {
                    Text("Cancel")
                }
            },
        )
    }

    pendingDeleteEntry?.let { entry ->
        AlertDialog(
            onDismissRequest = { pendingDeleteEntry = null },
            containerColor = BgCard,
            title = { Text("Remove Applied Tariff?", color = TextMain) },
            text = {
                val eff = if (entry.effectiveFrom <= "1970-01-01") "Initial (Baseline)" else entry.effectiveFrom
                Text(
                    "Remove tariff effective from $eff (${entry.importTariff.label} / ${entry.exportTariff.label})? Cost calculations will immediately recalculate.",
                    color = TextMuted,
                    fontSize = 12.5.sp,
                )
            },
            confirmButton = {
                Button(
                    onClick = {
                        val id = entry.id
                        pendingDeleteEntry = null
                        onRemoveTariffHistory(id)
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = AccentGridImport),
                ) {
                    Text("Remove")
                }
            },
            dismissButton = {
                TextButton(onClick = { pendingDeleteEntry = null }) {
                    Text("Cancel")
                }
            },
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TariffSideEditor(
    sideLabel: String,
    mode: String,
    presetId: String,
    description: String,
    octopusCode: String,
    rateP: String,
    windows: List<TariffWindowModel>,
    presets: List<TariffPresetModel>,
    octopusProducts: List<OctopusProductModel>,
    subUnit: String,
    onModeChange: (String) -> Unit,
    onPresetSelect: (TariffPresetModel) -> Unit,
    onOctopusSelect: (OctopusProductModel) -> Unit,
    onRateChange: (String) -> Unit,
    onDescriptionChange: (String) -> Unit,
    onWindowsChange: (List<TariffWindowModel>) -> Unit,
) {
    var presetMenuOpen by remember { mutableStateOf(false) }
    var octMenuOpen by remember { mutableStateOf(false) }

    val modes = listOf("preset" to "Preset", "octopus_api" to "Octopus API", "custom" to "Custom")
    FlowRow(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        modes.forEach { (mKey, mLbl) ->
            val active = mode == mKey
            FilterChip(
                selected = active,
                onClick = { onModeChange(mKey) },
                label = { Text(mLbl, fontSize = 11.sp, color = if (active) Color.White else TextMuted) },
                colors = FilterChipDefaults.filterChipColors(
                    containerColor = Color(0xFF0F172A),
                    selectedContainerColor = AccentPrimary,
                ),
            )
        }
    }

    if (mode == "preset" && presets.isNotEmpty()) {
        val currentPreset = presets.find { it.id == presetId }
        Box(modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(onClick = { presetMenuOpen = true }, modifier = Modifier.fillMaxWidth()) {
                Text(
                    currentPreset?.let { "${it.supplier} — ${it.name}" } ?: "Select $sideLabel Preset ▾",
                    maxLines = 1,
                    fontSize = 12.sp,
                )
            }
            DropdownMenu(expanded = presetMenuOpen, onDismissRequest = { presetMenuOpen = false }) {
                presets.forEach { p ->
                    DropdownMenuItem(
                        text = { Text("${p.supplier} — ${p.name}", fontSize = 12.5.sp) },
                        onClick = {
                            presetMenuOpen = false
                            onPresetSelect(p)
                        },
                    )
                }
            }
        }
        Spacer(Modifier.height(6.dp))
    } else if (mode == "octopus_api") {
        val currentOct = octopusProducts.find { it.code == octopusCode }
        Box(modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(onClick = { octMenuOpen = true }, modifier = Modifier.fillMaxWidth()) {
                Text(
                    currentOct?.displayName ?: (if (octopusCode.isNotBlank()) octopusCode else "Select Octopus Product ▾"),
                    maxLines = 1,
                    fontSize = 12.sp,
                )
            }
            DropdownMenu(expanded = octMenuOpen, onDismissRequest = { octMenuOpen = false }) {
                octopusProducts.forEach { op ->
                    DropdownMenuItem(
                        text = { Text(op.displayName, fontSize = 12.5.sp) },
                        onClick = {
                            octMenuOpen = false
                            onOctopusSelect(op)
                        },
                    )
                }
            }
        }
        Spacer(Modifier.height(6.dp))
    }

    OutlinedTextField(
        value = description,
        onValueChange = onDescriptionChange,
        label = { Text("$sideLabel Description") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    Spacer(Modifier.height(6.dp))

    OutlinedTextField(
        value = rateP,
        onValueChange = onRateChange,
        label = { Text("Default / Day Rate ($subUnit/kWh)") },
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )

    if (mode != "octopus_api") {
        Spacer(Modifier.height(6.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Time-of-Use Windows (${windows.size})", color = TextMuted, fontSize = 11.5.sp)
            TextButton(onClick = {
                onWindowsChange(windows + TariffWindowModel("00:00", "07:00", 7.0, "Off-Peak"))
            }) {
                Text("+ Add Window", fontSize = 11.5.sp)
            }
        }
        windows.forEachIndexed { idx, w ->
            Row(
                modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
                horizontalArrangement = Arrangement.spacedBy(4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedTextField(
                    value = w.start,
                    onValueChange = { nv ->
                        val copy = windows.toMutableList()
                        copy[idx] = w.copy(start = nv)
                        onWindowsChange(copy)
                    },
                    label = { Text("Start") },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = w.end,
                    onValueChange = { nv ->
                        val copy = windows.toMutableList()
                        copy[idx] = w.copy(end = nv)
                        onWindowsChange(copy)
                    },
                    label = { Text("End") },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = String.format(Locale.US, "%.2f", w.rateP),
                    onValueChange = { nv ->
                        val parsed = nv.toDoubleOrNull()
                        if (parsed != null) {
                            val copy = windows.toMutableList()
                            copy[idx] = w.copy(rateP = parsed)
                            onWindowsChange(copy)
                        }
                    },
                    label = { Text(subUnit) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                    modifier = Modifier.weight(0.9f),
                )
                TextButton(onClick = {
                    val copy = windows.toMutableList()
                    copy.removeAt(idx)
                    onWindowsChange(copy)
                }) {
                    Text("✕", color = AccentGridImport)
                }
            }
        }
    }
}
