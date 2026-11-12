package com.eliterf.kiosk

object PowerGuardRules {
    fun shouldWake(
        isDeviceOwner: Boolean,
        maintenanceActive: Boolean,
        kioskConfigured: Boolean
    ): Boolean = isDeviceOwner && !maintenanceActive && kioskConfigured
}
