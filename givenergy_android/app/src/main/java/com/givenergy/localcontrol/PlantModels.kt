package com.givenergy.localcontrol

import org.json.JSONArray
import org.json.JSONObject

data class LivePoint(
    val ts: String,
    val pvW: Double,
    val loadW: Double,
    val gridW: Double,
    val batW: Double,
    val soc: Double,
)

data class DailyRecord(
    val date: String,
    val pvKwh: Double,
    val loadKwh: Double,
    val gridImportKwh: Double,
    val gridExportKwh: Double,
    val batChargeKwh: Double,
    val batDischargeKwh: Double,
)

data class InverterState(
    val connected: Boolean = false,
    val serialNumber: String = "",
    val pPvTotal: Double = 0.0,
    val pBattery: Double = 0.0,
    val pGrid: Double = 0.0,
    val pLoad: Double = 0.0,
    val vGrid: Double = 0.0,
    val fGrid: Double = 0.0,
    val inverterTempC: Double = 0.0,
    val operatingMode: String = "—",
    val ecoMode: Boolean = false,
    val enableCharge: Boolean = false,
    val enableDischarge: Boolean = false,
    val chargeTargetSoc: Int = 100,
    val batterySocReserve: Int = 4,
    val ePvToday: Double = 0.0,
    val eGridImportToday: Double = 0.0,
    val eGridExportToday: Double = 0.0,
    val eBatChargeToday: Double = 0.0,
    val eBatDischargeToday: Double = 0.0,
    val eLoadToday: Double = 0.0,
    val errorMessage: String = "",
)

data class BatteryState(
    val present: Boolean = false,
    val soc: Double = 0.0,
    val voltage: Double = 0.0,
    val current: Double = 0.0,
    val powerW: Double = 0.0,
    val tempC: Double = 0.0,
    val cellVoltages: List<Double> = emptyList(),
)

data class EvChargerState(
    val enabled: Boolean = false,
    val connected: Boolean = false,
    val cableConnected: Boolean = false,
    val chargeStatus: String = "—",
    val powerW: Double = 0.0,
    val chargeLimitA: Double = 32.0,
    val minCurrentA: Double = 6.0,
    val maxCurrentA: Double = 32.0,
    val plugAndGo: Boolean = false,
    val chargeControlState: String = "—",
    val chargingMode: String = "Grid",
    val maxBatteryDischargeW: Int = 0,
    val solarExportSplitPct: Int = 0,
    val sessionEnergyKwh: Double = 0.0,
    val sessionDuration: String = "0m",
    val totalMeterKwh: Double = 0.0,
)

data class PiSystemState(
    val hostname: String = "",
    val hardwareModel: String = "",
    val appVersion: String = "",
    val cpuTempC: Double? = null,
    val memPct: Double = 0.0,
    val diskPct: Double = 0.0,
    val uptimeStr: String = "",
)

data class DailyCostRecord(
    val date: String,
    val daysCount: Int = 1,
    val importKwh: Double,
    val exportKwh: Double,
    val importCostGbp: Double,
    val exportIncomeGbp: Double,
    val standingChargeGbp: Double,
    val netCostGbp: Double,
    val avgDailyNetGbp: Double = 0.0,
    val savingsGbp: Double,
)

data class CostSummaryState(
    val loaded: Boolean = false,
    val selectedPeriod: String = "days",
    val periodDaysCount: Int = 0,
    val periodAvgDailyNetGbp: Double = 0.0,
    val periodImportKwh: Double = 0.0,
    val periodExportKwh: Double = 0.0,
    val periodImportCostGbp: Double = 0.0,
    val periodStandingChargeGbp: Double = 0.0,
    val periodPriceCapCostGbp: Double = 0.0,
    val periodSavingsVsCapGbp: Double = 0.0,
    val currencySymbol: String = "£",
    val subUnitSymbol: String = "p",
    val region: String = "H",
    val importRateP: Double = 24.5,
    val exportRateP: Double = 15.0,
    val standingChargeP: Double = 60.0,
    val importLabel: String = "Standard Variable Tariff",
    val exportLabel: String = "Outgoing Octopus Fixed",
    val todayNetCostGbp: Double = 0.0,
    val todayImportCostGbp: Double = 0.0,
    val todayExportIncomeGbp: Double = 0.0,
    val todayStandingChargeGbp: Double = 0.0,
    val todaySavingsGbp: Double = 0.0,
    val thirtyDayNetCostGbp: Double = 0.0,
    val thirtyDayExportIncomeGbp: Double = 0.0,
    val thirtyDaySavingsGbp: Double = 0.0,
    val dailyCosts: List<DailyCostRecord> = emptyList(),
)

