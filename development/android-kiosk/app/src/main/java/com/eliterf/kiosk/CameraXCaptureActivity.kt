package com.eliterf.kiosk

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.drawable.GradientDrawable
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/** Minimal in-package visitor camera. CameraX owns preview orientation and scaling. */
class CameraXCaptureActivity : ComponentActivity() {
    companion object {
        const val EXTRA_OUTPUT_PATH = "output_path"
        private const val TAG = "EliteRfCameraX"
        private const val TARGET_WIDTH = 2560
        private const val TARGET_HEIGHT = 1920
    }

    private lateinit var previewView: PreviewView
    private lateinit var captureButton: Button
    private lateinit var backButton: Button
    private lateinit var statusText: TextView
    private lateinit var outputFile: File
    private var imageCapture: ImageCapture? = null
    private var previewReady = false
    private val captureGuard = CaptureAttemptGuard()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val hideStatus = Runnable { statusText.visibility = View.GONE }
    private var cameraProvider: ProcessCameraProvider? = null
    private val cameraExecutor: ExecutorService = Executors.newSingleThreadExecutor()

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) bindCamera() else showError("Camera permission was denied. Please contact reception.")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or WindowManager.LayoutParams.FLAG_SECURE)
        hideSystemUi()
        outputFile = File(intent.getStringExtra(EXTRA_OUTPUT_PATH).orEmpty())
        if (outputFile.parentFile == null || !outputFile.parentFile!!.isDirectory) {
            finishCanceled()
            return
        }
        setContentView(buildLayout())
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = cancelCapture()
        })
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            bindCamera()
        } else {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    private fun buildLayout(): View {
        previewView = PreviewView(this).apply {
            scaleType = PreviewView.ScaleType.FIT_CENTER
            // Use a texture-backed preview so the camera surface is opaque while
            // CameraX is starting. The performance SurfaceView can briefly show
            // the underlying WebView during activity launch.
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
            setBackgroundColor(0xFF101820.toInt())
        }
        val root = FrameLayout(this).apply {
            setBackgroundColor(0xFF101820.toInt())
            addView(previewView, FrameLayout.LayoutParams(-1, -1))
        }
        val controls = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(24), dp(8), dp(24), dp(8))
            setBackgroundColor(0xEFFFFFFF.toInt())
            elevation = dp(4).toFloat()
        }
        val copy = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val stepLabel = TextView(this).apply {
            text = "ONE LAST STEP"
            setTextColor(0xFFE36B2C.toInt())
            textSize = 13f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            letterSpacing = 0.14f
        }
        val heading = TextView(this).apply {
            text = "Please take your photo"
            setTextColor(0xFF1478BB.toInt())
            textSize = 29f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
        }
        val instruction = TextView(this).apply {
            text = "Look at the camera and center your face and shoulders in the frame."
            setTextColor(0xFF647784.toInt())
            textSize = 16f
        }
        val security = TextView(this).apply {
            text = "Your photo is used for visitor identification and security."
            setTextColor(0xFF8798A3.toInt())
            textSize = 13f
        }
        backButton = Button(this).apply {
            text = "Back"
            isAllCaps = false
            setTextColor(0xFF18354C.toInt())
            background = roundedButton(0xFFE7EDF1.toInt(), 0xFFE7EDF1.toInt())
            setOnClickListener { cancelCapture() }
        }
        captureButton = Button(this).apply {
            text = "TAKE PHOTO"
            isAllCaps = false
            isEnabled = false
            setTextColor(0xFFFFFFFF.toInt())
            background = roundedButton(0xFF1478BB.toInt(), 0xFF1478BB.toInt())
            setOnClickListener { capture() }
        }
        statusText = TextView(this).apply {
            setTextColor(0xFF9B3C25.toInt())
            textSize = 14f
            setPadding(0, dp(3), 0, 0)
            visibility = View.GONE
        }
        copy.addView(stepLabel, LinearLayout.LayoutParams(-1, dp(17)))
        copy.addView(heading, LinearLayout.LayoutParams(-1, dp(36)))
        copy.addView(instruction, LinearLayout.LayoutParams(-1, dp(20)))
        copy.addView(security, LinearLayout.LayoutParams(-1, dp(20)))
        copy.addView(statusText, LinearLayout.LayoutParams(-1, dp(20)))
        controls.addView(copy, LinearLayout.LayoutParams(0, -1, 1f).apply {
            marginEnd = dp(18)
        })
        val actions = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        actions.addView(backButton, LinearLayout.LayoutParams(dp(100), dp(50)).apply {
            marginEnd = dp(10)
        })
        actions.addView(captureButton, LinearLayout.LayoutParams(dp(190), dp(50)))
        controls.addView(actions, LinearLayout.LayoutParams(dp(300), -1))
        // At the MESWAO tablet's 240dpi density this is approximately 198px
        // on the 1080px landscape display, leaving most of the screen for preview.
        root.addView(controls, FrameLayout.LayoutParams(-1, dp(132), Gravity.BOTTOM))
        return root
    }

    private fun bindCamera() {
        if (!packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_FRONT)) {
            showError("The front camera is not available on this tablet.")
            return
        }
        val frontJpegSizes = frontCameraJpegSizes()
        Log.i(TAG, "Front camera JPEG sizes=${frontJpegSizes.joinToString { "${it.width}x${it.height}" }}")
        if (frontJpegSizes.none { it.width == TARGET_WIDTH && it.height == TARGET_HEIGHT }) {
            showError("The front camera does not provide the required 2560x1920 photo size.")
            return
        }
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener({
            runCatching {
                val provider = providerFuture.get()
                cameraProvider = provider
                val selector = CameraSelector.DEFAULT_FRONT_CAMERA
                if (!provider.hasCamera(selector)) error("The front camera is not available on this tablet.")
                val resolution = ResolutionSelector.Builder()
                    .setResolutionStrategy(
                        ResolutionStrategy(
                            android.util.Size(TARGET_WIDTH, TARGET_HEIGHT),
                            ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER
                        )
                    )
                    .build()
                val rotation = previewView.display?.rotation ?: android.view.Surface.ROTATION_0
                val preview = Preview.Builder()
                    .setResolutionSelector(resolution)
                    .setTargetRotation(rotation)
                    .build()
                val capture = ImageCapture.Builder()
                    .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
                    .setResolutionSelector(resolution)
                    .setTargetRotation(rotation)
                    .build()
                preview.setSurfaceProvider(previewView.surfaceProvider)
                provider.unbindAll()
                provider.bindToLifecycle(this, selector, preview, capture)
                imageCapture = capture
                runOnUiThread {
                    previewView.previewStreamState.observe(this) { state ->
                        previewReady = state == PreviewView.StreamState.STREAMING
                        if (previewReady && !captureGuard.isBusy()) {
                            captureButton.isEnabled = true
                        }
                    }
                }
            }.onFailure { showError("The camera could not start.") }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun frontCameraJpegSizes(): List<android.util.Size> = runCatching {
        val manager = getSystemService(CameraManager::class.java)
        manager.cameraIdList.flatMap { id ->
            val characteristics = manager.getCameraCharacteristics(id)
            if (characteristics.get(CameraCharacteristics.LENS_FACING) != CameraCharacteristics.LENS_FACING_FRONT) return@flatMap emptyList()
            characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                ?.getOutputSizes(ImageFormat.JPEG)
                ?.toList()
                .orEmpty()
        }.distinct()
    }.getOrDefault(emptyList())

    private fun capture() {
        val capture = imageCapture ?: return
        if (!previewReady || isFinishing) return
        val generation = captureGuard.begin() ?: return
        captureButton.isEnabled = false
        backButton.isEnabled = false
        captureButton.text = "SAVING..."
        statusText.text = "Saving your photo..."
        statusText.visibility = View.VISIBLE
        outputFile.delete()
        val metadata = ImageCapture.Metadata().apply {
            // MESWAO's front-camera JPEG pixels are horizontally reversed even
            // though the file is tagged with normal EXIF orientation.
            isReversedHorizontal = true
        }
        val options = ImageCapture.OutputFileOptions.Builder(outputFile)
            .setMetadata(metadata)
            .build()
        capture.takePicture(options, cameraExecutor, object : ImageCapture.OnImageSavedCallback {
            override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                if (!captureGuard.isActive(generation) || isFinishing) return
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeFile(outputFile.absolutePath, bounds)
                Log.i(TAG, "CameraX JPEG dimensions=${bounds.outWidth}x${bounds.outHeight}")
                if (!isReadableJpeg(bounds)) {
                    outputFile.delete()
                    captureGuard.complete(generation)
                    runOnUiThread {
                        showError("The photo could not be verified. Please try again.")
                    }
                    return
                }
                runOnUiThread {
                    if (!captureGuard.isActive(generation) || isFinishing) return@runOnUiThread
                    captureGuard.complete(generation)
                    setResult(
                        Activity.RESULT_OK,
                        Intent().putExtra(EXTRA_OUTPUT_PATH, outputFile.absolutePath)
                    )
                    finish()
                }
            }

            override fun onError(exception: ImageCaptureException) {
                if (!captureGuard.isActive(generation) || isFinishing) return
                outputFile.delete()
                captureGuard.complete(generation)
                runOnUiThread { showError("The photo could not be saved. Please try again.") }
            }
        })
    }

    private fun isReadableJpeg(bounds: BitmapFactory.Options): Boolean =
        outputFile.isFile && outputFile.canRead() && outputFile.length() > 0L &&
            bounds.outWidth == TARGET_WIDTH && bounds.outHeight == TARGET_HEIGHT

    private fun cancelCapture() {
        captureGuard.cancel()
        outputFile.delete()
        setResult(Activity.RESULT_CANCELED)
        finish()
    }

    private fun visitorCameraMessage(message: String): String {
        val text = message.lowercase()
        return when {
            text.contains("saved") -> "The photo could not be saved. Please try again."
            text.contains("verified") -> "The photo could not be verified. Please try again."
            else -> "The camera is temporarily unavailable. Please try again or ask reception for help."
        }
    }

    private fun showError(message: String) {
        runOnUiThread {
            statusText.text = visitorCameraMessage(message)
            statusText.visibility = View.VISIBLE
            mainHandler.removeCallbacks(hideStatus)
            if (previewReady) mainHandler.postDelayed(hideStatus, 3500)
            captureButton.text = "TAKE PHOTO"
            captureButton.isEnabled = previewReady && !isFinishing
            backButton.isEnabled = !isFinishing
        }
    }

    private fun finishCanceled() {
        setResult(Activity.RESULT_CANCELED)
        finish()
    }

    private fun hideSystemUi() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            hide(WindowInsetsCompat.Type.systemBars())
        }
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
                View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
        )
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun roundedButton(fill: Int, stroke: Int): GradientDrawable =
        GradientDrawable().apply {
            setColor(fill)
            setStroke(dp(2), stroke)
            cornerRadius = dp(14).toFloat()
        }

    override fun onDestroy() {
        mainHandler.removeCallbacks(hideStatus)
        captureGuard.cancel()
        cameraProvider?.unbindAll()
        super.onDestroy()
        cameraExecutor.shutdownNow()
    }
}
