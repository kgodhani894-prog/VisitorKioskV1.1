package com.eliterf.kiosk

import android.Manifest
import android.app.Activity
import android.app.admin.DevicePolicyManager
import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.pm.ApplicationInfo
import android.graphics.Color
import android.graphics.BitmapFactory
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.view.Gravity
import android.view.View
import android.view.Window
import android.view.WindowManager
import android.view.MotionEvent
import android.net.Uri
import android.util.Base64
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.ProgressBar
import android.widget.TextView
import android.text.InputType
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.core.content.FileProvider
import com.eliterf.kiosk.admin.KioskPolicy
import com.eliterf.kiosk.admin.MaintenanceController
import com.eliterf.kiosk.MaintenanceDecision
import com.eliterf.kiosk.security.PinAuth
import com.eliterf.kiosk.RendererRecoveryPolicy
import java.io.File
import org.json.JSONObject

class MainActivity : Activity() {
    companion object {
        const val NATIVE_CAPTURE_REQUEST = 7201
        const val CAMERAX_CAPTURE_REQUEST = 7202
        const val NATIVE_CAPTURE_PREFS = "native_capture"
        const val NATIVE_CAPTURE_PATH = "path"
        const val NATIVE_CAPTURE_URI = "uri"
        const val NATIVE_CAPTURE_MODE = "mode"
        private const val NATIVE_CAPTURE_MODE_CAMERAX = "camerax"
        private const val NATIVE_CAPTURE_MODE_LEGACY = "legacy"
        const val NATIVE_CAPTURE_REVIEW_PATH = "review_path"
        const val NATIVE_CAPTURE_PENDING = "pending"
        const val NATIVE_CAPTURE_MAX_BYTES = 10 * 1024 * 1024
        private const val NATIVE_CAPTURE_GRANT_FLAGS =
            Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
        private const val NATIVE_CAPTURE_CHUNK_BYTES = 49_152

        internal fun discardPendingNativeCapture(context: Context) {
            val prefs = context.getSharedPreferences(NATIVE_CAPTURE_PREFS, Context.MODE_PRIVATE)
            prefs.getString(NATIVE_CAPTURE_PATH, null)?.let(::File)?.delete()
            prefs.getString(NATIVE_CAPTURE_REVIEW_PATH, null)?.let(::File)?.delete()
            prefs.getString(NATIVE_CAPTURE_URI, null)?.let { uri ->
                runCatching {
                    val parsed = Uri.parse(uri)
                    context.revokeUriPermission(parsed, NATIVE_CAPTURE_GRANT_FLAGS)
                    context.contentResolver.delete(parsed, null, null)
                }
            }
            prefs.edit().clear().commit()
        }
    }
    private lateinit var settings: KioskSettings
    private lateinit var pinAuth: PinAuth
    private lateinit var statusStore: KioskStatusStore
    private var webView: WebView? = null
    private var webContainer: FrameLayout? = null
    private var nativeCamera: NativeCameraController? = null
    private val handler = Handler(Looper.getMainLooper())
    private var idleCheck: Runnable? = null
    private var loadTimeout: Runnable? = null
    private var healthCheck: Runnable? = null
    private var maintenanceCountdown: Runnable? = null
    private var healthCheckInFlight = false
    private val rehideSystemUi = Runnable { hideSystemBarsNow() }
    private var systemUiGuardInstalled = false
    private var screenOffReceiverRegistered = false
    private var networkCallbackRegistered = false
    private var managedConfigReceiverRegistered = false
    private var waitingForReconnect = false
    private var cameraDiagnosticsRequested = false
    private var cameraCalibrationRequested = false
    private var cameraCalibrationActive = false
    private var nativeCapturePending = false
    private var nativeCaptureUri: Uri? = null
    private var nativeCaptureFile: File? = null
    private var nativeReviewFile: File? = null
    private var pageReady = false
    private var retryGeneration = 0
    private var lastInteraction = System.currentTimeMillis()
    private val adminTapGesture = AdminTapGesture()
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            handler.post {
                if (!waitingForReconnect || !KioskPolicy.isFullyLocked(this@MainActivity)) return@post
                retryNow()
            }
        }
    }
    private val screenOffReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != Intent.ACTION_SCREEN_OFF) return
            val configured = pinAuth.configured() && settings.url() != null
            if (!PowerGuardRules.shouldWake(
                    KioskPolicy.isDeviceOwner(context),
                    MaintenanceController.active(context),
                    configured
                )
            ) return
            @Suppress("DEPRECATION")
            context.getSystemService(PowerManager::class.java)
                .newWakeLock(
                    PowerManager.FULL_WAKE_LOCK or
                        PowerManager.ACQUIRE_CAUSES_WAKEUP or
                        PowerManager.ON_AFTER_RELEASE,
                    "$packageName:visitor-kiosk-power-guard"
                )
                .acquire(1_500)
        }
    }
    private val managedConfigReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != Intent.ACTION_APPLICATION_RESTRICTIONS_CHANGED) return
            if (settings.applyManagedConfiguration() && KioskPolicy.isFullyLocked(this@MainActivity)) {
                clearVisitorSession()
                buildWebView()
                loadConfiguredKiosk()
            }
        }
    }

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or WindowManager.LayoutParams.FLAG_SECURE)
        settings = KioskSettings(this)
        settings.applyManagedConfiguration()
        pinAuth = PinAuth(this)
        statusStore = KioskStatusStore(this)
        restoreNativeCaptureState()
        registerPowerGuard()
        registerNetworkRecovery()
        registerManagedConfiguration()
        installSystemUiGuard()
        hideSystemUi()
        if (MaintenanceController.enforceDeadline(this)) {
            if (MaintenanceController.mode(this) == MaintenanceDecision.FULL) {
                KioskPolicy.relaxFullMaintenanceRestrictions(this)
                showFullMaintenancePanel()
            }
            else showStatus("Maintenance mode active", "The kiosk will lock again automatically when the ten-minute maintenance window ends.")
            return
        }
        if (!KioskPolicy.isDeviceOwner(this)) showStatus("Administrator setup required", "Provision this tablet as Device Owner before using visitor mode.")
        else if (!pinAuth.configured() || settings.url() == null) startSecureSetup()
        else startKiosk()
    }

    override fun onResume() {
        super.onResume()
        settings.applyManagedConfiguration()
        if (MaintenanceController.enforceDeadline(this)) {
            if (MaintenanceController.mode(this) == MaintenanceDecision.FULL) {
                KioskPolicy.relaxFullMaintenanceRestrictions(this)
                showFullMaintenancePanel()
            }
            return
        }
        hideSystemUi()
        if (KioskPolicy.isDeviceOwner(this)) {
            if (!runCatching { KioskPolicy.apply(this) }.getOrDefault(false)) {
                showStatus("Security policy failure", "Android policy application failed; visitor mode is disabled.")
            } else if (!startLockTaskSafely()) {
                showStatus("Security policy failure", "Android is no longer in true Lock Task Mode.")
            }
        }
    }

    internal fun launchNativeCameraCapture(payloadJson: String): Boolean {
        if (nativeCapturePending || !KioskPolicy.isDeviceOwner(this)) return false
        if (!KioskPolicy.apply(this)) return false
        val cameraDir = File(cacheDir, "camera").apply { mkdirs() }
        val output = runCatching { File.createTempFile("camerax_capture_", ".jpg", cameraDir) }.getOrNull() ?: return false
        nativeCapturePending = true
        nativeCaptureFile = output
        nativeCaptureUri = null
        val stored = getSharedPreferences(NATIVE_CAPTURE_PREFS, MODE_PRIVATE).edit()
            .putBoolean(NATIVE_CAPTURE_PENDING, true)
            .putString(NATIVE_CAPTURE_PATH, output.absolutePath)
            .putString(NATIVE_CAPTURE_MODE, NATIVE_CAPTURE_MODE_CAMERAX)
            .putString("payload", payloadJson)
            .commit()
        if (!stored) {
            clearNativeCaptureState()
            output.delete()
            return false
        }
        return runCatching {
            startActivityForResult(
                Intent(this, CameraXCaptureActivity::class.java)
                    .putExtra(CameraXCaptureActivity.EXTRA_OUTPUT_PATH, output.absolutePath),
                CAMERAX_CAPTURE_REQUEST
            )
            true
        }.getOrElse {
            clearNativeCaptureState()
            output.delete()
            KioskPolicy.restoreAfterNativeCameraCapture(this)
            false
        }
    }

    // Retained as the rollback path for the previously verified MediaTek flow.
    internal fun launchLegacyNativeCameraCapture(payloadJson: String): Boolean {
        if (nativeCapturePending || !KioskPolicy.isDeviceOwner(this)) return false
        val resolved = packageManager.resolveActivity(
            Intent(MediaStore.ACTION_IMAGE_CAPTURE),
            PackageManager.MATCH_DEFAULT_ONLY
        )?.activityInfo ?: return false
        if (resolved.packageName != KioskPolicy.NATIVE_CAMERA_PACKAGE) return false
        val cameraDir = File(cacheDir, "camera").apply { mkdirs() }
        val output = runCatching { File.createTempFile("capture_", ".jpg", cameraDir) }.getOrNull() ?: return false
        val privateUri = runCatching {
            FileProvider.getUriForFile(this, "$packageName.fileprovider", output)
        }.getOrNull() ?: run {
            output.delete()
            return false
        }
        val uri = if (resolved.packageName == KioskPolicy.NATIVE_CAMERA_PACKAGE) runCatching {
            val values = android.content.ContentValues().apply {
                put(MediaStore.Images.Media.DISPLAY_NAME, output.name)
                put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
                put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/Elite RF/.pending")
                // MESWAO's camera process cannot write an IS_PENDING item. The
                // randomized file is placed in a hidden temporary directory and
                // deleted as soon as the JPEG is copied into app-private cache.
                put(MediaStore.Images.Media.IS_PENDING, 0)
            }
            contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
        }.getOrNull() else privateUri
        if (uri == null) {
            output.delete()
            return false
        }
        if (!KioskPolicy.prepareNativeCameraCapture(this)) {
            output.delete()
            return false
        }
        runCatching {
            grantUriPermission(resolved.packageName, uri, NATIVE_CAPTURE_GRANT_FLAGS)
        }.getOrElse {
            contentResolver.delete(uri, null, null)
            output.delete()
            KioskPolicy.restoreAfterNativeCameraCapture(this)
            return false
        }
        nativeCapturePending = true
        nativeCaptureFile = output
        nativeCaptureUri = uri
        val stored = getSharedPreferences(NATIVE_CAPTURE_PREFS, MODE_PRIVATE).edit()
            .putBoolean(NATIVE_CAPTURE_PENDING, true)
            .putString(NATIVE_CAPTURE_PATH, output.absolutePath)
            .putString(NATIVE_CAPTURE_MODE, NATIVE_CAPTURE_MODE_LEGACY)
            .putString(NATIVE_CAPTURE_URI, uri.toString())
            .putString("payload", payloadJson)
            .commit()
        if (!stored) {
            clearNativeCaptureState()
            KioskPolicy.restoreAfterNativeCameraCapture(this)
            return false
        }
        val captureIntent = Intent(MediaStore.ACTION_IMAGE_CAPTURE).apply {
            component = android.content.ComponentName(resolved.packageName, resolved.name)
            putExtra(MediaStore.EXTRA_OUTPUT, uri)
            // The standard capture contract does not require front-camera
            // selection. These hints are harmless on cameras that ignore them;
            // MESWAO relies on its remembered lens selection.
            putExtra("android.intent.extras.CAMERA_FACING", 1)
            putExtra("android.intent.extra.USE_FRONT_CAMERA", true)
            // The MESWAO camera reopens the output through the intent data URI.
            setDataAndType(uri, "image/jpeg")
            clipData = ClipData.newRawUri("Elite RF camera output", uri)
            addFlags(
                NATIVE_CAPTURE_GRANT_FLAGS
            )
        }
        return runCatching {
            startActivityForResult(captureIntent, NATIVE_CAPTURE_REQUEST)
            true
        }.getOrElse {
            revokeUriPermission(uri, NATIVE_CAPTURE_GRANT_FLAGS)
            contentResolver.delete(uri, null, null)
            clearNativeCaptureState()
            KioskPolicy.restoreAfterNativeCameraCapture(this)
            false
        }
    }

    internal fun pendingNativeCapturePayload(): String? {
        if (!nativeCapturePending) return null
        return getSharedPreferences(NATIVE_CAPTURE_PREFS, MODE_PRIVATE).getString("payload", null)
    }

    internal fun resetNativeCaptureForRetry() {
        nativeCaptureUri?.let {
            revokeUriPermission(it, NATIVE_CAPTURE_GRANT_FLAGS)
            contentResolver.delete(it, null, null)
        }
        nativeCaptureFile?.delete()
        clearNativeCaptureState()
        KioskPolicy.restoreAfterNativeCameraCapture(this)
    }

    internal fun capturedNativePhotoSize(): Int = nativeReviewFile
        ?.takeIf(::isValidNativeCapture)
        ?.length()
        ?.coerceAtMost(Int.MAX_VALUE.toLong())
        ?.toInt() ?: 0

    internal fun capturedNativePhotoChunk(offset: Int, requestedLength: Int): String? {
        val file = nativeReviewFile?.takeIf(::isValidNativeCapture) ?: return null
        if (offset < 0 || requestedLength <= 0 || offset >= file.length()) return null
        val length = minOf(requestedLength, NATIVE_CAPTURE_CHUNK_BYTES, (file.length() - offset).toInt())
        val bytes = ByteArray(length)
        return runCatching {
            java.io.RandomAccessFile(file, "r").use { input ->
                input.seek(offset.toLong())
                input.readFully(bytes)
            }
            Base64.encodeToString(bytes, Base64.NO_WRAP)
        }.getOrNull()
    }

    internal fun discardCapturedNativePhoto() {
        nativeReviewFile?.delete()
        nativeReviewFile = null
        getSharedPreferences(NATIVE_CAPTURE_PREFS, MODE_PRIVATE).edit()
            .remove(NATIVE_CAPTURE_REVIEW_PATH)
            .commit()
    }

    private fun restoreNativeCaptureState() {
        val prefs = getSharedPreferences(NATIVE_CAPTURE_PREFS, MODE_PRIVATE)
        // A review image is intentionally not resumed after a process restart.
        // Remove it before loading the next visitor to prevent data leakage.
        prefs.getString(NATIVE_CAPTURE_REVIEW_PATH, null)?.let(::File)?.delete()
        if (prefs.contains(NATIVE_CAPTURE_REVIEW_PATH)) {
            prefs.edit().remove(NATIVE_CAPTURE_REVIEW_PATH).commit()
        }
        if (!prefs.getBoolean(NATIVE_CAPTURE_PENDING, false)) return
        val path = prefs.getString(NATIVE_CAPTURE_PATH, null)
        val file = path?.let(::File)
        val isCameraX = prefs.getString(NATIVE_CAPTURE_MODE, null) == NATIVE_CAPTURE_MODE_CAMERAX
        val cameraDir = File(cacheDir, "camera").canonicalFile
        val validCameraXPath = isCameraX && file != null && runCatching {
            file.canonicalFile.parentFile == cameraDir &&
                file.name.startsWith("camerax_capture_") && file.extension == "jpg"
        }.getOrDefault(false)
        if (file == null || (!file.isFile && !validCameraXPath)) {
            prefs.edit().clear().commit()
            return
        }
        nativeCapturePending = true
        nativeCaptureFile = file
        nativeCaptureUri = if (isCameraX) null else runCatching {
            prefs.getString(NATIVE_CAPTURE_URI, null)?.let(Uri::parse)
        }.getOrNull()
        if (isCameraX) return
        if (nativeCaptureUri == null) {
            prefs.getString(NATIVE_CAPTURE_URI, null)?.let { uri ->
                runCatching {
                    val parsed = Uri.parse(uri)
                    revokeUriPermission(parsed, NATIVE_CAPTURE_GRANT_FLAGS)
                    contentResolver.delete(parsed, null, null)
                }
            }
            file.delete()
            prefs.edit().clear().commit()
            nativeCapturePending = false
            nativeCaptureFile = null
        }
    }

    @Suppress("DEPRECATION")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == CAMERAX_CAPTURE_REQUEST) {
            handleCameraXResult(resultCode, data)
            return
        }
        if (requestCode != NATIVE_CAPTURE_REQUEST) return
        val uri = nativeCaptureUri
        val file = nativeCaptureFile
        val photoReady = resultCode == RESULT_OK && uri != null && file != null &&
            materializeNativeCapture(uri, file) && isValidNativeCapture(file)
        uri?.let {
            revokeUriPermission(it, NATIVE_CAPTURE_GRANT_FLAGS)
            contentResolver.delete(it, null, null)
        }
        if (photoReady) {
            nativeReviewFile = file
            getSharedPreferences(NATIVE_CAPTURE_PREFS, MODE_PRIVATE).edit()
                .putString(NATIVE_CAPTURE_REVIEW_PATH, file?.absolutePath)
                .commit()
        } else file?.delete()
        clearNativeCaptureState()
        val policyRestored = KioskPolicy.restoreAfterNativeCameraCapture(this)
        if (!policyRestored) {
            showStatus("Security policy failure", "The kiosk could not restore its locked state after camera capture.")
            return
        }
        startLockTaskSafely()
        val callback = when {
            photoReady -> "window.eliteRfNativeCaptureReady?.();"
            resultCode == RESULT_CANCELED -> "window.eliteRfNativeCaptureCancelled?.();"
            else -> "window.eliteRfNativeCaptureError?.('Photo could not be captured. Please try again.');"
        }
        webView?.post { webView?.evaluateJavascript(callback, null) }
    }

    private fun handleCameraXResult(resultCode: Int, data: Intent?) {
        // Activity transitions can recreate MainActivity; recover the pending
        // app-private output from durable state before consuming the result.
        val prefs = getSharedPreferences(NATIVE_CAPTURE_PREFS, MODE_PRIVATE)
        val file = nativeCaptureFile
            ?: prefs.getString(NATIVE_CAPTURE_PATH, null)?.let(::File)
            ?: data?.getStringExtra(CameraXCaptureActivity.EXTRA_OUTPUT_PATH)?.let(::File)
        val fileValid = file?.let(::isValidNativeCapture) == true
        val photoReady = resultCode == RESULT_OK && fileValid
        android.util.Log.i("EliteRfKiosk", "CameraX result=$resultCode file=${file?.absolutePath} valid=$fileValid")
        if (photoReady) {
            nativeReviewFile = file
            getSharedPreferences(NATIVE_CAPTURE_PREFS, MODE_PRIVATE).edit()
                .putString(NATIVE_CAPTURE_REVIEW_PATH, file?.absolutePath)
                .commit()
        } else file?.delete()
        clearNativeCaptureState()
        val policyRestored = KioskPolicy.restoreAfterNativeCameraCapture(this)
        if (!policyRestored) {
            showStatus("Security policy failure", "The kiosk could not restore its locked state after camera capture.")
            return
        }
        startLockTaskSafely()
        val callback = when {
            photoReady -> "window.eliteRfNativeCaptureReady?.();"
            resultCode == RESULT_CANCELED -> "window.eliteRfNativeCaptureCancelled?.();"
            else -> "window.eliteRfNativeCaptureError?.('Photo could not be captured. Please try again.');"
        }
        webView?.post { webView?.evaluateJavascript(callback, null) }
    }

    private fun isValidNativeCapture(file: File): Boolean {
        if (!file.isFile || file.length() <= 0L || file.length() > NATIVE_CAPTURE_MAX_BYTES) return false
        val bytes = runCatching { file.readBytes() }.getOrNull() ?: return false
        if (bytes.size < 4 || bytes[0] != 0xFF.toByte() || bytes[1] != 0xD8.toByte() ||
            bytes[bytes.lastIndex - 1] != 0xFF.toByte() || bytes.last() != 0xD9.toByte()) return false
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        return bounds.outWidth >= 160 && bounds.outHeight >= 120
    }

    private fun materializeNativeCapture(uri: Uri, file: File): Boolean = runCatching {
        contentResolver.openInputStream(uri)?.use { input ->
            file.outputStream().use { output -> input.copyTo(output) }
        } ?: return false
        true
    }.getOrDefault(false)

    private fun clearNativeCaptureState() {
        nativeCapturePending = false
        nativeCaptureUri = null
        nativeCaptureFile = null
        getSharedPreferences(NATIVE_CAPTURE_PREFS, MODE_PRIVATE).edit()
            .remove(NATIVE_CAPTURE_PENDING)
            .remove(NATIVE_CAPTURE_PATH)
            .remove(NATIVE_CAPTURE_URI)
            .remove(NATIVE_CAPTURE_MODE)
            .remove("payload")
            .commit()
    }

    private fun registerManagedConfiguration() {
        if (managedConfigReceiverRegistered) return
        val filter = IntentFilter(Intent.ACTION_APPLICATION_RESTRICTIONS_CHANGED)
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(managedConfigReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("DEPRECATION")
            registerReceiver(managedConfigReceiver, filter)
        }
        managedConfigReceiverRegistered = true
    }

    private fun startKiosk() {
        val url = settings.url() ?: return showSetup()
        if (!settings.validUrl(url)) return showStatus("Security configuration error", "The kiosk URL must use trusted HTTPS.")
        val policyReady = runCatching { KioskPolicy.apply(this) }.getOrDefault(false)
        if (!policyReady) return showStatus("Security policy failure", "This tablet is not ready for locked visitor mode.")
        cancelScheduledWork()
        if (!startLockTaskSafely()) {
            return showStatus("Security policy failure", "Android did not enter true Lock Task Mode.")
        }
        buildWebView()
        loadConfiguredKiosk()
        scheduleIdleCheck()
    }

    private fun buildWebView() {
        nativeCamera?.stop()
        nativeCamera = null
        webView?.let { previous ->
            (previous.parent as? android.view.ViewGroup)?.removeView(previous)
            previous.destroy()
        }
        val current = LockedInputWebView(this)
        webView = current
        current.setBackgroundColor(Color.TRANSPARENT)
        WebView.setWebContentsDebuggingEnabled(false)
        current.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            setSupportMultipleWindows(false)
            javaScriptCanOpenWindowsAutomatically = false
            allowFileAccess = false
            allowContentAccess = false
            setGeolocationEnabled(false)
            mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW
            savePassword = false
            safeBrowsingEnabled = true
            userAgentString = "$userAgentString EliteRfVisitorKiosk/1.0"
        }
        CookieManager.getInstance().setAcceptThirdPartyCookies(current, false)
        current.webViewClient = KioskWebViewClient(settings, ::handleWebFailure, ::recoverRenderer, ::restoreWebView)
        current.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: android.webkit.PermissionRequest) {
                val origin = request.origin.toString().trimEnd('/')
                val allowed = origin == settings.approvedOrigin() && request.resources.contentEquals(arrayOf(android.webkit.PermissionRequest.RESOURCE_VIDEO_CAPTURE))
                if (allowed) request.grant(arrayOf(android.webkit.PermissionRequest.RESOURCE_VIDEO_CAPTURE)) else request.deny()
            }
        }
        val webContainer = FrameLayout(this)
        webContainer.addView(current, FrameLayout.LayoutParams(-1, -1))
        this.webContainer = webContainer
        val cameraController = NativeCameraController(this, webContainer, current)
        nativeCamera = cameraController
        current.addJavascriptInterface(cameraController, "EliteRfNativeCamera")
        current.setDownloadListener { _, _, _, _, _ -> }
        current.setOnTouchListener { _, event ->
            lastInteraction = System.currentTimeMillis()
            if (event.action == MotionEvent.ACTION_UP && event.x < current.width * 0.35f && event.y < current.height * 0.35f) {
                if (adminTapGesture.recordTap(System.currentTimeMillis())) showAdminGate()
            }
            false
        }
        setContentView(webContainer)
    }

    private fun scheduleIdleCheck() {
        idleCheck?.let(handler::removeCallbacks)
        idleCheck = object : Runnable {
            override fun run() {
                if (webView == null) return
                if (!cameraCalibrationActive && System.currentTimeMillis() - lastInteraction > 60_000) {
                    clearVisitorSession()
                    loadConfiguredKiosk()
                    lastInteraction = System.currentTimeMillis()
                }
                handler.postDelayed(this, 5_000)
            }
        }
        handler.postDelayed(idleCheck!!, 5_000)
    }

    private fun retryLater() {
        val generation = ++retryGeneration
        handler.postDelayed({
            if (generation == retryGeneration) probeForReconnect(generation)
        }, 5_000)
    }

    private fun retryNow() {
        val generation = ++retryGeneration
        probeForReconnect(generation)
    }

    private fun probeForReconnect(generation: Int) {
        val url = settings.url() ?: return
        Thread {
            val reachable = serverReachable(url)
            runOnUiThread {
                if (generation != retryGeneration || !waitingForReconnect) return@runOnUiThread
                if (reachable && KioskPolicy.isFullyLocked(this)) {
                    buildWebView()
                    loadConfiguredKiosk()
                } else {
                    retryLater()
                }
            }
        }.start()
    }

    private fun loadConfiguredKiosk() {
        val current = webView ?: return
        val url = settings.url() ?: return
        val diagnostics = cameraDiagnosticsRequested
        cameraDiagnosticsRequested = false
        val calibration = cameraCalibrationRequested
        cameraCalibrationRequested = false
        cameraCalibrationActive = calibration
        pageReady = false
        waitingForReconnect = false
        loadTimeout?.let(handler::removeCallbacks)
        val fragment = when {
            diagnostics -> "#camera-debug"
            calibration -> "#camera-calibration"
            else -> ""
        }
        current.loadUrl("$url$fragment")
        loadTimeout = Runnable {
            if (webView !== current || pageReady) return@Runnable
            handleWebFailure(KioskFailure.NETWORK_UNAVAILABLE)
            current.stopLoading()
        }.also { handler.postDelayed(it, 12_000) }
    }

    private fun handleWebFailure(failure: KioskFailure) {
        if (failure.retryable && waitingForReconnect) return
        loadTimeout?.let(handler::removeCallbacks)
        loadTimeout = null
        pageReady = false
        statusStore.record(failure.code)
        waitingForReconnect = failure.retryable
        showStatus(failure.title, failure.message, failure.retryable)
        if (failure.retryable) retryLater()
    }

    private fun restoreWebView(readyWebView: WebView) {
        if (webView !== readyWebView || !KioskPolicy.isFullyLocked(this)) return
        readyWebView.evaluateJavascript(
            "Boolean(document.getElementById('checkinForm'))"
        ) { result ->
            if (result != "true" || webView !== readyWebView || !KioskPolicy.isFullyLocked(this)) {
                return@evaluateJavascript
            }
            loadTimeout?.let(handler::removeCallbacks)
            loadTimeout = null
            pageReady = true
            waitingForReconnect = false
            statusStore.clear()
            setContentView(webContainer ?: readyWebView)
            lastInteraction = System.currentTimeMillis()
            scheduleIdleCheck()
            scheduleHealthCheck()
        }
    }

    internal fun onCameraCalibrationFinished() {
        cameraCalibrationActive = false
        lastInteraction = System.currentTimeMillis()
    }

    private fun scheduleHealthCheck() {
        healthCheck?.let(handler::removeCallbacks)
        healthCheck = Runnable { checkServerAvailability() }
        handler.postDelayed(healthCheck!!, 10_000)
    }

    private fun checkServerAvailability() {
        if (healthCheckInFlight || !pageReady) return
        val url = settings.url() ?: return
        val generation = retryGeneration
        healthCheckInFlight = true
        Thread {
            val reachable = serverReachable(url)
            runOnUiThread {
                healthCheckInFlight = false
                if (generation != retryGeneration || !pageReady) return@runOnUiThread
                if (reachable) {
                    scheduleHealthCheck()
                } else {
                    handleWebFailure(
                        ConnectivityFailureRules.failure(networkIsConnected())
                    )
                }
            }
        }.start()
    }

    private fun serverReachable(url: String): Boolean {
        return try {
            val separator = if (url.contains("?")) "&" else "?"
            val probeUrl = "$url${separator}_kiosk_health=${System.currentTimeMillis()}"
            val connection = java.net.URL(probeUrl).openConnection() as java.net.HttpURLConnection
            try {
                connection.connectTimeout = 5_000
                connection.readTimeout = 5_000
                connection.requestMethod = "GET"
                connection.useCaches = false
                connection.setRequestProperty("Cache-Control", "no-cache, no-store")
                connection.setRequestProperty("Pragma", "no-cache")
                connection.responseCode in 200..399
            } finally {
                connection.disconnect()
            }
        } catch (error: Exception) {
            android.util.Log.w("EliteRfKiosk", "Backend health probe failed: ${error.javaClass.simpleName}")
            false
        }
    }

    private fun networkIsConnected(): Boolean {
        val manager = getSystemService(ConnectivityManager::class.java)
        val capabilities = manager.getNetworkCapabilities(manager.activeNetwork)
        return capabilities?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true
    }

    private fun cancelScheduledWork() {
        idleCheck?.let(handler::removeCallbacks)
        idleCheck = null
        loadTimeout?.let(handler::removeCallbacks)
        loadTimeout = null
        healthCheck?.let(handler::removeCallbacks)
        healthCheck = null
        pageReady = false
        retryGeneration++
    }

    private fun clearVisitorSession() {
        val current = webView ?: return
        current.clearHistory()
        current.clearFormData()
        current.clearCache(true)
        current.clearSslPreferences()
        clearSharedBrowserStorage()
        current.evaluateJavascript("try { localStorage.clear(); sessionStorage.clear(); } catch (_) {}", null)
    }

    private fun clearSharedBrowserStorage() {
        CookieManager.getInstance().removeAllCookies(null)
        android.webkit.WebStorage.getInstance().deleteAllData()
    }

    private fun recoverRenderer(failedWebView: WebView) {
        cancelScheduledWork()
        statusStore.record("renderer_restart")
        nativeCamera?.stop()
        nativeCamera = null
        webContainer = null
        if (webView === failedWebView) webView = null
        (failedWebView.parent as? android.view.ViewGroup)?.removeView(failedWebView)
        failedWebView.destroy()
        clearSharedBrowserStorage()
        if (!KioskPolicy.isDeviceOwner(this)) {
            return showStatus("Security policy failure", "The kiosk renderer stopped and the tablet is not managed.")
        }
        val policyApplied = runCatching { KioskPolicy.apply(this) }.getOrDefault(false)
        if (!RendererRecoveryPolicy.mayRecreate(KioskPolicy.isDeviceOwner(this), policyApplied, startLockTaskSafely())) {
            return showStatus("Security policy failure", "The kiosk renderer stopped and Android is not locked.")
        }
        startKiosk()
    }

    private fun startLockTaskSafely(): Boolean {
        val dpm = getSystemService(DevicePolicyManager::class.java)
        if (!KioskPolicy.isDeviceOwner(this) || !dpm.isLockTaskPermitted(packageName)) return false
        if (KioskPolicy.isFullyLocked(this)) return true
        return runCatching {
            startLockTask()
            handler.postDelayed({
                if (!KioskPolicy.isFullyLocked(this) && MaintenanceController.mode(this) != MaintenanceDecision.FULL) {
                    showStatus("Security policy failure", "Android did not enter true Lock Task Mode.")
                }
            }, 350)
            true
        }.getOrDefault(false)
    }

    private fun startSecureSetup() {
        val policyReady = runCatching { KioskPolicy.apply(this) }.getOrDefault(false)
        if (!policyReady) {
            return showStatus("Security policy failure", "This tablet is not ready for locked administrator setup.")
        }
        cancelScheduledWork()
        if (!startLockTaskSafely()) {
            return showStatus("Security policy failure", "Android did not lock the administrator setup screen.")
        }
        showSetup()
    }

    private fun showSetup() {
        val url = EditText(this).apply { hint = "Trusted HTTPS kiosk URL" }
        val pin = EditText(this).apply { hint = "New administrator PIN (4-12 digits)"; inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD }
        val button = Button(this).apply { text = "Configure kiosk" }
        val message = TextView(this).apply { text = "Set the approved HTTPS URL and create the administrator PIN. The recovery code is shown once." }
        button.setOnClickListener {
            if (button.text == "I saved the recovery code") { startKiosk(); return@setOnClickListener }
            val address = url.text.toString().trim(); val code = pin.text.toString().trim()
            if (!settings.validUrl(address)) { message.text = "Use a valid HTTPS URL such as https://kiosk.eliterf.company/kiosk"; return@setOnClickListener }
            if (!code.matches(Regex("\\d{4,12}"))) { message.text = "PIN must contain 4 to 12 digits."; return@setOnClickListener }
            settings.setUrl(address); val recovery = pinAuth.setInitial(code); message.text = "Save this recovery code now: $recovery"; button.text = "I saved the recovery code"
        }
        showPanel("Elite RF Visitor Kiosk", message, url, pin, button)
    }

    private fun showAdminGate() {
        cancelScheduledWork()
        waitingForReconnect = false
        val pin = EditText(this).apply { hint = "Administrator PIN"; inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD }
        val recovery = EditText(this).apply {
            hint = "Or enter recovery code"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        val message = TextView(this).apply { text = "Enter the administrator PIN to open technician access for ten minutes." }
        val maintenance = Button(this).apply { text = "Technician mode for ten minutes" }
        val fullMaintenance = Button(this).apply { text = "Full tablet maintenance for 30 minutes" }
        val controls = Button(this).apply { text = "Administrator controls" }
        val cameraDiagnostics = Button(this).apply {
            val debugBuild = applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0
            text = if (debugBuild) "Open camera pixel diagnostic" else "Camera pixel diagnostic unavailable"
            isEnabled = debugBuild
            setOnClickListener {
                if (!verifyAdministrator(pin, recovery)) {
                    message.text = "Incorrect PIN or temporary lockout."
                } else {
                    cameraDiagnosticsRequested = true
                    startKiosk()
                }
            }
        }
        val back = Button(this).apply { text = "Back to kiosk" }
        maintenance.setOnClickListener {
            if (!verifyAdministrator(pin, recovery)) {
                message.text = "Incorrect PIN or temporary lockout."
            } else {
                openMaintenance(message)
            }
        }
        fullMaintenance.setOnClickListener {
            if (!verifyAdministrator(pin, recovery)) message.text = "Incorrect PIN or temporary lockout."
            else openFullMaintenance(message)
        }
        controls.setOnClickListener {
            if (verifyAdministrator(pin, recovery)) showAdminConsole()
            else message.text = "Incorrect PIN or temporary lockout."
        }
        back.setOnClickListener { startKiosk() }
        showPanel("Administrator", message, pin, recovery, maintenance, fullMaintenance, controls, cameraDiagnostics, back)
    }

    private fun verifyAdministrator(pin: EditText, recovery: EditText): Boolean {
        val pinValue = pin.text.toString()
        val recoveryValue = recovery.text.toString().trim()
        return when {
            pinValue.isNotBlank() -> pinAuth.verifyPin(pinValue)
            recoveryValue.isNotBlank() -> pinAuth.verifyRecovery(recoveryValue)
            else -> false
        }
    }

    private fun openMaintenance(message: TextView) {
        if (MaintenanceController.enter(this)) {
            stopLockTask()
            startActivity(Intent(android.provider.Settings.ACTION_SETTINGS))
        } else {
            message.text = "Maintenance is unavailable because the secure automatic relock could not be scheduled."
        }
    }

    private fun openFullMaintenance(message: TextView) {
        if (!MaintenanceController.enterFull(this)) {
            message.text = "Full maintenance is unavailable because secure automatic relock could not be guaranteed."
            return
        }
        stopLockTask()
        showFullMaintenancePanel()
        handler.postDelayed({ openAndroidLauncher() }, 400)
    }

    private fun openAndroidLauncher() {
        val homeIntent = Intent(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_HOME)
            addCategory(Intent.CATEGORY_DEFAULT)
        }
        val launcher = packageManager.queryIntentActivities(homeIntent, PackageManager.MATCH_DEFAULT_ONLY)
            .filter { it.activityInfo.packageName != packageName && it.activityInfo.packageName != "com.android.settings" }
            .sortedByDescending { it.activityInfo.packageName == "com.android.launcher3" }
            .firstOrNull() ?: return
        val component = android.content.ComponentName(launcher.activityInfo.packageName, launcher.activityInfo.name)
        val launchIntent = Intent(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_LAUNCHER)
            setComponent(component)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        runCatching {
            startActivity(launchIntent)
            handler.postDelayed({ moveTaskToBack(true) }, 250)
        }
    }

    private fun showFullMaintenancePanel() {
        maintenanceCountdown?.let(handler::removeCallbacks)
        val remaining = TextView(this).apply { gravity = Gravity.CENTER; textSize = 18f }
        val message = TextView(this).apply {
            text = "Normal tablet use is available until the automatic relock deadline."
            gravity = Gravity.CENTER
        }
        val returnButton = Button(this).apply {
            text = "Return to Kiosk"
            setOnClickListener {
                MaintenanceController.finish(this@MainActivity)
                startKiosk()
            }
        }
        val homeButton = Button(this).apply {
            text = "Open Tablet Home Screen"
            setOnClickListener {
                stopLockTask()
                handler.postDelayed({ openAndroidLauncher() }, 400)
            }
        }
        maintenanceCountdown = object : Runnable {
            override fun run() {
                if (!MaintenanceController.active(this@MainActivity) || MaintenanceController.mode(this@MainActivity) != MaintenanceDecision.FULL) return
                remaining.text = "Automatic relock in ${remainingMaintenanceText()}"
                handler.postDelayed(this, 1_000)
            }
        }
        handler.post(maintenanceCountdown!!)
        showPanel("Elite RF Maintenance", message, remaining, homeButton, returnButton)
    }

    private fun remainingMaintenanceText(): String {
        val deadline = getSharedPreferences("maintenance", Context.MODE_PRIVATE).getLong("deadline", 0L)
        val seconds = ((deadline - System.currentTimeMillis()).coerceAtLeast(0L) / 1_000L)
        return "%02d:%02d".format(seconds / 60L, seconds % 60L)
    }

    private fun showAdminConsole() {
        val status = TextView(this).apply {
            text = adminStatusText("Checking securely...")
            setTextColor(Color.DKGRAY)
        }
        val url = EditText(this).apply { hint = "Approved HTTPS kiosk URL"; setText(settings.url()) }
        val urlResult = TextView(this)
        val changeUrl = Button(this).apply { text = "Validate and change kiosk URL"; setOnClickListener { validateAndSaveUrl(url.text.toString().trim(), urlResult) } }
        val reload = Button(this).apply { text = "Reload kiosk"; setOnClickListener { startKiosk() } }
        val clear = Button(this).apply { text = "Clear visitor session"; setOnClickListener { clearVisitorSession(); showAdminConsole() } }
        val currentPin = EditText(this).apply { hint = "Current administrator PIN"; inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD }
        val replacementPin = EditText(this).apply { hint = "New administrator PIN (4-12 digits)"; inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD }
        val pinResult = TextView(this)
        val changePin = Button(this).apply { text = "Change administrator PIN"; setOnClickListener {
            pinResult.text = if (pinAuth.changePin(currentPin.text.toString(), replacementPin.text.toString())) "PIN changed." else "PIN change failed."
        } }
        val camera = Button(this).apply { text = "Run camera permission check"; setOnClickListener {
            status.text = status.text.toString() + "\nCamera check: ${checkSelfPermission(Manifest.permission.CAMERA) == android.content.pm.PackageManager.PERMISSION_GRANTED}"
        } }
        val cameraDiagnostics = Button(this).apply {
            val debugBuild = applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0
            text = if (debugBuild) "Open camera pixel diagnostic" else "Camera pixel diagnostic unavailable"
            isEnabled = debugBuild
            setOnClickListener {
                cameraDiagnosticsRequested = true
                startKiosk()
            }
        }
        val cameraCalibration = Button(this).apply {
            text = "Calibrate camera"
            setOnClickListener {
                cameraCalibrationRequested = true
                startKiosk()
            }
        }
        val restart = Button(this).apply { text = "Restart kiosk activity"; setOnClickListener { recreate() } }
        val maintenance = Button(this).apply { text = "Enter ten-minute maintenance mode"; setOnClickListener { showMaintenanceGate() } }
        val fullMaintenance = Button(this).apply {
            text = "Enter full tablet maintenance for 30 minutes"
            setOnClickListener { showFullMaintenanceGate() }
        }
        val lockdown = Button(this).apply {
            val debugBuild = applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0
            text = when {
                debugBuild -> "Production lockdown unavailable in test build"
                settings.productionLockdown() -> "Production lockdown is active"
                else -> "Activate production lockdown"
            }
            isEnabled = !debugBuild && !settings.productionLockdown()
            setOnClickListener { showProductionLockdownGate() }
        }
        val back = Button(this).apply { text = "Return to kiosk now"; setOnClickListener { startKiosk() } }
        showPanel("Elite RF Administrator", status, url, changeUrl, urlResult, reload, clear, currentPin, replacementPin, changePin, pinResult, camera, cameraDiagnostics, cameraCalibration, restart, maintenance, fullMaintenance, lockdown, back)
        refreshServerStatus(status)
    }

    private fun adminStatusText(serverStatus: String): String {
        val network = getSystemService(ConnectivityManager::class.java)
        val capabilities = network.getNetworkCapabilities(network.activeNetwork)
        val online = capabilities?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true
        val webViewVersion = WebView.getCurrentWebViewPackage()?.versionName ?: "unavailable"
        val appVersion = runCatching {
            packageManager.getPackageInfo(packageName, 0).versionName
        }.getOrNull() ?: "unknown"
        return """
            Server: $serverStatus
            Network: ${if (online) "connected" else "offline"}
            Camera granted: ${checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED}
            Android: ${android.os.Build.VERSION.RELEASE}
            Android System WebView: $webViewVersion
            APK version: $appVersion
            Device Owner: ${KioskPolicy.isDeviceOwner(this)}
            Lock Task state: ${getSystemService(android.app.ActivityManager::class.java).lockTaskModeState}
            Last recovery error: ${statusStore.lastError()}
            Production lockdown: ${if (settings.productionLockdown()) "active" else "not activated"}
            Approved kiosk URL: ${settings.url()}
        """.trimIndent()
    }

    private fun refreshServerStatus(status: TextView) {
        val url = settings.url() ?: return
        Thread {
            val result = runCatching {
                val connection = java.net.URL(url).openConnection() as java.net.HttpURLConnection
                connection.connectTimeout = 5_000
                connection.readTimeout = 5_000
                connection.requestMethod = "GET"
                if (connection.responseCode in 200..399) "reachable" else "unavailable"
            }.getOrDefault("unavailable")
            runOnUiThread { status.text = adminStatusText(result) }
        }.start()
    }

    private fun showMaintenanceGate() {
        val pin = EditText(this).apply {
            hint = "Administrator PIN"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
        }
        val message = TextView(this).apply { text = "Re-enter the administrator PIN to unlock maintenance for ten minutes." }
        val button = Button(this).apply { text = "Verify and open maintenance" }
        button.setOnClickListener {
            if (!pinAuth.verifyPin(pin.text.toString())) {
                message.text = "Incorrect PIN or temporary lockout."
                return@setOnClickListener
            }
            openMaintenance(message)
        }
        showPanel("Administrator Verification", message, pin, button)
    }

    private fun showFullMaintenanceGate() {
        val pin = EditText(this).apply {
            hint = "Administrator PIN"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
        }
        val message = TextView(this).apply { text = "Re-enter the administrator PIN to unlock normal tablet use for 30 minutes." }
        val button = Button(this).apply { text = "Verify and open full maintenance" }
        button.setOnClickListener {
            if (!pinAuth.verifyPin(pin.text.toString())) {
                message.text = "Incorrect PIN or temporary lockout."
                return@setOnClickListener
            }
            openFullMaintenance(message)
        }
        showPanel("Full Tablet Maintenance", message, pin, button)
    }

    private fun showProductionLockdownGate() {
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) {
            showStatus("Test build protected", "This test APK cannot disable USB debugging. Install the reviewed production APK before final company lockdown.")
            return
        }
        val pin = EditText(this).apply {
            hint = "Administrator PIN"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
        }
        val confirmation = EditText(this).apply { hint = "Type LOCKDOWN to confirm" }
        val message = TextView(this).apply {
            text = "Complete ADB verification first. Activation disables Android debugging and is intended for final lobby deployment."
        }
        val button = Button(this).apply { text = "Activate permanent production restrictions" }
        button.setOnClickListener {
            if (!pinAuth.verifyPin(pin.text.toString()) || confirmation.text.toString().trim() != "LOCKDOWN") {
                message.text = "Administrator verification or confirmation failed."
                return@setOnClickListener
            }
            settings.setProductionLockdown(true)
            if (!runCatching { KioskPolicy.apply(this@MainActivity) }.getOrDefault(false)) {
                settings.setProductionLockdown(false)
                message.text = "Production policy activation failed. The kiosk remains unavailable."
                return@setOnClickListener
            }
            showStatus("Production lockdown active", "USB debugging is now disabled. Restarting the kiosk securely.")
            handler.postDelayed({ startKiosk() }, 1_500)
        }
        showPanel("Final Production Lockdown", message, pin, confirmation, button)
    }

    private fun validateAndSaveUrl(value: String, result: TextView) {
        if (!settings.validUrl(value)) { result.text = "Use a trusted HTTPS URL ending in /kiosk."; return }
        result.text = "Checking server and certificate..."
        Thread {
            val ok = runCatching {
                val connection = java.net.URL(value).openConnection() as java.net.HttpURLConnection
                connection.connectTimeout = 5_000
                connection.readTimeout = 5_000
                connection.requestMethod = "GET"
                connection.responseCode in 200..399
            }.getOrDefault(false)
            runOnUiThread {
                if (ok) { settings.setUrl(value); result.text = "Server verified. Reloading kiosk."; startKiosk() }
                else result.text = "Server or certificate could not be verified."
            }
        }.start()
    }

    private fun showStatus(title: String, body: String, retrying: Boolean = false) {
        releasePageCamera()
        val titleView = TextView(this).apply {
            text = title
            textSize = 28f
            setTextColor(Color.rgb(11, 74, 138))
            setTypeface(typeface, Typeface.BOLD)
            gravity = Gravity.CENTER
        }
        val bodyView = TextView(this).apply {
            text = body
            textSize = 18f
            setTextColor(Color.rgb(67, 76, 88))
            gravity = Gravity.CENTER
            setLineSpacing(0f, 1.2f)
        }
        val retryView = TextView(this).apply {
            text = "No action is needed. This screen will reconnect automatically."
            textSize = 16f
            setTextColor(Color.rgb(92, 101, 113))
            gravity = Gravity.CENTER
            visibility = if (retrying) View.VISIBLE else View.GONE
        }
        val progress = ProgressBar(this).apply {
            isIndeterminate = true
            visibility = if (retrying) View.VISIBLE else View.GONE
        }
        val logo = ImageView(this).apply {
            setImageResource(com.eliterf.kiosk.R.drawable.elite_rf_logo)
            adjustViewBounds = true
            scaleType = ImageView.ScaleType.FIT_CENTER
            isClickable = true
            setOnClickListener {
                if (adminTapGesture.recordTap(System.currentTimeMillis())) showAdminGate()
            }
        }
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(52), dp(42), dp(52), dp(42))
            background = GradientDrawable().apply {
                setColor(Color.WHITE)
                cornerRadius = dp(28).toFloat()
                setStroke(dp(1), Color.rgb(222, 229, 236))
            }
            elevation = dp(8).toFloat()
            addView(logo, LinearLayout.LayoutParams(dp(430), dp(150)))
            addView(titleView, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(28) })
            addView(bodyView, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(14) })
            addView(progress, LinearLayout.LayoutParams(dp(42), dp(42)).apply {
                gravity = Gravity.CENTER_HORIZONTAL
                topMargin = dp(24)
            })
            addView(retryView, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(14) })
        }
        val background = LinearLayout(this).apply {
            gravity = Gravity.CENTER
            setPadding(dp(80), dp(50), dp(80), dp(50))
            setBackgroundColor(Color.rgb(244, 247, 250))
            addView(card, LinearLayout.LayoutParams(-1, -2))
        }
        setContentView(background)
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun showPanel(title: String, vararg children: View) {
        releasePageCamera()
        val panel = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER; setPadding(60, 40, 60, 40); setBackgroundColor(Color.WHITE) }
        panel.addView(TextView(this).apply { text = title; textSize = 28f; setTextColor(Color.rgb(11, 103, 178)); gravity = Gravity.CENTER }, LinearLayout.LayoutParams(-1, -2))
        children.forEach { panel.addView(it, LinearLayout.LayoutParams(-1, -2).apply { topMargin = 16 }) }
        // Keep every administrator action reachable on the tablet's landscape display.
        setContentView(ScrollView(this).apply {
            isFillViewport = true
            addView(panel, android.widget.FrameLayout.LayoutParams(-1, -1))
        })
    }

    private fun releasePageCamera() {
        nativeCamera?.stop()
        webView?.evaluateJavascript(
            """
                document.querySelectorAll('video').forEach(function(video) {
                    if (video.srcObject) {
                        video.srcObject.getTracks().forEach(function(track) { track.stop(); });
                        video.srcObject = null;
                    }
                });
            """.trimIndent(),
            null
        )
    }

    private fun installSystemUiGuard() {
        if (systemUiGuardInstalled) return
        systemUiGuardInstalled = true
        val decor = window.decorView
        ViewCompat.setOnApplyWindowInsetsListener(decor) { _, insets ->
            if (insets.isVisible(WindowInsetsCompat.Type.systemBars())) requestSystemUiHide()
            insets
        }
        decor.setOnSystemUiVisibilityChangeListener { visibility ->
            val fullscreen = visibility and View.SYSTEM_UI_FLAG_FULLSCREEN != 0
            val navigationHidden = visibility and View.SYSTEM_UI_FLAG_HIDE_NAVIGATION != 0
            if (!fullscreen || !navigationHidden) requestSystemUiHide()
        }
    }

    private fun hideSystemUi() {
        if (MaintenanceController.mode(this) == MaintenanceDecision.FULL && MaintenanceController.active(this)) return
        handler.removeCallbacks(rehideSystemUi)
        hideSystemBarsNow()
    }

    private fun hideSystemBarsNow() {
        val decor = window.decorView
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowCompat.getInsetsController(window, decor).apply {
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_DEFAULT
            hide(WindowInsetsCompat.Type.systemBars())
        }
        @Suppress("DEPRECATION")
        decor.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_FULLSCREEN or
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
                View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
                View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
            )
    }

    private fun requestSystemUiHide() {
        if (MaintenanceController.mode(this) == MaintenanceDecision.FULL && MaintenanceController.active(this)) return
        handler.removeCallbacks(rehideSystemUi)
        handler.postDelayed(rehideSystemUi, 60)
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) requestSystemUiHide()
    }

    override fun onBackPressed() {
        if (MaintenanceController.mode(this) == MaintenanceDecision.FULL && MaintenanceController.active(this)) {
            MaintenanceController.finish(this)
            startKiosk()
            return
        }
        if (nativeReviewFile != null || nativeCapturePending) {
            discardCapturedNativePhoto()
            resetNativeCaptureForRetry()
            webView?.evaluateJavascript("window.eliteRfResetKiosk?.();", null)
            requestSystemUiHide()
            return
        }
        loadConfiguredKiosk()
        requestSystemUiHide()
    }

    private fun registerPowerGuard() {
        if (screenOffReceiverRegistered) return
        val filter = IntentFilter(Intent.ACTION_SCREEN_OFF)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(screenOffReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("DEPRECATION")
            registerReceiver(screenOffReceiver, filter)
        }
        screenOffReceiverRegistered = true
    }

    private fun registerNetworkRecovery() {
        if (networkCallbackRegistered) return
        getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(networkCallback)
        networkCallbackRegistered = true
    }

    override fun onDestroy() {
        cancelScheduledWork()
        maintenanceCountdown?.let(handler::removeCallbacks)
        nativeCamera?.stop()
        nativeCamera = null
        handler.removeCallbacks(rehideSystemUi)
        if (networkCallbackRegistered) {
            getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(networkCallback)
            networkCallbackRegistered = false
        }
        if (screenOffReceiverRegistered) {
            unregisterReceiver(screenOffReceiver)
            screenOffReceiverRegistered = false
        }
        if (managedConfigReceiverRegistered) {
            unregisterReceiver(managedConfigReceiver)
            managedConfigReceiverRegistered = false
        }
        super.onDestroy()
    }
}