data class TariffWindowModel(
    val start: String = "00:00",
    val end: String = "07:00",
    val rateP: Double = 7.0,
    val label: String = "Off-Peak",
)

data class TariffSideConfigModel(
    val mode: String = "preset",
    val presetId: String = "",
    val label: String = "Standard Variable Tariff (Flat)",
    val description: String = "",
    val octopusProductCode: String = "",
    val octopusTariffCode: String = "",
    val defaultRateP: Double = 24.5,
    val windows: List<TariffWindowModel> = emptyList(),
) {
    fun toJson(): JSONObject {
        val winArr = JSONArray()
        windows.forEach { w ->
            winArr.put(JSONObject().apply {
                put("start", w.start)
                put("end", w.end)
                put("rate_p", w.rateP)
                put("label", w.label)
            })
        }
        return JSONObject().apply {
            put("mode", mode)
            put("preset_id", presetId)
            put("label", label)
            put("description", description)
            put("octopus_product_code", octopusProductCode)
            put("octopus_tariff_code", octopusTariffCode)
            put("default_rate_p", defaultRateP)
            put("windows", winArr)
        }
    }
}

data class TariffHistoryEntryModel(
    val id: String,
    val effectiveFrom: String,
    val standingChargeP: Double,
    val importTariff: TariffSideConfigModel,
    val exportTariff: TariffSideConfigModel,
)

data class TariffPresetModel(
    val id: String,
    val supplier: String,
    val name: String,
    val defaultRateP: Double,
    val standingChargeP: Double,
    val windows: List<TariffWindowModel>,
)

data class OctopusProductModel(
    val code: String,
    val displayName: String,
)

data class TariffCheckResult(
    val days: Int = 30,
    val daysCount: Int = 0,
    val currencySymbol: String = "£",
    val candidateImportCostGbp: Double = 0.0,
    val candidateExportIncomeGbp: Double = 0.0,
    val candidateStandingChargeGbp: Double = 0.0,
    val candidateNetCostGbp: Double = 0.0,
    val candidateAvgDailyNetGbp: Double = 0.0,
    val baselineNetCostGbp: Double = 0.0,
    val diffGbp: Double = 0.0,
    val annualEstDiffGbp: Double = 0.0,
    val error: String? = null,
)

data class TariffEditorState(
    val loaded: Boolean = false,
    val region: String = "H",
    val postcode: String = "",
    val currencySymbol: String = "£",
    val subUnitSymbol: String = "p",
    val standingChargeP: Double = 60.0,
    val importTariff: TariffSideConfigModel = TariffSideConfigModel(),
    val exportTariff: TariffSideConfigModel = TariffSideConfigModel(
        mode = "preset",
        presetId = "octopus_outgoing_fixed",
        label = "Outgoing Octopus Fixed (15.0p)",
        defaultRateP = 15.0,
    ),
    val tariffHistory: List<TariffHistoryEntryModel> = emptyList(),
    val importPresets: List<TariffPresetModel> = emptyList(),
    val exportPresets: List<TariffPresetModel> = emptyList(),
    val regions: List<Pair<String, String>> = emptyList(),
    val octopusImportProducts: List<OctopusProductModel> = emptyList(),
    val octopusExportProducts: List<OctopusProductModel> = emptyList(),
    val checkInProgress: Boolean = false,
    val checkResult: TariffCheckResult? = null,
    val savingInProgress: Boolean = false,
)

data class SolarForecastHour(
    val ts: String,
    val dateStr: String,
    val hourStr: String,
    val gtiWm2: Double,
    val cloudCoverPct: Double,
    val tempC: Double,
    val rawKw: Double,
    val baselineKw: Double,
    val forecastKw: Double,
    val actualKw: Double?,
    val isPast: Boolean,
)

