package com.eliterf.kiosk

object MaintenanceWindow {
    const val TECHNICIAN_DURATION_MILLIS = 10 * 60_000L
    const val FULL_DURATION_MILLIS = 30 * 60_000L
    const val DURATION_MILLIS = TECHNICIAN_DURATION_MILLIS
    fun deadline(start: Long): Long = start + TECHNICIAN_DURATION_MILLIS
    fun fullDeadline(start: Long): Long = start + FULL_DURATION_MILLIS
    fun active(now: Long, deadline: Long): Boolean = now < deadline
}
