package com.eliterf.kiosk

import android.content.Context
import com.eliterf.kiosk.security.SafeLog

class KioskStatusStore(context: Context) {
    private val prefs = context.getSharedPreferences("kiosk_status", Context.MODE_PRIVATE)

    fun record(code: String) {
        prefs.edit().putString("last_error", SafeLog.message(code)).apply()
    }

    fun clear() {
        prefs.edit().remove("last_error").apply()
    }

    fun lastError(): String = prefs.getString("last_error", "none") ?: "none"
}
