package com.eliterf.kiosk

import android.content.Context
import android.content.RestrictionsManager
import android.content.pm.ApplicationInfo

class KioskSettings(private val context: Context) {
    private val prefs = context.getSharedPreferences("kiosk_settings", Context.MODE_PRIVATE)
    private val allowInsecureHttp = context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0
    fun url(): String? = prefs.getString("url", null)
    fun setUrl(value: String) { prefs.edit().putString("url", value).apply() }
    fun approvedOrigin(): String? = url()?.let { KioskUrlPolicy.origin(it) }
    fun validUrl(value: String): Boolean = KioskUrlPolicy.validServerUrl(value, allowInsecureHttp)
    fun applyManagedConfiguration(): Boolean {
        val restrictions = context.getSystemService(RestrictionsManager::class.java)
            ?.applicationRestrictions
        val selected = ManagedKioskConfiguration.selectedUrl(
            restrictions?.getString("kiosk_url"),
            url(),
            allowInsecureHttp
        )
        if (selected == null || selected == url()) return false
        setUrl(selected)
        return true
    }
    fun productionLockdown(): Boolean = prefs.getBoolean("production_lockdown", false)
    fun setProductionLockdown(enabled: Boolean) {
        prefs.edit().putBoolean("production_lockdown", enabled).commit()
    }
}
