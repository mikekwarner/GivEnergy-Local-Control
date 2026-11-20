package com.givenergy.localcontrol

import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.CurrencyPound
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.EvStation
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Smartphone
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.WbSunny
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import kotlin.concurrent.thread

class MainActivity : ComponentActivity() {

    private val apiClient = GivApiClient()
    private var uiState by mutableStateOf(PlantUiState())
    private var pairingConfigState by mutableStateOf(PairingConfig())
    private val mainHandler = Handler(Looper.getMainLooper())
    @Volatile private var isActivityStarted = false
    @Volatile private var isConnecting = false
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    private val setupLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val loaded = PairingStore.load(this)
        pairingConfigState = loaded
        if (result.resultCode == RESULT_OK || loaded.isConfigured) {
            connectAll(force = true)
        } else {
            finish()
        }
    }

    @OptIn(ExperimentalMaterial3Api::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pairingConfigState = PairingStore.load(this)

        setContent {
            var selectedTab by remember { mutableIntStateOf(0) }
            var webReloadTrigger by remember { mutableIntStateOf(0) }
            val snackbarHostState = remember { SnackbarHostState() }
            val scope = rememberCoroutineScope()
            val config = LocalConfiguration.current
            val isTablet = config.smallestScreenWidthDp >= 600
            val useWebView = when (pairingConfigState.uiMode) {
                "web" -> true
                "native" -> false
                else -> isTablet
            }

            Scaffold(
                containerColor = Color(0xFF0B1120),
                snackbarHost = { SnackbarHost(snackbarHostState) },
                topBar = {
                    TopAppBar(
                        colors = TopAppBarDefaults.topAppBarColors(
                            containerColor = Color(0xFF111827),
                            titleContentColor = Color(0xFFF1F5F9),
                        ),
                        title = {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    "⚡ ${uiState.propertyName}",
                                    fontSize = 17.sp,
                                    fontWeight = FontWeight.Bold,
                                    maxLines = 1,
                                )
                                Spacer(Modifier.width(8.dp))
                                val badgeColor = when (uiState.connectionMode) {
                                    "LAN" -> Color(0xFF34D399)
                                    "Funnel HTTPS" -> Color(0xFF38BDF8)
                                    "Offline" -> Color(0xFFF87171)
                                    else -> Color(0xFF93C5FD)
                                }
                                Box(
                                    modifier = Modifier
                                        .background(Color(0xFF1E293B), RoundedCornerShape(999.dp))
                                        .padding(horizontal = 8.dp, vertical = 2.dp)
                                ) {
                                    Text(uiState.connectionMode, color = badgeColor, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                                }
                            }
                        },
                        actions = {
                            if (isTablet || pairingConfigState.uiMode != "auto") {
                                IconButton(onClick = {
                                    val nextMode = if (useWebView) "native" else "web"
                                    PairingStore.saveUiMode(this@MainActivity, nextMode)
                                    pairingConfigState = pairingConfigState.copy(uiMode = nextMode)
                                }) {
                                    Icon(
                                        imageVector = if (useWebView) Icons.Default.Smartphone else Icons.Default.Language,
                                        contentDescription = if (useWebView) "Switch to Native UI" else "Switch to Web UI",
                                        tint = Color(0xFF94A3B8),
                                    )
                                }
                            }
                            IconButton(onClick = {
                                if (useWebView) {
                                    webReloadTrigger++
                                }
                                thread {
                                    try {
                                        apiClient.pollNow()
                                        refreshSnapshotAndHistory()
                                    } catch (_: Exception) {
                                    }
                                }
                            }) {
                                Icon(Icons.Default.Refresh, contentDescription = "Poll Now", tint = Color(0xFF94A3B8))
                            }
                            IconButton(onClick = {
                                setupLauncher.launch(Intent(this@MainActivity, SetupActivity::class.java))
                            }) {
                                Icon(Icons.Default.Settings, contentDescription = "Pairing", tint = Color(0xFF94A3B8))
                            }
                        },
                    )
                },
                bottomBar = {
                    if (!useWebView) {
                        NavigationBar(containerColor = Color(0xFF111827)) {
                        NavigationBarItem(
                            selected = selectedTab == 0,
                            onClick = { selectedTab = 0 },
                            icon = { Icon(Icons.Default.Bolt, contentDescription = "Live") },
                            label = { Text("Live", maxLines = 1, softWrap = false) },
                        )
                        NavigationBarItem(
                            selected = selectedTab == 2,
                            onClick = {
                                selectedTab = 2
                                loadHistoryAsync(uiState.historyRangeDays)
                            },
                            icon = { Icon(Icons.Default.DateRange, contentDescription = "History") },
                            label = { Text("History", maxLines = 1, softWrap = false) },
                        )
                        NavigationBarItem(
                            selected = selectedTab == 6,
                            onClick = {
                                selectedTab = 6
                                loadSolarForecastAsync()
                            },
                            icon = { Icon(Icons.Default.WbSunny, contentDescription = "Solar") },
                            label = { Text("Solar", maxLines = 1, softWrap = false) },
                        )
                        NavigationBarItem(
                            selected = selectedTab == 4,
                            onClick = {
                                selectedTab = 4
                                loadCostsAsync()
                            },
                            icon = { Icon(Icons.Default.CurrencyPound, contentDescription = "Costs") },
                            label = { Text("Costs", maxLines = 1, softWrap = false) },
                        )
                        NavigationBarItem(
                            selected = selectedTab == 1,
                            onClick = { selectedTab = 1 },
                            icon = { Icon(Icons.Default.Tune, contentDescription = "Inverter") },
                            label = { Text("Inverter", maxLines = 1, softWrap = false) },
                        )
                        NavigationBarItem(
                            selected = selectedTab == 5,
                            onClick = { selectedTab = 5 },
                            icon = { Icon(Icons.Default.EvStation, contentDescription = "EV") },
                            label = { Text("EV", maxLines = 1, softWrap = false) },
                        )
                        NavigationBarItem(
                            selected = selectedTab == 3,
                            onClick = { selectedTab = 3 },
                            icon = { Icon(Icons.Default.Memory, contentDescription = "System") },
                            label = { Text("System", maxLines = 1, softWrap = false) },
                        )
                        }
                    }
                },
            ) { innerPadding ->
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(innerPadding)
                ) {
                    if (useWebView) {
                        TabletWebScreen(
                            pairingConfig = pairingConfigState,
                            connectionMode = uiState.connectionMode,
                            reloadTrigger = webReloadTrigger,
                            onOpenPairing = {
                                setupLauncher.launch(Intent(this@MainActivity, SetupActivity::class.java))
                            },
                        )
                    } else {
                    val sendControlAction: (String, String, Map<String, Any>, String) -> Unit =
                        { target, action, params, desc ->
                            thread {
                                val ok = try {
                                    apiClient.sendControl(target, action, params, desc)
                                } catch (_: Exception) {
                                    false
                                }
                                runOnUiThread {
                                    scope.launch {
                                        snackbarHostState.showSnackbar(
                                            if (ok) "Queued: $desc" else "Failed to send command"
                                        )
                                    }
                                }
                            }
                        }
                    when (selectedTab) {
                        0 -> LiveScreen(uiState)
                        1 -> ControlsScreen(uiState, sendControlAction)
                        5 -> EvChargerScreen(uiState, sendControlAction)
                        2 -> HistoryScreen(
                            state = uiState,
                            onSelectRange = { days -> loadHistoryAsync(days, uiState.historyEndDate) },
                            onSelectEndDate = { endDate -> loadHistoryAsync(uiState.historyRangeDays, endDate) },
                            onReloadHistory = { loadHistoryAsync(uiState.historyRangeDays, uiState.historyEndDate) },
                        )
                        6 -> SolarForecastScreen(
                            state = uiState,
                            onRefreshForecast = { loadSolarForecastAsync() },
                            onSyncOpenMeteo = {
                                thread {
                                    val ok = try {
                                        apiClient.syncSolarForecast()
                                    } catch (_: Exception) {
                                        false
                                    }
                                    if (ok) loadSolarForecastAsync()
                                    runOnUiThread {
                                        scope.launch {
                                            snackbarHostState.showSnackbar(
                                                if (ok) "Open-Meteo forecast synced" else "Open-Meteo sync failed"
                                            )
                                        }
                                    }
                                }
                            },
                            onSaveConfig = { payload ->
                                thread {
                                    val res = try {
                                        apiClient.saveSolarForecastConfig(payload)
                                    } catch (_: Exception) {
                                        null
                                    }
                                    if (res != null) loadSolarForecastAsync()
                                    runOnUiThread {
                                        scope.launch {
                                            snackbarHostState.showSnackbar(
                                                if (res != null) "Solar forecast saved & synced" else "Failed to save solar forecast"
                                            )
                                        }
                                    }
                                }
                            },
                        )
                        4 -> CostsScreen(
                            state = uiState,
                            onRefreshCosts = { loadCostsAsync(uiState.costSummary.selectedPeriod) },
                            onSelectCostPeriod = { period -> loadCostsAsync(period) },
                            onOpenTariffWebSetup = {
                                val cfg = PairingStore.load(this@MainActivity)
                                val base = if (uiState.connectionMode == "Funnel HTTPS" && cfg.funnelUrl.isNotBlank()) {
                                    cfg.funnelUrl
                                } else {
                                    cfg.lanUrl.ifBlank { cfg.funnelUrl }
                                }
                                if (base.isNotBlank()) {
                                    startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("$base/?tab=costs")))
                                }
                            },
                        )
                        3 -> SystemScreen(
                            state = uiState,
                            onReconnectDevices = {
                                thread {
                                    val ok = apiClient.sendControl("system", "reconnect", emptyMap(), "Reconnect devices")
                                    runOnUiThread {
                                        scope.launch {
                                            snackbarHostState.showSnackbar(
                                                if (ok) "Reconnect requested" else "Failed to send reconnect"
                                            )
                                        }
                                    }
                                }
                            },
                            onOpenWebFallback = {
                                val cfg = PairingStore.load(this@MainActivity)
                                val targetUrl = if (uiState.connectionMode == "LAN" && cfg.lanUrl.isNotBlank()) {
                                    cfg.lanUrl
                                } else {
                                    cfg.funnelUrl.ifBlank { cfg.lanUrl }
                                }
                                if (targetUrl.isNotBlank()) {
                                    startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(targetUrl)))
                                }
                            },
                            onOpenPairing = {
                                setupLauncher.launch(Intent(this@MainActivity, SetupActivity::class.java))
                            },
                        )
                    }
                    }
                }
            }
        }

        if (!PairingStore.load(this).isConfigured) {
            setupLauncher.launch(Intent(this, SetupActivity::class.java))
        }
    }

    override fun onStart() {
        super.onStart()
        isActivityStarted = true
        registerNetworkWatcher()
        if (PairingStore.load(this).isConfigured && !uiState.isOnline) {
            connectAll()
        }
    }

    override fun onStop() {
        super.onStop()
        isActivityStarted = false
        unregisterNetworkWatcher()
        mainHandler.removeCallbacksAndMessages(null)
        apiClient.closeWebSocket()
        uiState = uiState.copy(isOnline = false)
    }

    private fun registerNetworkWatcher() {
        val cm = getSystemService(ConnectivityManager::class.java) ?: return
        if (networkCallback != null) return
        var initialSeen = false
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                if (!initialSeen) {
                    initialSeen = true
                    return
                }
                mainHandler.postDelayed({
                    if (isActivityStarted && PairingStore.load(this@MainActivity).isConfigured) {
                        connectAll(force = true)
                    }
                }, 600)
            }
        }
        networkCallback = cb
        try {
            cm.registerDefaultNetworkCallback(cb)
        } catch (_: Exception) {
        }
    }

    private fun unregisterNetworkWatcher() {
        val cm = getSystemService(ConnectivityManager::class.java) ?: return
        networkCallback?.let {
            try {
                cm.unregisterNetworkCallback(it)
            } catch (_: Exception) {
            }
        }
        networkCallback = null
    }

    private fun connectAll(force: Boolean = false) {
        val cfg = PairingStore.load(this)
        if (!cfg.isConfigured) return
        if (isConnecting && !force) return
        isConnecting = true
        mainHandler.removeCallbacksAndMessages(null)
        uiState = uiState.copy(propertyName = cfg.propertyName, connectionMode = "Connecting…", errorBanner = null)

        thread {
            try {
                val (_, mode) = apiClient.resolveEndpoint(cfg)
                runOnUiThread {
                    uiState = uiState.copy(connectionMode = mode)
                }
                refreshSnapshotAndHistory()
                apiClient.connectWebSocket(
                    onMessage = { json ->
                        runOnUiThread {
                            uiState = PlantJsonParser.parseSnapshot(json, uiState)
                        }
                    },
                    onDisconnected = { reason ->
                        runOnUiThread {
                            val intentional = reason == "Closing" || !isActivityStarted
                            uiState = uiState.copy(
                                isOnline = false,
                                errorBanner = if (intentional) null else "Reconnecting: $reason",
                            )
                            if (!intentional) {
                                mainHandler.postDelayed({
                                    if (isActivityStarted) connectAll(force = true)
                                }, 3000)
                            }
                        }
                    },
                )
            } finally {
                isConnecting = false
            }
        }
    }

    private fun refreshSnapshotAndHistory() {
        try {
            val snap = apiClient.fetchSnapshot()
            if (snap != null) {
                runOnUiThread {
                    uiState = PlantJsonParser.parseSnapshot(snap, uiState)
                }
            }
        } catch (e: Exception) {
            runOnUiThread {
                uiState = uiState.copy(errorBanner = "Snapshot error: ${e.localizedMessage ?: "unreachable"}")
            }
        }
        loadHistoryAsync()
        loadCostsAsync()
        loadSolarForecastAsync()
    }

    private fun loadSolarForecastAsync() {
        thread {
            try {
                val json = apiClient.fetchSolarForecastData()
                if (json != null) {
                    val parsed = PlantJsonParser.parseSolarForecast(json)
                    runOnUiThread {
                        uiState = uiState.copy(solarForecast = parsed)
                    }
                }
            } catch (_: Exception) {
            }
        }
    }

    private fun loadCostsAsync(period: String = uiState.costSummary.selectedPeriod) {
        runOnUiThread {
            uiState = uiState.copy(costSummary = uiState.costSummary.copy(selectedPeriod = period))
        }
        thread {
            try {
                val daysParam = if (period == "days") 30 else 0
                val costJson = apiClient.fetchCostSummary(daysParam, period)
                val parsedCost = if (costJson != null) PlantJsonParser.parseCostSummary(costJson) else null

                val cfgJson = apiClient.fetchTariffConfig()
                val presetsJson = if (uiState.tariffEditor.importPresets.isEmpty()) apiClient.fetchTariffPresets() else null
                val octJson = if (uiState.tariffEditor.octopusImportProducts.isEmpty()) runCatching { apiClient.fetchOctopusProducts() }.getOrNull() else null

                runOnUiThread {
                    var nextEditor = uiState.tariffEditor
                    if (presetsJson != null) nextEditor = PlantJsonParser.parseTariffPresets(presetsJson, nextEditor)
                    if (octJson != null) nextEditor = PlantJsonParser.parseOctopusProducts(octJson, nextEditor)
                    if (cfgJson != null) nextEditor = PlantJsonParser.parseTariffConfig(cfgJson, nextEditor)
                    uiState = uiState.copy(
                        costSummary = parsedCost ?: uiState.costSummary,
                        tariffEditor = nextEditor,
                    )
                }
            } catch (_: Exception) {
            }
        }
    }

    private fun loadHistoryAsync(
        days: Int = uiState.historyRangeDays,
        endDate: String = uiState.historyEndDate,
    ) {
        val targetDate = endDate.ifBlank { null }
        runOnUiThread {
            uiState = uiState.copy(historyRangeDays = days, historyEndDate = endDate)
        }
        thread {
            try {
                val seriesJson = if (days <= 1) {
                    apiClient.fetchIntradayHistory(targetDate)
                } else {
                    apiClient.fetchRangeHistory(days, targetDate)
                }
                val series = if (seriesJson != null) PlantJsonParser.parseHistorySeries(seriesJson) else emptyList()
                val hist = apiClient.fetchDailyHistory(30)
                val parsedDaily = if (hist != null) PlantJsonParser.parseDailyHistory(hist) else uiState.dailyHistory
                runOnUiThread {
                    uiState = uiState.copy(
                        historySeries = if (series.isNotEmpty() || seriesJson != null) series else uiState.historySeries,
                        dailyHistory = parsedDaily,
                    )
                }
            } catch (_: Exception) {
            }
        }
    }
}