data class SolarForecastDay(
    val dateStr: String,
    val rawKwh: Double,
    val baselineKwh: Double,
    val forecastKwh: Double,
    val actualKwh: Double?,
    val cloudCoverAvgPct: Double,
    val peakForecastKw: Double,
    val isToday: Boolean,
    val isFuture: Boolean,
)

data class SolarForecastState(
    val loaded: Boolean = false,
    val configured: Boolean = false,
    val latitude: Double? = null,
    val longitude: Double? = null,
    val capacityKwp: Double = 5.0,
    val tiltDeg: Double = 35.0,
    val azimuthDeg: Double = 180.0,
    val array2Enabled: Boolean = false,
    val array2CapacityKwp: Double = 0.0,
    val array2TiltDeg: Double = 35.0,
    val array2AzimuthDeg: Double = 270.0,
    val baseEfficiency: Double = 0.85,
    val lastSyncAt: String = "",
    val lastSyncError: String = "",
    val efficiencyPct: Double = 85.0,
    val confidence: String = "Baseline (awaiting actuals)",
    val sampleHours: Int = 0,
    val maeKw: Double? = null,
    val todayForecastKwh: Double = 0.0,
    val todayActualKwh: Double = 0.0,
    val todayRemainingKwh: Double = 0.0,
    val todayPeakKw: Double = 0.0,
    val tomorrowForecastKwh: Double? = null,
    val hourly: List<SolarForecastHour> = emptyList(),
    val daily: List<SolarForecastDay> = emptyList(),
)

data class PlantUiState(
    val propertyName: String = "Home",
    val connectionMode: String = "Connecting…",
    val isOnline: Boolean = false,
    val hasSolar: Boolean = true,
    val serverTime: String = "",
    val inverter: InverterState = InverterState(),
    val battery: BatteryState = BatteryState(),
    val evc: EvChargerState = EvChargerState(),
    val system: PiSystemState = PiSystemState(),
    val liveRing: List<LivePoint> = emptyList(),
    val historySeries: List<LivePoint> = emptyList(),
    val historyRangeDays: Int = 1,
    val dailyHistory: List<DailyRecord> = emptyList(),
    val costSummary: CostSummaryState = CostSummaryState(),
    val tariffEditor: TariffEditorState = TariffEditorState(),
    val solarForecast: SolarForecastState = SolarForecastState(),
    val errorBanner: String? = null,
)

object PlantJsonParser {

    private fun JSONObject.safeDouble(key: String, fallback: Double = 0.0): Double {
        if (!has(key) || isNull(key)) return fallback
        val v = optDouble(key, fallback)
        return if (v.isNaN() || v.isInfinite()) fallback else v
    }

    private fun JSONObject.safeInt(key: String, fallback: Int = 0): Int {
        if (!has(key) || isNull(key)) return fallback
        return optInt(key, fallback)
    }

