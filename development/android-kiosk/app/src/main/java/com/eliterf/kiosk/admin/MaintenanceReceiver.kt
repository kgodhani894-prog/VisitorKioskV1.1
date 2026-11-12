package com.eliterf.kiosk.admin

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class MaintenanceReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        MaintenanceController.finish(context)
        context.startActivity(Intent(context, com.eliterf.kiosk.MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        })
    }
}
