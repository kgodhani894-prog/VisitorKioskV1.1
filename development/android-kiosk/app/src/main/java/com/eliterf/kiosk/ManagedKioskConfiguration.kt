package com.eliterf.kiosk

object ManagedKioskConfiguration {
    fun selectedUrl(managedUrl: String?, storedUrl: String?, allowInsecureHttp: Boolean = false): String? {
        val candidate = managedUrl?.trim().orEmpty()
        return if (candidate.isNotEmpty() && KioskUrlPolicy.validServerUrl(candidate, allowInsecureHttp)) candidate else storedUrl
    }
}
