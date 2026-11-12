package com.eliterf.kiosk

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class KioskUrlPolicyTest {
    @Test fun acceptsOnlyHttpsServerUrls() {
        assertTrue(KioskUrlPolicy.validServerUrl("https://kiosk.eliterf.company/kiosk"))
        assertFalse(KioskUrlPolicy.validServerUrl("http://kiosk.eliterf.company/kiosk"))
        assertTrue(KioskUrlPolicy.validServerUrl("http://192.168.4.21:8080/kiosk", allowInsecureHttp = true))
        assertFalse(KioskUrlPolicy.validServerUrl("https://user:pass@kiosk.eliterf.company/kiosk"))
    }

    @Test fun matchesOnlyTheApprovedOrigin() {
        val origin = KioskUrlPolicy.origin("https://kiosk.eliterf.company/kiosk")!!
        assertTrue(KioskUrlPolicy.allowedUrl("https://kiosk.eliterf.company/kiosk", origin))
        assertFalse(KioskUrlPolicy.allowedUrl("https://other.example/kiosk", origin))
    }

    @Test fun comparesDnsHostnamesCaseInsensitively() {
        val origin = KioskUrlPolicy.origin("https://LAPTOP-EGB9135J.local:8080/kiosk")!!
        assertTrue(KioskUrlPolicy.allowedUrl("https://laptop-egb9135j.local:8080/static/kiosk.css", origin))
        assertFalse(KioskUrlPolicy.allowedUrl("https://laptop-egb9135j.local:8081/kiosk", origin))
    }

    @Test fun blocksAdminAndExternalPaths() {
        assertTrue(KioskUrlPolicy.allowedPath("/kiosk"))
        assertTrue(KioskUrlPolicy.allowedPath("/api/checkins"))
        assertFalse(KioskUrlPolicy.allowedPath("/admin"))
        assertFalse(KioskUrlPolicy.allowedPath("/api/admin/visits"))
        assertFalse(KioskUrlPolicy.allowedPath("/external"))
    }
}
