package com.eliterf.kiosk

object IdlePolicy {
    const val ABANDONED_SESSION_MILLIS = 60_000L
    fun shouldReset(now: Long, lastInteraction: Long): Boolean = now - lastInteraction >= ABANDONED_SESSION_MILLIS
}
