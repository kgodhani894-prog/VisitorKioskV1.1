package com.eliterf.kiosk

object MaintenanceDecision {
    const val TECHNICIAN = "technician"
    const val FULL = "full"

    fun mayEnter(isDeviceOwner: Boolean, exactAlarmAvailable: Boolean, settingsAvailable: Boolean): Boolean =
        isDeviceOwner && exactAlarmAvailable && settingsAvailable

    fun deadlineExpired(deadline: Long, now: Long): Boolean = deadline != 0L && deadline <= now

    fun deadlineRequiresRelock(mode: String, deadline: Long, now: Long): Boolean =
        mode !in setOf(TECHNICIAN, FULL) || deadline <= 0L || deadlineExpired(deadline, now)
}