    fun parseSnapshot(json: JSONObject, current: PlantUiState): PlantUiState {
        val invObj = json.optJSONObject("inverter") ?: JSONObject()
        val batObj = json.optJSONObject("battery") ?: JSONObject()
        val evcObj = json.optJSONObject("ev_charger") ?: JSONObject()
        val capObj = json.optJSONObject("capabilities") ?: JSONObject()
        val sysObj = json.optJSONObject("system")

        val cells = mutableListOf<Double>()
        val cellArr = batObj.optJSONArray("cell_voltages") ?: JSONArray()
        for (i in 0 until cellArr.length()) {
            val v = cellArr.optDouble(i, 0.0)
            if (!v.isNaN() && v > 0.0) cells.add(v)
        }

        val ring = mutableListOf<LivePoint>()
        val ringArr = json.optJSONArray("live_ring") ?: JSONArray()
        for (i in 0 until ringArr.length()) {
            val pt = ringArr.optJSONObject(i) ?: continue
            ring.add(
                LivePoint(
                    ts = pt.optString("ts", ""),
                    pvW = pt.safeDouble("solar_w", pt.safeDouble("p_pv_total")),
                    loadW = pt.safeDouble("home_w", pt.safeDouble("p_load")),
                    gridW = pt.safeDouble("grid_w", pt.safeDouble("p_grid")),
                    batW = pt.safeDouble("battery_w", pt.safeDouble("p_battery")),
                    soc = pt.safeDouble("soc_pct", pt.safeDouble("battery_soc")),
                )
            )
        }

        val inverter = InverterState(
            connected = invObj.optBoolean("connected", false),
            serialNumber = invObj.optString("serial_number", ""),
            pPvTotal = invObj.safeDouble("p_pv_total"),
            pBattery = invObj.safeDouble("p_battery"),
            pGrid = invObj.safeDouble("p_grid_out", invObj.safeDouble("p_grid")),
            pLoad = invObj.safeDouble("p_load_demand", invObj.safeDouble("p_load")),
            vGrid = invObj.safeDouble("v_ac1", invObj.safeDouble("v_grid")),
            fGrid = invObj.safeDouble("f_ac1", invObj.safeDouble("f_grid")),
            inverterTempC = invObj.safeDouble("t_inverter_heatsink", invObj.safeDouble("temp_inverter_heatsink")),
            operatingMode = invObj.optString("operating_mode_summary", "—"),
            ecoMode = invObj.safeInt("battery_power_mode", 0) == 1 || invObj.optBoolean("eco_mode", false),
            enableCharge = invObj.optBoolean("enable_charge", false),
            enableDischarge = invObj.optBoolean("enable_discharge", false),
            chargeTargetSoc = invObj.safeInt("charge_target_soc", 100),
            batterySocReserve = invObj.safeInt("battery_soc_reserve", 4),
            ePvToday = invObj.safeDouble("e_pv_generation_today"),
            eGridImportToday = invObj.safeDouble("e_grid_in_day", invObj.safeDouble("e_grid_import_today")),
            eGridExportToday = invObj.safeDouble("e_grid_out_day", invObj.safeDouble("e_grid_export_today")),
            eBatChargeToday = invObj.safeDouble("e_battery_charge_today"),
            eBatDischargeToday = invObj.safeDouble("e_battery_discharge_today"),
            eLoadToday = invObj.safeDouble("e_consumption_today", invObj.safeDouble("e_load_today")),
            errorMessage = invObj.optString("error_message", ""),
        )

        val battery = BatteryState(
            present = batObj.optBoolean("present", false),
            soc = batObj.safeDouble("soc", invObj.safeDouble("battery_soc")),
            voltage = batObj.safeDouble("v_out", invObj.safeDouble("v_battery")),
            current = batObj.safeDouble("i_battery"),
            powerW = invObj.safeDouble("p_battery", invObj.safeDouble("p_battery")),
            tempC = batObj.safeDouble("t_max", invObj.safeDouble("t_bms_mosfet")),
            cellVoltages = cells,
        )

        val evc = EvChargerState(
            enabled = capObj.optBoolean("evc_enabled", false),
            connected = evcObj.optBoolean("connected", false),
            cableConnected = evcObj.optBoolean("cable_connected", false),
            chargeStatus = evcObj.optString("charging_state", evcObj.optString("charge_status", "—")),
            powerW = evcObj.safeDouble("active_power_w", evcObj.safeDouble("power_w")),
            chargeLimitA = evcObj.safeDouble("charge_limit_a", 32.0),
            minCurrentA = evcObj.safeDouble("evse_min_current_a", 6.0),
            maxCurrentA = evcObj.safeDouble("evse_max_current_a", 32.0),
            plugAndGo = evcObj.optBoolean("plug_and_go_enabled", false),
            chargeControlState = evcObj.optString("charge_control_state", "—"),
            chargingMode = evcObj.optString("charging_mode", "Grid"),
            maxBatteryDischargeW = evcObj.safeInt("max_battery_discharge_w", 0),
            solarExportSplitPct = evcObj.safeInt("solar_export_split_pct", 0),
            sessionEnergyKwh = evcObj.safeDouble("session_energy_kwh"),
            sessionDuration = evcObj.optString("session_duration_formatted", "0m"),
            totalMeterKwh = evcObj.safeDouble("total_meter_energy_kwh"),
        )

        val sys = if (sysObj != null) {
            PiSystemState(
                hostname = sysObj.optString("hostname", ""),
                hardwareModel = sysObj.optString("hardware_model", ""),
                appVersion = sysObj.optString("app_version", ""),
                cpuTempC = if (sysObj.isNull("cpu_temp_c")) null else sysObj.optDouble("cpu_temp_c"),
                memPct = sysObj.optDouble("mem_pct", 0.0),
                diskPct = sysObj.optDouble("disk_pct", 0.0),
                uptimeStr = sysObj.optString("service_uptime_str", ""),
            )
        } else {
            current.system
        }

        return current.copy(
            propertyName = capObj.optString("property_name", current.propertyName),
            isOnline = true,
            hasSolar = capObj.optBoolean("has_solar", true),
            serverTime = json.optString("server_time", ""),
            inverter = inverter,
            battery = battery,
            evc = evc,
            system = sys,
            liveRing = if (ring.isNotEmpty()) ring else current.liveRing,
            errorBanner = null,
        )
    }

