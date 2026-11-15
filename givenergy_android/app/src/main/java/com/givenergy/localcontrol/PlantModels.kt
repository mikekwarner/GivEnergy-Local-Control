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
    val chargeStatus: String = "—",
    val powerW: Double = 0.0,
    val chargeLimitA: Double = 32.0,
    val minCurrentA: Double = 6.0,
    val maxCurrentA: Double = 32.0,
    val plugAndGo: Boolean = false,
    val chargeControlState: String = "—",
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
            chargeStatus = evcObj.optString("charging_state", evcObj.optString("charge_status", "—")),
            powerW = evcObj.safeDouble("active_power_w", evcObj.safeDouble("power_w")),
            chargeLimitA = evcObj.safeDouble("charge_limit_a", 32.0),
            minCurrentA = evcObj.safeDouble("evse_min_current_a", 6.0),
            maxCurrentA = evcObj.safeDouble("evse_max_current_a", 32.0),
            plugAndGo = evcObj.optBoolean("plug_and_go_enabled", false),
            chargeControlState = evcObj.optString("charge_control_state", "—"),
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
}
