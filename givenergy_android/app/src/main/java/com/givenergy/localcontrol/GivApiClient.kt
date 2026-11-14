package com.givenergy.localcontrol

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class GivApiClient {

    private val probeHttp = OkHttpClient.Builder()
        .connectTimeout(1200, TimeUnit.MILLISECONDS)
        .readTimeout(1200, TimeUnit.MILLISECONDS)
        .build()

    private val apiHttp = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    private var activeBaseUrl: String = ""
    private var activeToken: String = ""
    private var activeMode: String = "Connecting…"
    private var webSocket: WebSocket? = null

    fun resolveEndpoint(config: PairingConfig): Pair<String, String> {
        if (config.lanUrl.isNotBlank() && isLanReachable(config.lanUrl)) {
            activeBaseUrl = config.lanUrl
            activeToken = config.apiToken
            activeMode = "LAN"
            return activeBaseUrl to activeMode
        }
        if (config.funnelUrl.isNotBlank()) {
            activeBaseUrl = config.funnelUrl
            activeToken = config.apiToken
            activeMode = "Funnel HTTPS"
            return activeBaseUrl to activeMode
        }
        activeBaseUrl = config.lanUrl
        activeToken = config.apiToken
        activeMode = "Offline"
        return activeBaseUrl to activeMode
    }

    private fun isLanReachable(lanUrl: String): Boolean {
        return try {
            val req = Request.Builder().url("$lanUrl/api/system/stats").get().build()
            probeHttp.newCall(req).execute().use { it.isSuccessful }
        } catch (_: Exception) {
            false
        }
    }

    private fun buildAuthorizedRequest(path: String): Request.Builder {
        val builder = Request.Builder().url("$activeBaseUrl$path")
        if (activeToken.isNotBlank()) {
            builder.header("Authorization", "Bearer $activeToken")
        }
        return builder
    }

    fun fetchSnapshot(): JSONObject? {
        if (activeBaseUrl.isBlank()) return null
        val req = buildAuthorizedRequest("/api/snapshot").get().build()
        return apiHttp.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) throw RuntimeException("HTTP ${resp.code}")
            val body = resp.body?.string() ?: return null
            JSONObject(body)
        }
    }

    fun fetchDailyHistory(limit: Int = 30): JSONObject? {
        if (activeBaseUrl.isBlank()) return null
        val req = buildAuthorizedRequest("/api/history/daily?limit=$limit").get().build()
        return apiHttp.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) return null
            val body = resp.body?.string() ?: return null
            JSONObject(body)
        }
    }

    fun fetchIntradayHistory(): JSONObject? {
        if (activeBaseUrl.isBlank()) return null
        val req = buildAuthorizedRequest("/api/history/intraday").get().build()
        return apiHttp.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) return null
            val body = resp.body?.string() ?: return null
            JSONObject(body)
        }
    }

    fun fetchRangeHistory(days: Int): JSONObject? {
        if (activeBaseUrl.isBlank()) return null
        val req = buildAuthorizedRequest("/api/history/range?days=$days").get().build()
        return apiHttp.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) return null
            val body = resp.body?.string() ?: return null
            JSONObject(body)
        }
    }

    fun sendControl(target: String, action: String, params: Map<String, Any>, description: String): Boolean {
        if (activeBaseUrl.isBlank()) return false
        val json = JSONObject()
            .put("target", target)
            .put("action", action)
            .put("params", JSONObject(params))
            .put("description", description)
        val body = json.toString().toRequestBody("application/json; charset=utf-8".toMediaType())
        val req = buildAuthorizedRequest("/api/control").post(body).build()
        return apiHttp.newCall(req).execute().use { it.isSuccessful }
    }

    fun pollNow(): Boolean {
        if (activeBaseUrl.isBlank()) return false
        val empty = "".toRequestBody("application/json".toMediaType())
        val req = buildAuthorizedRequest("/api/poll-now").post(empty).build()
        return apiHttp.newCall(req).execute().use { it.isSuccessful }
    }

    fun connectWebSocket(
        onMessage: (JSONObject) -> Unit,
        onDisconnected: (String) -> Unit,
    ) {
        closeWebSocket()
        if (activeBaseUrl.isBlank()) return
        val wsBase = activeBaseUrl
            .replaceFirst("https://", "wss://")
            .replaceFirst("http://", "ws://")
        val req = buildAuthorizedRequest("/ws/live")
            .url("$wsBase/ws/live")
            .build()

        webSocket = apiHttp.newWebSocket(req, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text == "pong") return
                try {
                    onMessage(JSONObject(text))
                } catch (_: Exception) {
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                onDisconnected(t.localizedMessage ?: "Connection lost")
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                onDisconnected(reason.ifBlank { "Closed ($code)" })
            }
        })
    }

    fun closeWebSocket() {
        webSocket?.close(1000, "Closing")
        webSocket = null
    }
}
