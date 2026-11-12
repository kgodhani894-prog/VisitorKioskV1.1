package com.eliterf.kiosk

enum class KioskReadiness { NOT_DEVICE_OWNER, NOT_CONFIGURED, READY, POLICY_FAILURE }

object KioskReadinessRules {
    fun state(deviceOwner: Boolean, configured: Boolean, lockTaskLocked: Boolean, policyApplied: Boolean): KioskReadiness = when {
        !deviceOwner -> KioskReadiness.NOT_DEVICE_OWNER
        !configured -> KioskReadiness.NOT_CONFIGURED
        !policyApplied || !lockTaskLocked -> KioskReadiness.POLICY_FAILURE
        else -> KioskReadiness.READY
    }
}
