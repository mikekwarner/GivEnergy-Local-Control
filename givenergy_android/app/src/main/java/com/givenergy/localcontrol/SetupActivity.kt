package com.givenergy.localcontrol

import android.os.Bundle
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

class SetupActivity : AppCompatActivity() {

    private lateinit var edtPropertyName: EditText
    private lateinit var edtFunnelUrl: EditText
    private lateinit var edtLanUrl: EditText
    private lateinit var edtApiToken: EditText
    private lateinit var btnToggleToken: Button
    private lateinit var txtStatus: TextView
    private lateinit var btnSaveConnect: Button
    private lateinit var btnScanQr: Button
    private var tokenVisible = false

    private val setupHttp = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .protocols(listOf(Protocol.HTTP_1_1))
        .build()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setup)

        edtPropertyName = findViewById(R.id.edtPropertyName)
        edtFunnelUrl = findViewById(R.id.edtFunnelUrl)
        edtLanUrl = findViewById(R.id.edtLanUrl)
        edtApiToken = findViewById(R.id.edtApiToken)
        btnToggleToken = findViewById(R.id.btnToggleToken)
        txtStatus = findViewById(R.id.txtSetupStatus)
        btnSaveConnect = findViewById(R.id.btnSaveConnect)
        btnScanQr = findViewById(R.id.btnScanQr)

        val existing = PairingStore.load(this)
        edtPropertyName.setText(existing.propertyName)
        edtFunnelUrl.setText(existing.funnelUrl)
        edtLanUrl.setText(existing.lanUrl)
        edtApiToken.setText(existing.apiToken)

        btnToggleToken.setOnClickListener {
            tokenVisible = !tokenVisible
            val sel = edtApiToken.selectionEnd.coerceAtLeast(0)
            val tf = edtApiToken.typeface
            edtApiToken.inputType = if (tokenVisible) {
                InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
            } else {
                InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            }
            edtApiToken.typeface = tf
            edtApiToken.setSelection(sel.coerceAtMost(edtApiToken.text?.length ?: 0))
            btnToggleToken.text = if (tokenVisible) "Hide" else "Show"
        }
        btnScanQr.setOnClickListener { startQrScan() }
        btnSaveConnect.setOnClickListener { testAndSave() }
    }

    private fun startQrScan() {
        showStatus("Opening QR scanner…", isError = false)
        val options = GmsBarcodeScannerOptions.Builder()
            .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
            .enableAutoZoom()
            .build()
        val scanner = GmsBarcodeScanning.getClient(this, options)
        scanner.startScan()
            .addOnSuccessListener { barcode ->
                val raw = barcode.rawValue?.trim().orEmpty()
                if (raw.isEmpty()) {
                    showStatus("QR code was empty.", isError = true)
                    return@addOnSuccessListener
                }
                try {
                    val json = JSONObject(raw)
                    val url = json.optString("url", "")
                    val lan = json.optString("lan_url", "")
                    val token = json.optString("token", "")
                    val name = json.optString("name", "Home")
                    if (name.isNotBlank()) edtPropertyName.setText(name)
                    if (url.isNotBlank()) edtFunnelUrl.setText(url)
                    if (lan.isNotBlank()) edtLanUrl.setText(lan)
                    if (token.isNotBlank()) edtApiToken.setText(token)
                    showStatus("QR code scanned! Verifying connection…", isError = false)
                    testAndSave()
                } catch (e: Exception) {
                    showStatus("Invalid pairing QR code format.", isError = true)
                }
            }
            .addOnCanceledListener {
                txtStatus.visibility = View.GONE
            }
            .addOnFailureListener { e ->
                showStatus("QR scanner unavailable: ${e.localizedMessage ?: "error"}", isError = true)
            }
    }

    private fun testAndSave() {
        val propName = edtPropertyName.text.toString().trim().ifBlank { "Home" }
        val funnelUrl = PairingStore.normalizeUrl(edtFunnelUrl.text.toString())
        val lanUrl = PairingStore.normalizeUrl(edtLanUrl.text.toString())
        val token = edtApiToken.text.toString().trim()

        if (funnelUrl.isBlank() && lanUrl.isBlank()) {
            showStatus("Enter a Funnel HTTPS URL or a Local LAN URL.", isError = true)
            return
        }
        if (funnelUrl.isNotBlank() && token.isBlank()) {
            showStatus("Bearer Token is required for Funnel HTTPS access.", isError = true)
            return
        }

        btnSaveConnect.isEnabled = false
        showStatus("Testing connection…", isError = false)

        thread {
            var funnelOk = false
            var lanOk = false
            var lastError = ""

            if (funnelUrl.isNotBlank()) {
                try {
                    val bodyJson = JSONObject().put("token", token).toString()
                    val req = Request.Builder()
                        .url("$funnelUrl/api/auth/verify")
                        .post(bodyJson.toRequestBody("application/json; charset=utf-8".toMediaType()))
                        .build()
                    val code = setupHttp.newCall(req).execute().use { it.code }
                    if (code == 200) {
                        funnelOk = true
                    } else if (code == 401) {
                        lastError = "Invalid Bearer Token (401 Unauthorized)."
                    } else {
                        lastError = "Funnel returned HTTP $code."
                    }
                } catch (e: Exception) {
                    val msg = e.localizedMessage ?: e.javaClass.simpleName
                    lastError = "Funnel unreachable: $msg. Ensure Tailscale Funnel (tailscale funnel --bg 80) and Funnel ACLs are active on the Pi."
                }
            }

            if (!funnelOk && lanUrl.isNotBlank()) {
                try {
                    val req = Request.Builder().url("$lanUrl/api/system/stats").get().build()
                    val code = setupHttp.newCall(req).execute().use { it.code }
                    if (code == 200) {
                        lanOk = true
                    }
                } catch (_: Exception) {
                }
            }

            runOnUiThread {
                btnSaveConnect.isEnabled = true
                if (funnelOk || lanOk) {
                    PairingStore.save(
                        this,
                        PairingConfig(
                            propertyName = propName,
                            funnelUrl = funnelUrl,
                            lanUrl = lanUrl,
                            apiToken = token,
                        ),
                    )
                    setResult(RESULT_OK)
                    finish()
                } else {
                    showStatus(lastError.ifBlank { "Could not reach GivEnergy server." }, isError = true)
                }
            }
        }
    }

    private fun showStatus(message: String, isError: Boolean) {
        txtStatus.visibility = View.VISIBLE
        txtStatus.setTextColor(if (isError) 0xFFF87171.toInt() else 0xFF93C5FD.toInt())
        txtStatus.text = message
    }
}
