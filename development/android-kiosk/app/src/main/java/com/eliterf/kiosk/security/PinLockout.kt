package com.eliterf.kiosk.security

class PinLockout(private val clock: () -> Long) {
    private var failures = 0
    private var lockedUntil = 0L

    fun allowed(): Boolean = clock() >= lockedUntil

    fun record(success: Boolean) {
        if (!allowed()) return
        if (success) failures = 0 else {
            failures++
            if (failures >= 5) { lockedUntil = clock() + 5 * 60_000; failures = 0 }
        }
    }
}