    fun parseHistorySeries(json: JSONObject): List<LivePoint> {
        val arr = json.optJSONArray("series") ?: return emptyList()
        val out = mutableListOf<LivePoint>()
        for (i in 0 until arr.length()) {
            val pt = arr.optJSONObject(i) ?: continue
            out.add(
                LivePoint(
                    ts = pt.optString("ts", ""),
                    pvW = pt.safeDouble("solar_w", pt.safeDouble("solar_w_avg")),
                    loadW = pt.safeDouble("home_w", pt.safeDouble("home_w_avg")),
                    gridW = pt.safeDouble("grid_w", pt.safeDouble("grid_w_avg")),
                    batW = pt.safeDouble("battery_w", pt.safeDouble("battery_w_avg")),
                    soc = pt.safeDouble("soc_pct"),
                )
            )
        }
        return out
    }

    fun parseDailyHistory(json: JSONObject): List<DailyRecord> {
        val arr = json.optJSONArray("days") ?: return emptyList()
        val out = mutableListOf<DailyRecord>()
        for (i in 0 until arr.length()) {
            val d = arr.optJSONObject(i) ?: continue
            out.add(
                DailyRecord(
                    date = d.optString("date_str", d.optString("date", "")),
                    pvKwh = d.safeDouble("pv_kwh", d.safeDouble("e_pv_kwh")),
                    loadKwh = d.safeDouble("consumption_kwh", d.safeDouble("e_load_kwh")),
                    gridImportKwh = d.safeDouble("grid_import_kwh", d.safeDouble("e_grid_import_kwh")),
                    gridExportKwh = d.safeDouble("grid_export_kwh", d.safeDouble("e_grid_export_kwh")),
                    batChargeKwh = d.safeDouble("battery_charge_kwh", d.safeDouble("e_bat_charge_kwh")),
                    batDischargeKwh = d.safeDouble("battery_discharge_kwh", d.safeDouble("e_bat_discharge_kwh")),
                )
            )
        }
        return out
    }

