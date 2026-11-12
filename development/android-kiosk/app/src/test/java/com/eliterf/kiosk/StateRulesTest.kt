package com.eliterf.kiosk

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import com.eliterf.kiosk.admin.ApprovedInputMethodPolicy

class StateRulesTest {
    @Test fun onlyApprovedGboardIsPermittedWhenAvailable() {
        assertEquals(
            listOf(ApprovedInputMethodPolicy.APPROVED_GBOARD_PACKAGE),
            ApprovedInputMethodPolicy.permittedPackages(
                setOf(ApprovedInputMethodPolicy.APPROVED_GBOARD_PACKAGE, "com.example.alternate")
            )
        )
        assertTrue(ApprovedInputMethodPolicy.isPermitted("com.google.android.inputmethod.latin"))
        assertFalse(ApprovedInputMethodPolicy.isPermitted("com.example.alternate"))
        assertFalse(ApprovedInputMethodPolicy.isPermitted("com.google.android.tts"))
    }

    @Test fun inputMethodPolicyFailsClosedWhenGboardIsUnavailable() {
        assertEquals(null, ApprovedInputMethodPolicy.permittedPackages(setOf("com.example.alternate")))
    }

    @Test fun activeInputMethodMustBeApprovedGboard() {
        assertTrue(
            ApprovedInputMethodPolicy.isApprovedInputMethodId(
                "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME"
            )
        )
        assertFalse(
            ApprovedInputMethodPolicy.isApprovedInputMethodId(
                "com.example.alternate/.AlternateImeService"
            )
        )
        assertFalse(ApprovedInputMethodPolicy.isApprovedInputMethodId(null))
    }

    @Test fun userControlLockTargetsOnlyApprovedGboard() {
        assertEquals(
            listOf(ApprovedInputMethodPolicy.APPROVED_GBOARD_PACKAGE),
            ApprovedInputMethodPolicy.userControlDisabledPackages()
        )
    }

    @Test fun gboardManagedConfigurationBlocksExternalIntents() {
        val restrictions = ApprovedInputMethodPolicy.gboardManagedRestrictionValues()
        assertEquals(true, restrictions[ApprovedInputMethodPolicy.PREVENT_EXTERNAL_INTENTS_KEY])
        assertEquals(
            "sticker;gif_search;translate;clipboard;textediting;share",
            restrictions["access_points_order"]
        )
        assertEquals(
            "sticker;gif_search;translate;clipboard;textediting;share",
            restrictions["config_access_points_order"]
        )
        assertFalse((restrictions["appearance_quick_settings_order"] as String).contains("keyboard_mode"))
        assertFalse((restrictions["appearance_quick_settings_order"] as String).contains("keyboard_resize"))
        assertEquals(false, restrictions["config_settings_access_point"])
        assertEquals(false, restrictions["enable_quick_settings"])
        assertEquals(false, restrictions["show_access_points_customize_ui"])
        assertEquals(false, restrictions["enable_split_keyboard_on_tablet_large"])
        assertEquals(false, restrictions["enable_auto_float_keyboard_in_landscape"])
        assertEquals(false, restrictions["enable_auto_float_keyboard_in_freeform"])
        assertEquals(false, restrictions["enable_auto_float_keyboard_in_multi_window"])
        assertEquals("normal", restrictions["config_default_keyboard_mode"])
        assertEquals(0, restrictions["config_default_access_points_num_on_bar"])
        assertEquals(false, restrictions["enable_voice_widget"])
        assertEquals(
            "sticker;gif_search;translate;clipboard;textediting;share",
            ApprovedInputMethodPolicy.gboardManagedPreferenceValues()["pref_key_access_points_showing_order"]
        )
        assertEquals(0, ApprovedInputMethodPolicy.gboardManagedIntegerPreferenceValues()["access_points_count_on_bar"])
    }

