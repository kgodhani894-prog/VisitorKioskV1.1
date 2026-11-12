package com.eliterf.kiosk

import java.net.URI

object KioskUrlPolicy {
    private fun normalizedOrigin(uri: URI): String? {
        if (uri.userInfo != null) return null
        val scheme = uri.scheme?.lowercase() ?: return null
        val host = uri.host?.lowercase() ?: return null
        val displayHost = if (host.contains(":")) "[$host]" else host
        val port = when {
            uri.port == -1 -> ""
            scheme == "https" && uri.port == 443 -> ""
            else -> ":${uri.port}"
        }
        return "$scheme://$displayHost$port"
    }

    fun validServerUrl(value: String, allowInsecureHttp: Boolean = false): Boolean {
        val uri = runCatching { URI(value) }.getOrNull() ?: return false
        val secureScheme = uri.scheme.equals("https", true)
        val testScheme = allowInsecureHttp && uri.scheme.equals("http", true)
        return (secureScheme || testScheme) && uri.userInfo == null && !uri.host.isNullOrBlank() && uri.port != 80 && uri.path == "/kiosk"
    }

    fun origin(value: String): String? = runCatching { URI(value) }.getOrNull()?.let(::normalizedOrigin)

    fun allowedUrl(value: String, approvedOrigin: String): Boolean {
        val uri = runCatching { URI(value) }.getOrNull() ?: return false
        val approved = runCatching { URI(approvedOrigin) }.getOrNull() ?: return false
        return normalizedOrigin(uri) == normalizedOrigin(approved) && allowedPath(uri.path ?: "/")
    }

    fun allowedPath(path: String): Boolean {
        if (path == "/admin" || path.startsWith("/api/admin") || path.contains("login", true) || path.contains("setup", true)) return false
        return path == "/" || path == "/kiosk" || path.startsWith("/static/") || path == "/api/employees" || path == "/api/checkins"
    }
}
