package com.eliterf.kiosk

enum class KioskFailure(
    val code: String,
    val title: String,
    val message: String,
    val retryable: Boolean
) {
    SERVER_UNAVAILABLE(
        "server_unavailable",
        "Reconnecting to Elite RF",
        "The check-in service is temporarily unavailable. Please wait while we reconnect.",
        true
    ),
    NETWORK_UNAVAILABLE(
        "network_unavailable",
        "Connection unavailable",
        "Please wait while we restore the tablet connection.",
        true
    ),
    CERTIFICATE_ERROR(
        "certificate_error",
        "Security configuration error",
        "The server certificate is not trusted. Administrator assistance is required.",
        false
    ),
    UNAPPROVED_NAVIGATION(
        "policy_failure",
        "Security policy blocked a page",
        "This kiosk attempted to open a page that is not approved.",
        false
    )
}