    fun parseCostSummary(json: JSONObject): CostSummaryState {
        val rates = json.optJSONObject("current_rates") ?: JSONObject()
        val today = json.optJSONObject("today") ?: JSONObject()
        val daily = json.optJSONObject("daily") ?: JSONObject()
        val tot30 = daily.optJSONObject("totals") ?: JSONObject()
        val period = daily.optString("period", "days")
        val daysArr = daily.optJSONArray("rows") ?: daily.optJSONArray("days") ?: JSONArray()
        val rows = mutableListOf<DailyCostRecord>()
        for (i in 0 until daysArr.length()) {
            val d = daysArr.optJSONObject(i) ?: continue
            val net = d.safeDouble("net_cost_gbp")
            val dc = d.optInt("days_count", 1).coerceAtLeast(1)
            rows.add(
                DailyCostRecord(
                    date = d.optString("label", "").ifBlank { d.optString("date_str", "") },
                    daysCount = dc,
                    importKwh = d.safeDouble("import_kwh"),
                    exportKwh = d.safeDouble("export_kwh"),
                    importCostGbp = d.safeDouble("import_cost_gbp"),
                    exportIncomeGbp = d.safeDouble("export_income_gbp"),
                    standingChargeGbp = d.safeDouble("standing_charge_gbp"),
                    netCostGbp = net,
                    avgDailyNetGbp = if (d.has("avg_daily_net_gbp")) d.safeDouble("avg_daily_net_gbp") else net / dc,
                    savingsGbp = d.safeDouble("savings_gbp"),
                )
            )
        }
        val impDesc = rates.optString("import_description", "").trim()
        val expDesc = rates.optString("export_description", "").trim()
        val impLbl = rates.optString("import_label", "Import Tariff")
        val expLbl = rates.optString("export_label", "Export Tariff")
        return CostSummaryState(
            loaded = true,
            selectedPeriod = period,
            periodDaysCount = tot30.optInt("days_count", rows.sumOf { it.daysCount }),
            periodAvgDailyNetGbp = tot30.safeDouble("avg_daily_net_gbp"),
            periodImportKwh = tot30.safeDouble("import_kwh"),
            periodExportKwh = tot30.safeDouble("export_kwh"),
            periodImportCostGbp = tot30.safeDouble("import_cost_gbp"),
            periodStandingChargeGbp = tot30.safeDouble("standing_charge_gbp"),
            periodPriceCapCostGbp = tot30.safeDouble("price_cap_cost_gbp"),
            periodSavingsVsCapGbp = tot30.safeDouble("savings_vs_price_cap_gbp"),
            currencySymbol = json.optString("currency_symbol", "£"),
            subUnitSymbol = json.optString("sub_unit_symbol", "p"),
            region = json.optString("region", "H"),
            importRateP = rates.safeDouble("import_rate_p", 24.5),
            exportRateP = rates.safeDouble("export_rate_p", 15.0),
            standingChargeP = rates.safeDouble("standing_charge_p", 60.0),
            importLabel = if (impDesc.isNotEmpty()) "$impDesc ($impLbl)" else impLbl,
            exportLabel = if (expDesc.isNotEmpty()) "$expDesc ($expLbl)" else expLbl,
            todayNetCostGbp = today.safeDouble("net_cost_gbp"),
            todayImportCostGbp = today.safeDouble("import_cost_gbp"),
            todayExportIncomeGbp = today.safeDouble("export_income_gbp"),
            todayStandingChargeGbp = today.safeDouble("standing_charge_gbp"),
            todaySavingsGbp = today.safeDouble("savings_gbp"),
            thirtyDayNetCostGbp = tot30.safeDouble("net_cost_gbp"),
            thirtyDayExportIncomeGbp = tot30.safeDouble("export_income_gbp"),
            thirtyDaySavingsGbp = tot30.safeDouble("savings_gbp"),
            dailyCosts = rows,
        )
    }

