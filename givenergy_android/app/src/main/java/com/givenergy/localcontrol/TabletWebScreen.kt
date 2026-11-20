package com.givenergy.localcontrol

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun TabletWebScreen(
    pairingConfig: PairingConfig,
    connectionMode: String,
    reloadTrigger: Int,
    onOpenPairing: () -> Unit,
) {
    val targetUrl = remember(pairingConfig, connectionMode) {
        when {
            connectionMode == "LAN" && pairingConfig.lanUrl.isNotBlank() -> pairingConfig.lanUrl
            connectionMode == "Funnel HTTPS" && pairingConfig.funnelUrl.isNotBlank() -> pairingConfig.funnelUrl
            pairingConfig.lanUrl.isNotBlank() && connectionMode != "Connecting…" -> pairingConfig.lanUrl
            pairingConfig.funnelUrl.isNotBlank() -> pairingConfig.funnelUrl
            else -> pairingConfig.lanUrl
        }
    }

    if (targetUrl.isBlank()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Color(0xFF0B1120))
                .padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                "No Server URL Configured",
                color = Color(0xFFF1F5F9),
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                "Pair with your GivEnergy local server to load the tablet dashboard.",
                color = Color(0xFF94A3B8),
                fontSize = 14.sp,
            )
            Spacer(Modifier.height(16.dp))
            Button(
                onClick = onOpenPairing,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF2563EB)),
            ) {
                Text("Open Pairing Setup")
            }
        }
        return
    }

    var webViewRef by remember { mutableStateOf<WebView?>(null) }
    var isLoading by remember { mutableStateOf(true) }
    var canGoBack by remember { mutableStateOf(false) }
    var loadedUrl by remember { mutableStateOf("") }

    BackHandler(enabled = canGoBack) {
        webViewRef?.goBack()
    }

    LaunchedEffect(reloadTrigger) {
        if (reloadTrigger > 0) {
            webViewRef?.reload()
        }
    }

    LaunchedEffect(targetUrl, pairingConfig.apiToken) {
        val wv = webViewRef ?: return@LaunchedEffect
        if (loadedUrl != targetUrl) {
            applyAuthCookieAndLoad(wv, targetUrl, pairingConfig.apiToken)
            loadedUrl = targetUrl
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            webViewRef?.destroy()
            webViewRef = null
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF0B1120))
    ) {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { ctx ->
                WebView(ctx).apply {
                    setBackgroundColor(android.graphics.Color.parseColor("#0B1120"))
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    settings.useWideViewPort = true
                    settings.loadWithOverviewMode = true
                    settings.builtInZoomControls = true
                    settings.displayZoomControls = false
                    settings.mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE

                    webChromeClient = WebChromeClient()
                    webViewClient = object : WebViewClient() {
                        override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                            isLoading = true
                        }

                        override fun onPageFinished(view: WebView?, url: String?) {
                            isLoading = false
                            canGoBack = view?.canGoBack() == true
                        }

                        override fun shouldOverrideUrlLoading(
                            view: WebView?,
                            request: WebResourceRequest?,
                        ): Boolean = false
                    }

                    webViewRef = this
                    applyAuthCookieAndLoad(this, targetUrl, pairingConfig.apiToken)
                    loadedUrl = targetUrl
                }
            },
        )

        if (isLoading) {
            LinearProgressIndicator(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(3.dp)
                    .align(Alignment.TopCenter),
                color = Color(0xFF38BDF8),
                trackColor = Color(0xFF1E293B),
            )
        }
    }
}

private fun applyAuthCookieAndLoad(webView: WebView, url: String, apiToken: String) {
    val cm = CookieManager.getInstance()
    cm.setAcceptCookie(true)
    cm.setAcceptThirdPartyCookies(webView, true)
    if (apiToken.isNotBlank()) {
        cm.setCookie(url, "givenergy_token=$apiToken; Path=/")
        cm.flush()
        webView.loadUrl(url, mapOf("Authorization" to "Bearer $apiToken"))
    } else {
        webView.loadUrl(url)
    }
}
