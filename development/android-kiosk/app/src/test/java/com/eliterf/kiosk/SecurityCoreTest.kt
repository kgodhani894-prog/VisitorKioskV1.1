package com.eliterf.kiosk

import com.eliterf.kiosk.security.PinAuthCore
import com.eliterf.kiosk.security.PinLockout
import com.eliterf.kiosk.security.SafeLog
import java.security.SecureRandom
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SecurityCoreTest {
    @Test fun pinAndRecoveryMaterialAreSalted() {
        val random = SecureRandom()
        val salt = PinAuthCore.newSalt(random)
        val otherSalt = PinAuthCore.newSalt(random)
        val hash = PinAuthCore.hash("12345678", salt)
        assertTrue(PinAuthCore.validPin("12345678"))
        assertTrue(PinAuthCore.validPin("6825"))
        assertFalse(PinAuthCore.validPin("123"))
        assertTrue(PinAuthCore.matches("12345678", salt, hash))
        assertFalse(PinAuthCore.matches("12345679", salt, hash))
        assertNotEquals(PinAuthCore.encode(hash), PinAuthCore.encode(PinAuthCore.hash("12345678", otherSalt)))
        assertTrue(PinAuthCore.recoveryCode(random).matches(Regex("[A-Z2-9]{24}")))
    }

    @Test fun fiveFailuresLockForFiveMinutes() {
        var now = 0L
        val lockout = PinLockout { now }
        repeat(5) { lockout.record(false) }
        assertFalse(lockout.allowed())
        now += 5 * 60_000
        assertTrue(lockout.allowed())
    }

    @Test fun safeLogRejectsSensitiveFreeFormMessages() {
        assertTrue(SafeLog.message("policy_failure") == "policy_failure")
        assertTrue(SafeLog.message("network_unavailable") == "network_unavailable")
        assertTrue(SafeLog.message("visitor@example.com") == "kiosk_error")
        assertTrue(SafeLog.message("+15551234567") == "kiosk_error")
    }
}