    fun parseSolarForecast(json: JSONObject): SolarForecastState {
        val cfg = json.optJSONObject("config") ?: JSONObject()
        val eff = json.optJSONObject("efficiency") ?: JSONObject()
        val tsObj = json.optJSONObject("today_summary") ?: JSONObject()

        val hourlyArr = json.optJSONArray("hourly") ?: JSONArray()
        val hourlyList = mutableListOf<SolarForecastHour>()
        for (i in 0 until hourlyArr.length()) {
            val h = hourlyArr.optJSONObject(i) ?: continue
            hourlyList.add(
                SolarForecastHour(
                    ts = h.optString("ts", ""),
                    dateStr = h.optString("date_str", ""),
                    hourStr = h.optString("hour_str", ""),
                    gtiWm2 = h.safeDouble("gti_wm2"),
                    cloudCoverPct = h.safeDouble("cloud_cover_pct"),
                    tempC = h.safeDouble("temp_c"),
                    rawKw = h.safeDouble("raw_kw"),
                    baselineKw = h.safeDouble("baseline_kw"),
                    forecastKw = h.safeDouble("forecast_kw"),
                    actualKw = if (h.isNull("actual_kw")) null else h.safeDouble("actual_kw"),
                    isPast = h.optBoolean("is_past", false),
                )
            )
        }

        val dailyArr = json.optJSONArray("daily") ?: JSONArray()
        val dailyList = mutableListOf<SolarForecastDay>()
        for (i in 0 until dailyArr.length()) {
            val d = dailyArr.optJSONObject(i) ?: continue
            dailyList.add(
                SolarForecastDay(
                    dateStr = d.optString("date_str", ""),
                    rawKwh = d.safeDouble("raw_kwh"),
                    baselineKwh = d.safeDouble("baseline_kwh"),
                    forecastKwh = d.safeDouble("forecast_kwh"),
                    actualKwh = if (d.isNull("actual_kwh")) null else d.safeDouble("actual_kwh"),
                    cloudCoverAvgPct = d.safeDouble("cloud_cover_avg_pct"),
                    peakForecastKw = d.safeDouble("peak_forecast_kw"),
                    isToday = d.optBoolean("is_today", false),
                    isFuture = d.optBoolean("is_future", false),
                )
            )
        }

        return SolarForecastState(
            loaded = true,
            configured = json.optBoolean("configured", false),
            latitude = if (cfg.isNull("latitude")) null else cfg.safeDouble("latitude"),
            longitude = if (cfg.isNull("longitude")) null else cfg.safeDouble("longitude"),
            capacityKwp = cfg.safeDouble("capacity_kwp", 5.0),
            tiltDeg = cfg.safeDouble("tilt_deg", 35.0),
            azimuthDeg = cfg.safeDouble("azimuth_deg", 180.0),
            array2Enabled = cfg.optBoolean("array2_enabled", false),
            array2CapacityKwp = cfg.safeDouble("array2_capacity_kwp", 0.0),
            array2TiltDeg = cfg.safeDouble("array2_tilt_deg", 35.0),
            array2AzimuthDeg = cfg.safeDouble("array2_azimuth_deg", 270.0),
            baseEfficiency = cfg.safeDouble("base_efficiency", 0.85),
            lastSyncAt = json.optString("last_sync_at", ""),
            lastSyncError = json.optString("last_sync_error", ""),
            efficiencyPct = eff.safeDouble("efficiency_pct", 85.0),
            confidence = eff.optString("confidence", "Baseline (awaiting actuals)"),
            sampleHours = eff.safeInt("sample_hours", 0),
            maeKw = if (eff.isNull("mae_kw")) null else eff.safeDouble("mae_kw"),
            todayForecastKwh = tsObj.safeDouble("forecast_total_kwh"),
            todayActualKwh = tsObj.safeDouble("actual_so_far_kwh"),
            todayRemainingKwh = tsObj.safeDouble("remaining_forecast_kwh"),
            todayPeakKw = tsObj.safeDouble("peak_forecast_kw"),
            tomorrowForecastKwh = if (tsObj.isNull("tomorrow_forecast_kwh")) null else tsObj.safeDouble("tomorrow_forecast_kwh"),
            hourly = hourlyList,
            daily = dailyList,
        )
    }

    private fun parseTariffWindows(arr: JSONArray?): List<TariffWindowModel> {
        if (arr == null) return emptyList()
        val list = mutableListOf<TariffWindowModel>()
        for (i in 0 until arr.length()) {
            val w = arr.optJSONObject(i) ?: continue
            list.add(
                TariffWindowModel(
                    start = w.optString("start", "00:00").take(5),
                    end = w.optString("end", "07:00").take(5),
                    rateP = w.safeDouble("rate_p", 0.0),
                    label = w.optString("label", ""),
                )
            )
        }
        return list
    }

    private fun parseTariffSide(obj: JSONObject?, fallbackLabel: String): TariffSideConfigModel {
        if (obj == null) return TariffSideConfigModel(label = fallbackLabel)
        return TariffSideConfigModel(
            mode = obj.optString("mode", "preset"),
            presetId = obj.optString("preset_id", ""),
            label = obj.optString("label", fallbackLabel),
            description = obj.optString("description", ""),
            octopusProductCode = obj.optString("octopus_product_code", ""),
            octopusTariffCode = obj.optString("octopus_tariff_code", ""),
            defaultRateP = obj.safeDouble("default_rate_p", 24.5),
            windows = parseTariffWindows(obj.optJSONArray("windows")),
        )
    }