    @Test fun lockedInputWebViewUsesGboardLockHints() {
        assertEquals(
            listOf(
                LockedInputPolicy.NO_SETTINGS_KEY,
                LockedInputPolicy.NO_RESIZE,
                LockedInputPolicy.HIDE_COMPANION_WIDGET,
                LockedInputPolicy.NO_MICROPHONE_KEY
            ),
            LockedInputPolicy.gboardPrivateImeOptions
        )
    }

    @Test fun cameraCalibrationClampsHorizontalStretch() {
        val calibration = CameraCalibration(null, 0, false, 1f, 0f, 0f, 2f).normalized()
        assertEquals(1.5f, calibration.horizontalStretch)
    }
    @Test fun readinessFailsClosed() {
        assertEquals(KioskReadiness.NOT_DEVICE_OWNER, KioskReadinessRules.state(false, true, true, true))
        assertEquals(KioskReadiness.NOT_CONFIGURED, KioskReadinessRules.state(true, false, false, true))
        assertEquals(KioskReadiness.POLICY_FAILURE, KioskReadinessRules.state(true, true, false, true))
        assertEquals(KioskReadiness.READY, KioskReadinessRules.state(true, true, true, true))
    }

    @Test fun idlePolicyResetsOnlyAfterOneMinute() {
        assertFalse(IdlePolicy.shouldReset(59_999, 0))
        assertTrue(IdlePolicy.shouldReset(60_000, 0))
    }

    @Test fun maintenanceWindowHasDurableDeadline() {
        val deadline = MaintenanceWindow.deadline(1_000)
        assertTrue(MaintenanceWindow.active(1_000, deadline))
        assertFalse(MaintenanceWindow.active(deadline, deadline))
    }

    @Test fun maintenanceWindowExpiresAtItsDeadline() {
        val deadline = MaintenanceWindow.deadline(1_000)
        assertTrue(MaintenanceWindow.active(deadline - 1, deadline))
        assertFalse(MaintenanceWindow.active(deadline, deadline))
        assertFalse(MaintenanceWindow.active(deadline + 1, deadline))
    }

    @Test fun fullMaintenanceUsesThirtyMinutesAndTechnicianRemainsTen() {
        assertEquals(10 * 60_000L, MaintenanceWindow.TECHNICIAN_DURATION_MILLIS)
        assertEquals(30 * 60_000L, MaintenanceWindow.FULL_DURATION_MILLIS)
        assertEquals(30 * 60_000L, MaintenanceWindow.fullDeadline(1_000) - 1_000)
    }

    @Test fun maintenanceRelocksWhenModeOrDeadlineIsUnsafe() {
        assertTrue(MaintenanceDecision.deadlineRequiresRelock("", 9_000, 1_000))
        assertTrue(MaintenanceDecision.deadlineRequiresRelock("unknown", 9_000, 1_000))
        assertTrue(MaintenanceDecision.deadlineRequiresRelock(MaintenanceDecision.FULL, 0, 1_000))
        assertTrue(MaintenanceDecision.deadlineRequiresRelock(MaintenanceDecision.FULL, 1_000, 1_000))
        assertFalse(MaintenanceDecision.deadlineRequiresRelock(MaintenanceDecision.FULL, 9_000, 1_000))
    }

    @Test fun maintenanceRefusesUnsafeEntryAndRestoresAfterDeadline() {
        assertFalse(MaintenanceDecision.mayEnter(false, true, true))
        assertFalse(MaintenanceDecision.mayEnter(true, false, true))
        assertFalse(MaintenanceDecision.mayEnter(true, true, false))
        assertTrue(MaintenanceDecision.mayEnter(true, true, true))
        assertTrue(MaintenanceDecision.deadlineExpired(5_000, 5_000))
        assertFalse(MaintenanceDecision.deadlineExpired(5_000, 4_999))
    }

