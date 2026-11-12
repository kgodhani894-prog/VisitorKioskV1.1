package com.eliterf.kiosk

object ConnectivityFailureRules {
    fun failure(networkConnected: Boolean): KioskFailure =
        if (networkConnected) {
            KioskFailure.SERVER_UNAVAILABLE
        } else {
            KioskFailure.NETWORK_UNAVAILABLE
        }
}
