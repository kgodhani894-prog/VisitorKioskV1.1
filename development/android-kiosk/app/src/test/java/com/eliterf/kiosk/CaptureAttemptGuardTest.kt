package com.eliterf.kiosk

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureAttemptGuardTest {
    @Test fun duplicateStartsAreRejectedUntilTheAttemptFinishes() {
        val guard = CaptureAttemptGuard()
        val first = guard.begin()

        assertNotNull(first)
        assertNull(guard.begin())
        assertTrue(guard.complete(first!!))
        assertNotNull(guard.begin())
    }

    @Test fun failedAttemptCanBeRetried() {
        val guard = CaptureAttemptGuard()
        val first = guard.begin()!!

        assertTrue(guard.complete(first))
        val retry = guard.begin()
        assertNotNull(retry)
        assertTrue(retry!! > first)
    }

    @Test fun staleCallbacksCannotCompleteAReplacementAttempt() {
        val guard = CaptureAttemptGuard()
        val first = guard.begin()!!
        guard.complete(first)
        val replacement = guard.begin()!!

        assertFalse(guard.complete(first))
        assertTrue(guard.isActive(replacement))
    }

    @Test fun cancellationInvalidatesTheActiveCallbackAndAllowsRetry() {
        val guard = CaptureAttemptGuard()
        val cancelled = guard.begin()!!

        guard.cancel()

        assertFalse(guard.isActive(cancelled))
        val retry = guard.begin()
        assertNotNull(retry)
        assertTrue(retry!! > cancelled)
    }
}
