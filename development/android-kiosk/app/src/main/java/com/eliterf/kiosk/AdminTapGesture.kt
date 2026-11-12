package com.eliterf.kiosk

class AdminTapGesture(
    private val requiredTaps: Int = 5,
    private val windowMillis: Long = 5_000L
) {
    private var firstTapAt = 0L
    private var tapCount = 0

    fun recordTap(now: Long): Boolean {
        if (tapCount == 0 || now - firstTapAt > windowMillis) {
            firstTapAt = now
            tapCount = 0
        }
        tapCount++
        if (tapCount < requiredTaps) return false
        reset()
        return true
    }

    fun reset() {
        firstTapAt = 0L
        tapCount = 0
    }
}
