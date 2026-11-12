package com.eliterf.kiosk.security

import android.content.Context
import java.security.SecureRandom

class PinAuth(context: Context) {
    private val prefs = context.getSharedPreferences("admin_security", Context.MODE_PRIVATE)
    private val random = SecureRandom()

    fun configured(): Boolean = prefs.contains("pin_hash") && prefs.contains("recovery_hash")

    fun changePin(current: String, replacement: String): Boolean {
        if (!verifyPin(current) || !PinAuthCore.validPin(replacement)) return false
        prefs.edit().putString("pin_hash", PinAuthCore.encode(hash(replacement, newSalt("pin_salt")))).putInt("failed", 0).apply()
        return true
    }

    fun setInitial(pin: String): String {
        require(PinAuthCore.validPin(pin))
        val recovery = PinAuthCore.recoveryCode(random)
        prefs.edit().putString("pin_hash", PinAuthCore.encode(hash(pin, newSalt("pin_salt"))))
            .putString("recovery_hash", PinAuthCore.encode(hash(recovery, newSalt("recovery_salt"))))
            .putInt("failed", 0).apply()
        return recovery
    }

    fun verifyPin(pin: String): Boolean {
        if (System.currentTimeMillis() < prefs.getLong("locked_until", 0)) return false
        val ok = PinAuthCore.matches(pin, salt("pin_salt"), decode(prefs.getString("pin_hash", "") ?: ""))
        if (ok) prefs.edit().putInt("failed", 0).apply()
        else {
            val failures = prefs.getInt("failed", 0) + 1
            prefs.edit().putInt("failed", failures).apply()
            if (failures >= 5) prefs.edit().putLong("locked_until", System.currentTimeMillis() + 5 * 60_000).putInt("failed", 0).apply()
        }
        return ok
    }

    fun verifyRecovery(code: String): Boolean {
        if (System.currentTimeMillis() < prefs.getLong("locked_until", 0)) return false
        val ok = PinAuthCore.matches(code, salt("recovery_salt"), decode(prefs.getString("recovery_hash", "") ?: ""))
        if (ok) prefs.edit().putInt("failed", 0).apply()
        return ok
    }

    private fun newSalt(key: String): ByteArray = ByteArray(16).also { random.nextBytes(it); prefs.edit().putString(key, PinAuthCore.encode(it)).apply() }
    private fun salt(key: String) = PinAuthCore.decode(prefs.getString(key, "") ?: "")
    private fun hash(value: String, salt: ByteArray) = PinAuthCore.hash(value, salt)
    private fun decode(value: String) = runCatching { PinAuthCore.decode(value) }.getOrDefault(ByteArray(0))
}
