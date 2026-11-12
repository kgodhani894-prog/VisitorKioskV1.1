package com.eliterf.kiosk.admin

import android.Manifest
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.view.inputmethod.InputMethodInfo
import android.view.inputmethod.InputMethodManager
import android.os.UserManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings

object KioskPolicy {
    const val NATIVE_CAMERA_PACKAGE = "com.mediatek.camera"
    // The SDK used for this build does not expose UserManager.DISALLOW_CAMERA,
    // but Android's device-owner restriction key is stable and documented.
    private const val DISALLOW_CAMERA = "no_camera"
    private val CAMERA_APP_PACKAGES = arrayOf("com.mediatek.camera")

    fun component(context: Context) = ComponentName(context, EliteDeviceAdminReceiver::class.java)

    fun isDeviceOwner(context: Context): Boolean =
        context.getSystemService(DevicePolicyManager::class.java).isDeviceOwnerApp(context.packageName)

    private fun applyApprovedInputMethodPolicy(
        context: Context,
        dpm: DevicePolicyManager,
        admin: ComponentName
    ): Boolean {
        val inputMethodManager = context.getSystemService(InputMethodManager::class.java)
        val enabledInputMethods = inputMethodManager.enabledInputMethodList
            .map(InputMethodInfo::getPackageName)
            .toSet()
        val permittedInputMethods = ApprovedInputMethodPolicy.permittedPackages(enabledInputMethods)
            ?: return false

        if (!dpm.setPermittedInputMethods(admin, permittedInputMethods)) return false

        // Prevent visitors from force-stopping or clearing the approved keyboard
        // through Android app controls. Gboard's own toolbar remains its private
        // UI and is not controllable through the public device-owner API.
        val userControlLocked = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            runCatching {
                dpm.setUserControlDisabledPackages(
                    admin,
                    ApprovedInputMethodPolicy.userControlDisabledPackages()
                )
                dpm.getUserControlDisabledPackages(admin).containsAll(
                    ApprovedInputMethodPolicy.userControlDisabledPackages()
                )
            }.getOrDefault(false)
        } else {
            true
        }

        // Gboard 18.0.5 consumes these typed managed flags through
        // RestrictionsManager. They remove its settings/customization entry
        // points and disable alternate tablet layout modes without replacing
        // Gboard or changing the visitor form's input behavior.
        val managedConfigurationApplied = runCatching {
            val expectedRestrictions = ApprovedInputMethodPolicy.gboardManagedRestrictionValues()
            val restrictions = Bundle().apply {
                expectedRestrictions.forEach { (key, value) ->
                    when (value) {
                        is Boolean -> putBoolean(key, value)
                        is String -> putString(key, value)
                        is Int -> putInt(key, value)
                        else -> error("Unsupported Gboard restriction type: ${value::class.java}")
                    }
                }
                putBundle("preferences", Bundle().apply {
                    ApprovedInputMethodPolicy.gboardManagedPreferenceValues().forEach { (key, value) ->
                        putString(key, value)
                    }
                    ApprovedInputMethodPolicy.gboardManagedIntegerPreferenceValues().forEach { (key, value) ->
                        putInt(key, value)
                    }
                })
            }
            dpm.setApplicationRestrictions(
                admin,
                ApprovedInputMethodPolicy.APPROVED_GBOARD_PACKAGE,
                restrictions
            )
            val appliedRestrictions = dpm.getApplicationRestrictions(
                admin,
                ApprovedInputMethodPolicy.APPROVED_GBOARD_PACKAGE
            )
            val restrictionsApplied = expectedRestrictions.all { (key, expectedValue) ->
                appliedRestrictions.get(key) == expectedValue
            }
            val preferences = appliedRestrictions.getBundle("preferences")
            restrictionsApplied && ApprovedInputMethodPolicy.gboardManagedPreferenceValues().all { (key, expectedValue) ->
                preferences?.getString(key) == expectedValue
            } && ApprovedInputMethodPolicy.gboardManagedIntegerPreferenceValues().all { (key, expectedValue) ->
                preferences?.getInt(key) == expectedValue
            }
        }.getOrDefault(false)