    fun parseTariffConfig(json: JSONObject, current: TariffEditorState): TariffEditorState {
        val histArr = json.optJSONArray("tariff_history") ?: JSONArray()
        val histList = mutableListOf<TariffHistoryEntryModel>()
        for (i in 0 until histArr.length()) {
            val e = histArr.optJSONObject(i) ?: continue
            histList.add(
                TariffHistoryEntryModel(
                    id = e.optString("id", ""),
                    effectiveFrom = e.optString("effective_from", ""),
                    standingChargeP = e.safeDouble("standing_charge_p", 60.0),
                    importTariff = parseTariffSide(e.optJSONObject("import_tariff"), "Import Tariff"),
                    exportTariff = parseTariffSide(e.optJSONObject("export_tariff"), "Export Tariff"),
                )
            )
        }
        return current.copy(
            loaded = true,
            region = json.optString("region", current.region),
            postcode = json.optString("postcode", current.postcode),
            currencySymbol = json.optString("currency_symbol", current.currencySymbol),
            subUnitSymbol = json.optString("sub_unit_symbol", current.subUnitSymbol),
            standingChargeP = json.safeDouble("standing_charge_p", current.standingChargeP),
            importTariff = parseTariffSide(json.optJSONObject("import_tariff"), "Import Tariff"),
            exportTariff = parseTariffSide(json.optJSONObject("export_tariff"), "Export Tariff"),
            tariffHistory = histList,
        )
    }

    fun parseTariffPresets(json: JSONObject, current: TariffEditorState): TariffEditorState {
        val regObj = json.optJSONObject("regions") ?: JSONObject()
        val regList = mutableListOf<Pair<String, String>>()
        regObj.keys().forEach { k ->
            regList.add(k to regObj.optString(k, k))
        }
        regList.sortBy { it.first }

        val presetsObj = json.optJSONObject("presets") ?: JSONObject()
        fun parseSidePresets(key: String): List<TariffPresetModel> {
            val arr = presetsObj.optJSONArray(key) ?: return emptyList()
            val out = mutableListOf<TariffPresetModel>()
            for (i in 0 until arr.length()) {
                val p = arr.optJSONObject(i) ?: continue
                out.add(
                    TariffPresetModel(
                        id = p.optString("id", ""),
                        supplier = p.optString("supplier", ""),
                        name = p.optString("name", ""),
                        defaultRateP = p.safeDouble("default_rate_p", 24.5),
                        standingChargeP = p.safeDouble("standing_charge_p", 60.0),
                        windows = parseTariffWindows(p.optJSONArray("windows")),
                    )
                )
            }
            return out
        }

        return current.copy(
            regions = regList,
            importPresets = parseSidePresets("import"),
            exportPresets = parseSidePresets("export"),
        )
    }

    fun parseOctopusProducts(json: JSONObject, current: TariffEditorState): TariffEditorState {
        fun parseProds(key: String): List<OctopusProductModel> {
            val arr = json.optJSONArray(key) ?: return emptyList()
            val out = mutableListOf<OctopusProductModel>()
            for (i in 0 until arr.length()) {
                val p = arr.optJSONObject(i) ?: continue
                out.add(
                    OctopusProductModel(
                        code = p.optString("code", ""),
                        displayName = p.optString("display_name", p.optString("code", "")),
                    )
                )
            }
            return out
        }
        return current.copy(
            octopusImportProducts = parseProds("import_products"),
            octopusExportProducts = parseProds("export_products"),
        )
    }

    fun parseTariffCheckResult(json: JSONObject): TariffCheckResult {
        if (!json.optBoolean("ok", false)) {
            return TariffCheckResult(error = json.optString("error", "Failed to check tariff"))
        }
        val cand = json.optJSONObject("candidate") ?: JSONObject()
        val base = json.optJSONObject("baseline") ?: JSONObject()
        return TariffCheckResult(
            days = json.safeInt("days", 30),
            daysCount = json.safeInt("days_count", 0),
            currencySymbol = json.optString("currency_symbol", "£"),
            candidateImportCostGbp = cand.safeDouble("import_cost_gbp"),
            candidateExportIncomeGbp = cand.safeDouble("export_income_gbp"),
            candidateStandingChargeGbp = cand.safeDouble("standing_charge_gbp"),
            candidateNetCostGbp = cand.safeDouble("net_cost_gbp"),
            candidateAvgDailyNetGbp = cand.safeDouble("avg_daily_net_gbp"),
            baselineNetCostGbp = base.safeDouble("net_cost_gbp"),
            diffGbp = json.safeDouble("diff_gbp"),
            annualEstDiffGbp = json.safeDouble("annual_est_diff_gbp"),
        )
    }
}
