package com.givenergy.localcontrol

import android.content.Context

data class PairingConfig(
    val propertyName: String = "Home",
    val funnelUrl: String = "",
    val lanUrl: String = "",
    val apiToken: String = "",
) {
    val isConfigured: Boolean
        get() = (funnelUrl.isNotBlank() && apiToken.isNotBlank()) || lanUrl.isNotBlank()
}

object PairingStore {
    private const val PREFS_NAME = "givenergy_pairing_prefs"
    private const val KEY_PROPERTY = "property_name"
    private const val KEY_FUNNEL_URL = "funnel_url"
    private const val KEY_LAN_URL = "lan_url"
    private const val KEY_API_TOKEN = "api_token"

    fun normalizeUrl(raw: String): String {
        val trimmed = raw.trim().trimEnd('/')
        if (trimmed.isEmpty()) return ""
        return if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
            trimmed
        } else {
            "https://$trimmed"
        }
    }

    fun load(context: Context): PairingConfig {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        return PairingConfig(
            propertyName = prefs.getString(KEY_PROPERTY, "Home") ?: "Home",
            funnelUrl = prefs.getString(KEY_FUNNEL_URL, "") ?: "",
            lanUrl = prefs.getString(KEY_LAN_URL, "") ?: "",
            apiToken = prefs.getString(KEY_API_TOKEN, "") ?: "",
        )
    }

    fun save(context: Context, config: PairingConfig) {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit()
            .putString(KEY_PROPERTY, config.propertyName.ifBlank { "Home" })
            .putString(KEY_FUNNEL_URL, normalizeUrl(config.funnelUrl))
            .putString(KEY_LAN_URL, normalizeUrl(config.lanUrl))
            .putString(KEY_API_TOKEN, config.apiToken.trim())
            .apply()
    }
}
