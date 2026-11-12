package com.eliterf.kiosk

/** Serializes one camera attempt and invalidates callbacks after cancellation. */
internal class CaptureAttemptGuard {
    private var nextGeneration = 0L
    private var activeGeneration: Long? = null

    fun begin(): Long? {
        if (activeGeneration != null) return null
        val generation = ++nextGeneration
        activeGeneration = generation
        return generation
    }

    fun isActive(generation: Long): Boolean = activeGeneration == generation

    fun isBusy(): Boolean = activeGeneration != null

    fun complete(generation: Long): Boolean {
        if (!isActive(generation)) return false
        activeGeneration = null
        return true
    }

    fun cancel() {
        activeGeneration = null
        nextGeneration++
    }
}
