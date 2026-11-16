package com.givenergy.localcontrol

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

data class AndroidReleaseInfo(
    val ok: Boolean,
    val currentVersion: String,
    val latestVersion: String? = null,
    val tagName: String? = null,
    val apkUrl: String? = null,
    val apkSizeBytes: Long = 0L,
    val releaseNotes: String? = null,
    val updateAvailable: Boolean = false,
    val error: String? = null,
)

object AppUpdater {
    private const val LATEST_RELEASE_API =
        "https://api.github.com/repos/mikekwarner/GivEnergy-Local-Control/releases/latest"

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .followRedirects(true)
        .build()

    fun getCurrentVersionName(context: Context): String {
        return try {
            @Suppress("DEPRECATION")
            val info = context.packageManager.getPackageInfo(context.packageName, 0)
            info.versionName ?: "1.0.0"
        } catch (_: Exception) {
            "1.0.0"
        }
    }

    private fun parseVersion(version: String): List<Int> {
        val cleaned = version.trim().removePrefix("v").removePrefix("V")
        return cleaned.split(".").mapNotNull { token ->
            val digits = token.takeWhile { it.isDigit() }
            digits.toIntOrNull()
        }
    }

    private fun isNewerVersion(remote: String, current: String): Boolean {
        val r = parseVersion(remote)
        val c = parseVersion(current)
        if (r.isEmpty() || c.isEmpty()) {
            return remote.trim().removePrefix("v") != current.trim().removePrefix("v")
        }
        val maxLen = maxOf(r.size, c.size)
        for (i in 0 until maxLen) {
            val rv = r.getOrElse(i) { 0 }
            val cv = c.getOrElse(i) { 0 }
            if (rv > cv) return true
            if (rv < cv) return false
        }
        return false
    }

    suspend fun checkLatestRelease(context: Context): AndroidReleaseInfo = withContext(Dispatchers.IO) {
        val currentVersion = getCurrentVersionName(context)
        try {
            val req = Request.Builder()
                .url(LATEST_RELEASE_API)
                .header("Accept", "application/vnd.github+json")
                .header("User-Agent", "GivEnergy-Local-Control-Android")
                .build()

            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) {
                    return@withContext AndroidReleaseInfo(
                        ok = false,
                        currentVersion = currentVersion,
                        error = "GitHub returned HTTP ${resp.code}",
                    )
                }
                val body = resp.body?.string() ?: "{}"
                val json = JSONObject(body)
                val tagName = json.optString("tag_name", "").trim()
                val latestVersion = tagName.removePrefix("v").removePrefix("V")
                val releaseNotes = json.optString("body", "").trim().takeIf { it.isNotEmpty() }

                val assets = json.optJSONArray("assets")
                var apkUrl: String? = null
                var apkSize = 0L
                if (assets != null) {
                    for (i in 0 until assets.length()) {
                        val asset = assets.getJSONObject(i)
                        val name = asset.optString("name", "")
                        if (name.endsWith(".apk", ignoreCase = true)) {
                            val url = asset.optString("browser_download_url", "")
                            val size = asset.optLong("size", 0L)
                            if (name.equals("givenergy-local-control.apk", ignoreCase = true) || apkUrl == null) {
                                apkUrl = url
                                apkSize = size
                            }
                        }
                    }
                }

                if (latestVersion.isBlank()) {
                    return@withContext AndroidReleaseInfo(
                        ok = false,
                        currentVersion = currentVersion,
                        error = "No release tag found on GitHub.",
                    )
                }

                AndroidReleaseInfo(
                    ok = true,
                    currentVersion = currentVersion,
                    latestVersion = latestVersion,
                    tagName = tagName,
                    apkUrl = apkUrl,
                    apkSizeBytes = apkSize,
                    releaseNotes = releaseNotes,
                    updateAvailable = isNewerVersion(latestVersion, currentVersion),
                )
            }
        } catch (e: Exception) {
            AndroidReleaseInfo(
                ok = false,
                currentVersion = currentVersion,
                error = e.message ?: "Network error",
            )
        }
    }

    suspend fun downloadApk(
        context: Context,
        apkUrl: String,
        onProgress: (Int) -> Unit,
    ): File = withContext(Dispatchers.IO) {
        val updateDir = File(context.cacheDir, "updates").apply { mkdirs() }
        val targetFile = File(updateDir, "givenergy-local-control.apk")
        if (targetFile.exists()) targetFile.delete()

        val req = Request.Builder()
            .url(apkUrl)
            .header("User-Agent", "GivEnergy-Local-Control-Android")
            .build()

        client.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) {
                throw IllegalStateException("Download failed (HTTP ${resp.code})")
            }
            val body = resp.body ?: throw IllegalStateException("Empty APK response")
            val totalBytes = body.contentLength()
            var downloaded = 0L
            var lastReportedPct = -1

            body.byteStream().use { input ->
                targetFile.outputStream().use { output ->
                    val buffer = ByteArray(32 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read == -1) break
                        output.write(buffer, 0, read)
                        downloaded += read
                        if (totalBytes > 0) {
                            val pct = ((downloaded * 100L) / totalBytes).toInt().coerceIn(0, 100)
                            if (pct != lastReportedPct) {
                                lastReportedPct = pct
                                withContext(Dispatchers.Main) { onProgress(pct) }
                            }
                        }
                    }
                }
            }
        }
        targetFile
    }

    fun canRequestPackageInstalls(context: Context): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.packageManager.canRequestPackageInstalls()
        } else {
            true
        }
    }

    fun openInstallPermissionSettings(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val intent = Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:${context.packageName}"),
            ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
        }
    }

    fun installApk(context: Context, apkFile: File): Boolean {
        if (!canRequestPackageInstalls(context)) {
            openInstallPermissionSettings(context)
            return false
        }
        val uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.fileprovider",
            apkFile,
        )
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
        return true
    }
}
