package com.eliterf.kiosk.security

object SafeLog {
    private val allowedCodes = setOf(
        "server_unavailable",
        "network_unavailable",
        "certificate_error",
        "policy_failure",
        "renderer_restart",
        "camera_unavailable"
    )
    fun message(code: String): String = if (code in allowedCodes) code else "kiosk_error"
}
