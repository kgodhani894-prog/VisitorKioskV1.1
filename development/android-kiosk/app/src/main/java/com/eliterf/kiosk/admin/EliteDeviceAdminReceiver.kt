package com.eliterf.kiosk.admin

import android.app.admin.DeviceAdminReceiver
import android.content.Context
import android.content.Intent

class EliteDeviceAdminReceiver : DeviceAdminReceiver() {
    override fun onEnabled(context: Context, intent: Intent) {
        KioskPolicy.apply(context)
    }
    override fun onProfileProvisioningComplete(context: Context, intent: Intent) {
        KioskPolicy.apply(context)
    }
}
