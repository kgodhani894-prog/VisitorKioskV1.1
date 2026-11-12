package com.eliterf.kiosk.admin

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.provider.Settings
import com.eliterf.kiosk.MaintenanceDecision
import com.eliterf.kiosk.MaintenanceWindow

object MaintenanceController {
    private const val PREFS = "maintenance"
    private const val DEADLINE = "deadline"
    private const val MODE = "mode"
    private const val REQUEST = 9014

    fun mode(context: Context): String = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .getString(MODE, "").orEmpty()

    fun active(context: Context): Boolean {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return !MaintenanceDecision.deadlineRequiresRelock(
            prefs.getString(MODE, "").orEmpty(),
            prefs.getLong(DEADLINE, 0L),
            System.currentTimeMillis()
        )
    }

    fun enforceDeadline(context: Context): Boolean {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val mode = prefs.getString(MODE, "").orEmpty()
        val deadline = prefs.getLong(DEADLINE, 0L)
        if (mode.isNotEmpty() && MaintenanceDecision.deadlineRequiresRelock(mode, deadline, System.currentTimeMillis())) {
            finish(context)
            return false
        }
        return mode.isNotEmpty() && deadline > 0L
    }

    fun enter(context: Context): Boolean = enterMode(context, MaintenanceDecision.TECHNICIAN)

    fun enterFull(context: Context): Boolean = enterMode(context, MaintenanceDecision.FULL)

    private fun enterMode(context: Context, mode: String): Boolean {
        val alarmManager = context.getSystemService(AlarmManager::class.java)
        val settingsPackage = context.packageManager.resolveActivity(Intent(Settings.ACTION_SETTINGS), 0)
            ?.activityInfo?.packageName.orEmpty()
        val exactAlarmAvailable = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || alarmManager.canScheduleExactAlarms()
        val requiredTargetAvailable = mode == MaintenanceDecision.FULL || settingsPackage.isNotBlank()
        if (!MaintenanceDecision.mayEnter(KioskPolicy.isDeviceOwner(context), exactAlarmAvailable, requiredTargetAvailable)) return false
        if (!KioskPolicy.isFullyLocked(context)) return false

        val deadline = if (mode == MaintenanceDecision.FULL) {
            MaintenanceWindow.fullDeadline(System.currentTimeMillis())
        } else {
            MaintenanceWindow.deadline(System.currentTimeMillis())
        }
        val pending = PendingIntent.getBroadcast(
            context,
            REQUEST,
            Intent(context, MaintenanceReceiver::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        runCatching { alarmManager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, deadline, pending) }
            .getOrElse {
                alarmManager.cancel(pending)
                return false
            }

        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!prefs.edit().putString(MODE, mode).putLong(DEADLINE, deadline).commit()) {
            alarmManager.cancel(pending)
            return false
        }
        runCatching {
            if (mode == MaintenanceDecision.FULL) {
                KioskPolicy.relaxFullMaintenanceRestrictions(context)
            } else {
                val dpm = context.getSystemService(android.app.admin.DevicePolicyManager::class.java)
                dpm.setLockTaskPackages(KioskPolicy.component(context), arrayOf(context.packageName, settingsPackage))
                KioskPolicy.relaxMaintenanceRestrictions(context)
            }
        }.getOrElse {
            alarmManager.cancel(pending)
            prefs.edit().remove(MODE).remove(DEADLINE).apply()
            KioskPolicy.apply(context)
            return false
        }
        return true
    }

    fun finish(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .remove(MODE).remove(DEADLINE).commit()
        val pending = PendingIntent.getBroadcast(
            context,
            REQUEST,
            Intent(context, MaintenanceReceiver::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        context.getSystemService(AlarmManager::class.java).cancel(pending)
        KioskPolicy.apply(context)
    }
}
