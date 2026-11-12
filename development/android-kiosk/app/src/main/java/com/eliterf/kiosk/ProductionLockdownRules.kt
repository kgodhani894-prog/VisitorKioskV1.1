package com.eliterf.kiosk

object ProductionLockdownRules {
    fun restrictDebugging(activated: Boolean, debuggableBuild: Boolean): Boolean =
        activated && !debuggableBuild
}