        // Confirm that Android accepted the exact allow-list and that the active
        // keyboard is the approved package before declaring kiosk readiness.
        val appliedInputMethods = dpm.getPermittedInputMethods(admin)
        val activeInputMethod = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.DEFAULT_INPUT_METHOD
        )
        return userControlLocked &&
            managedConfigurationApplied &&
            appliedInputMethods == permittedInputMethods &&
            ApprovedInputMethodPolicy.isApprovedInputMethodId(activeInputMethod)
    }

    fun apply(context: Context): Boolean {
        val dpm = context.getSystemService(DevicePolicyManager::class.java)
        val admin = component(context)
        if (!dpm.isDeviceOwnerApp(context.packageName)) return false
        val inputMethodsApplied = applyApprovedInputMethodPolicy(context, dpm, admin)
        dpm.setLockTaskPackages(admin, arrayOf(context.packageName))
        dpm.setLockTaskFeatures(admin, DevicePolicyManager.LOCK_TASK_FEATURE_NONE)
        val statusBarDisabled = dpm.setStatusBarDisabled(admin, true)
        val keyguardDisabled = dpm.setKeyguardDisabled(admin, true)
        dpm.setScreenCaptureDisabled(admin, true)
        // The device-admin manifest permits camera control, so explicitly clear
        // any camera-disabled state left by provisioning or an older build.
        dpm.setCameraDisabled(admin, false)
        // Do not suspend the manufacturer camera globally. Lock Task still keeps
        // it inaccessible to visitors because only the kiosk package is allowlisted.
        // Leaving it unsuspended is required for reliable administrator maintenance.
        dpm.setPackagesSuspended(admin, CAMERA_APP_PACKAGES, false)
        val cameraGranted = dpm.setPermissionGrantState(
            admin,
            context.packageName,
            Manifest.permission.CAMERA,
            DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED
        )
        listOf(
            UserManager.DISALLOW_FACTORY_RESET,
            UserManager.DISALLOW_SAFE_BOOT,
            UserManager.DISALLOW_ADD_USER,
            UserManager.DISALLOW_ADD_MANAGED_PROFILE,
            UserManager.DISALLOW_ADJUST_VOLUME,
            UserManager.DISALLOW_USB_FILE_TRANSFER,
            UserManager.DISALLOW_MOUNT_PHYSICAL_MEDIA,
            UserManager.DISALLOW_INSTALL_UNKNOWN_SOURCES,
            UserManager.DISALLOW_CONFIG_WIFI,
            UserManager.DISALLOW_CONFIG_BLUETOOTH,
            UserManager.DISALLOW_CREATE_WINDOWS
        ).forEach { dpm.addUserRestriction(admin, it) }
        val debuggableBuild = context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0
        if (com.eliterf.kiosk.ProductionLockdownRules.restrictDebugging(
                com.eliterf.kiosk.KioskSettings(context).productionLockdown(),
                debuggableBuild
            )) {
            dpm.addUserRestriction(admin, UserManager.DISALLOW_DEBUGGING_FEATURES)
        } else {
            // Debug/test installations must recover from any restriction left by an older build.
            dpm.clearUserRestriction(admin, UserManager.DISALLOW_DEBUGGING_FEATURES)
        }
        val home = android.content.IntentFilter().apply {
            addAction(Intent.ACTION_MAIN)
            addCategory(Intent.CATEGORY_HOME)
            addCategory(Intent.CATEGORY_DEFAULT)
        }
        dpm.addPersistentPreferredActivity(admin, home, ComponentName(context, com.eliterf.kiosk.MainActivity::class.java))
        return inputMethodsApplied && cameraGranted && statusBarDisabled && keyguardDisabled &&
            dpm.isLockTaskPermitted(context.packageName)
    }

    fun isFullyLocked(context: Context): Boolean =
        context.getSystemService(android.app.ActivityManager::class.java).lockTaskModeState == android.app.ActivityManager.LOCK_TASK_MODE_LOCKED

    fun prepareNativeCameraCapture(context: Context): Boolean {
        if (!isDeviceOwner(context)) return false
        val dpm = context.getSystemService(DevicePolicyManager::class.java)
        val admin = component(context)
        dpm.setCameraDisabled(admin, false)
        dpm.setPackagesSuspended(admin, CAMERA_APP_PACKAGES, false)
        dpm.setLockTaskPackages(admin, arrayOf(context.packageName, NATIVE_CAMERA_PACKAGE))
        return dpm.isLockTaskPermitted(NATIVE_CAMERA_PACKAGE)
    }

    fun restoreAfterNativeCameraCapture(context: Context): Boolean =
        runCatching { apply(context) }.getOrDefault(false)

    fun relaxMaintenanceRestrictions(context: Context) {
        if (!isDeviceOwner(context)) return
        val dpm = context.getSystemService(DevicePolicyManager::class.java)
        listOf(
            UserManager.DISALLOW_CONFIG_WIFI,
            UserManager.DISALLOW_CONFIG_BLUETOOTH,
            UserManager.DISALLOW_DEBUGGING_FEATURES
        ).forEach {
            dpm.clearUserRestriction(component(context), it)
        }
    }
    fun relaxFullMaintenanceRestrictions(context: Context) {
        if (!isDeviceOwner(context)) return
        val dpm = context.getSystemService(DevicePolicyManager::class.java)
        val admin = component(context)
        // Full maintenance relaxes only restrictions owned by this kiosk policy.
        // Device Owner remains active so the deadline and recovery path survive.
        listOf(
            // This can survive from older provisioning even when
            // setCameraDisabled(false) reports that camera control is clear.
            DISALLOW_CAMERA,
            UserManager.DISALLOW_FACTORY_RESET,
            UserManager.DISALLOW_SAFE_BOOT,
            UserManager.DISALLOW_ADD_USER,
            UserManager.DISALLOW_ADD_MANAGED_PROFILE,
            UserManager.DISALLOW_CONFIG_WIFI,
            UserManager.DISALLOW_CONFIG_BLUETOOTH,
            UserManager.DISALLOW_ADJUST_VOLUME,
            UserManager.DISALLOW_USB_FILE_TRANSFER,
            UserManager.DISALLOW_MOUNT_PHYSICAL_MEDIA,
            UserManager.DISALLOW_INSTALL_UNKNOWN_SOURCES,
            UserManager.DISALLOW_CREATE_WINDOWS,
            UserManager.DISALLOW_DEBUGGING_FEATURES
        ).forEach { dpm.clearUserRestriction(admin, it) }
        // Full maintenance must permit the native camera and the system camera
        // app while keeping Device Owner ownership and recovery policy intact.
        dpm.setCameraDisabled(admin, false)
        dpm.setPackagesSuspended(admin, CAMERA_APP_PACKAGES, false)
        dpm.setStatusBarDisabled(admin, false)
        dpm.setKeyguardDisabled(admin, false)
        dpm.setScreenCaptureDisabled(admin, false)
        dpm.clearPackagePersistentPreferredActivities(admin, context.packageName)
        // Keep the system camera explicitly permitted if the device briefly
        // re-enters a policy-controlled transition while maintenance is open.
        dpm.setLockTaskPackages(admin, arrayOf(context.packageName, NATIVE_CAMERA_PACKAGE))
    }

}