    @Test fun rendererRecoveryRequiresAllSafetyState() {
        assertFalse(RendererRecoveryPolicy.mayRecreate(false, true, true))
        assertFalse(RendererRecoveryPolicy.mayRecreate(true, false, true))
        assertFalse(RendererRecoveryPolicy.mayRecreate(true, true, false))
        assertTrue(RendererRecoveryPolicy.mayRecreate(true, true, true))
    }

    @Test fun onlyTransientConnectivityFailuresRetry() {
        assertTrue(KioskFailure.SERVER_UNAVAILABLE.retryable)
        assertTrue(KioskFailure.NETWORK_UNAVAILABLE.retryable)
        assertFalse(KioskFailure.CERTIFICATE_ERROR.retryable)
        assertFalse(KioskFailure.UNAPPROVED_NAVIGATION.retryable)
    }

    @Test fun retryableFailuresUseProfessionalOfflineMessages() {
        assertEquals("Connection unavailable", KioskFailure.NETWORK_UNAVAILABLE.title)
        assertEquals("Reconnecting to Elite RF", KioskFailure.SERVER_UNAVAILABLE.title)
        assertFalse(KioskFailure.NETWORK_UNAVAILABLE.message.contains("error", ignoreCase = true))
        assertFalse(KioskFailure.SERVER_UNAVAILABLE.message.contains("error", ignoreCase = true))
    }

    @Test fun healthFailureDistinguishesNetworkFromBackendOutage() {
        assertEquals(
            KioskFailure.NETWORK_UNAVAILABLE,
            ConnectivityFailureRules.failure(networkConnected = false)
        )
        assertEquals(
            KioskFailure.SERVER_UNAVAILABLE,
            ConnectivityFailureRules.failure(networkConnected = true)
        )
    }

    @Test fun debuggingIsRestrictedOnlyAfterFinalProductionActivation() {
        assertFalse(ProductionLockdownRules.restrictDebugging(false, debuggableBuild = false))
        assertTrue(ProductionLockdownRules.restrictDebugging(true, debuggableBuild = false))
        assertFalse(ProductionLockdownRules.restrictDebugging(true, debuggableBuild = true))
    }

    @Test fun fiveBrandingTapsOpenAdminGateAndReset() {
        val gesture = AdminTapGesture()
        repeat(4) { assertFalse(gesture.recordTap(1_000L + it * 100)) }
        assertTrue(gesture.recordTap(1_400))
        assertFalse(gesture.recordTap(1_500))
    }

    @Test fun brandingTapWindowExpires() {
        val gesture = AdminTapGesture()
        repeat(4) { assertFalse(gesture.recordTap(1_000L + it * 100)) }
        assertFalse(gesture.recordTap(6_301))
        repeat(3) { assertFalse(gesture.recordTap(6_400L + it * 100)) }
        assertTrue(gesture.recordTap(6_700))
    }

    @Test fun powerGuardOnlyWakesConfiguredLockedVisitorMode() {
        assertTrue(PowerGuardRules.shouldWake(isDeviceOwner = true, maintenanceActive = false, kioskConfigured = true))
        assertFalse(PowerGuardRules.shouldWake(isDeviceOwner = false, maintenanceActive = false, kioskConfigured = true))
        assertFalse(PowerGuardRules.shouldWake(isDeviceOwner = true, maintenanceActive = true, kioskConfigured = true))
        assertFalse(PowerGuardRules.shouldWake(isDeviceOwner = true, maintenanceActive = false, kioskConfigured = false))
    }

    @Test fun managedConfigurationUsesOnlyAValidTrustedKioskUrl() {
        val stored = "https://kiosk.eliterf.company:8080/kiosk"
        val managed = "https://new-kiosk.eliterf.company:8080/kiosk"
        assertEquals(managed, ManagedKioskConfiguration.selectedUrl(managed, stored))
        assertEquals(stored, ManagedKioskConfiguration.selectedUrl("http://unsafe.example/kiosk", stored))
        assertEquals(stored, ManagedKioskConfiguration.selectedUrl("", stored))
    }
}
