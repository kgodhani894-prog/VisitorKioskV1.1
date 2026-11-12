package com.eliterf.kiosk

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.content.pm.ApplicationInfo
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ImageFormat
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.SurfaceTexture
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.hardware.camera2.CameraAccessException
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.media.ImageReader
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.Log
import android.util.Size
import android.view.Surface
import android.view.TextureView
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.Button
import android.widget.TextView
import android.widget.ScrollView
import android.view.Gravity
import android.webkit.JavascriptInterface
import android.webkit.WebView
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.ByteArrayOutputStream
import kotlin.math.abs
import kotlin.math.ln
import kotlin.math.min

/** Provides the native Camera2 preview and still-photo capture for kiosk mode. */
class NativeCameraController(
    private val activity: Activity,
    private val container: FrameLayout,
    private val webView: WebView
) {
    private val handler = Handler(Looper.getMainLooper())
    private var cameraOverlay: FrameLayout? = null
    private var nativeControls: View? = null
    private var nativeCaptureButton: Button? = null
    private var previewView: TextureView? = null
    private var previewSurface: Surface? = null
    private var cameraDevice: CameraDevice? = null
    private var captureSession: CameraCaptureSession? = null
    private var imageReader: ImageReader? = null
    private var previewSize: Size? = null
    private var previewLensFacing: Int? = null
    private var activeCameraId: String? = null
    private var sensorOrientation = MESWAO_FRONT_SENSOR_ORIENTATION
    private var captureInProgress = false
    private var cameraOpenRequested = false
    private var previewReadyNotified = false
    private var containerLayoutListener: View.OnLayoutChangeListener? = null
    private var debugCapturePending = false
    private var calibrationMode = false
    private var savedCalibration = CameraCalibration.load(activity)
    private var workingCalibration: CameraCalibration? = null
    private var calibrationStatus: TextView? = null

    companion object {
        private const val TAG = "EliteRfNativeCamera"
        private const val MESWAO_FRONT_SENSOR_ORIENTATION = 270
        private const val MESWAO_FRONT_PREVIEW_OFFSET = 180
        private const val MESWAO_NATURAL_LANDSCAPE_ASPECT = 16f / 9f
    }

    private val surfaceListener = object : TextureView.SurfaceTextureListener {
        override fun onSurfaceTextureAvailable(surface: SurfaceTexture, width: Int, height: Int) {
            openCamera()
        }

        override fun onSurfaceTextureSizeChanged(surface: SurfaceTexture, width: Int, height: Int) {
            applyPreviewTransform()
        }

        override fun onSurfaceTextureDestroyed(surface: SurfaceTexture): Boolean {
            closeCamera()
            previewSurface?.release()
            previewSurface = null
            return true
        }

        override fun onSurfaceTextureUpdated(surface: SurfaceTexture) {
            if (previewReadyNotified) return
            previewReadyNotified = true
            nativeCaptureButton?.isEnabled = true
            webView.evaluateJavascript("window.eliteRfNativeCameraReady?.()", null)
        }
    }

    @JavascriptInterface
    fun launchCapture(payloadJson: String): Boolean =
        (activity as? MainActivity)?.launchNativeCameraCapture(payloadJson) == true

    @JavascriptInterface
    fun pendingCapturePayload(): String? =
        (activity as? MainActivity)?.pendingNativeCapturePayload()

    @JavascriptInterface
    fun resetCaptureForRetry() {
        (activity as? MainActivity)?.resetNativeCaptureForRetry()
    }

    @JavascriptInterface
    fun capturedPhotoSize(): Int =
        (activity as? MainActivity)?.capturedNativePhotoSize() ?: 0

    @JavascriptInterface
    fun capturedPhotoChunk(offset: Int, length: Int): String? =
        (activity as? MainActivity)?.capturedNativePhotoChunk(offset, length)

    @JavascriptInterface
    fun discardCapturedPhoto() {
        (activity as? MainActivity)?.discardCapturedNativePhoto()
    }

    @JavascriptInterface
    fun start(
        @Suppress("UNUSED_PARAMETER") left: Float,
        @Suppress("UNUSED_PARAMETER") top: Float,
        @Suppress("UNUSED_PARAMETER") width: Float,
        @Suppress("UNUSED_PARAMETER") height: Float,
        calibration: Boolean = false
    ) {
        activity.runOnUiThread {
            stopOnMainThread()
            calibrationMode = calibration
            savedCalibration = CameraCalibration.load(activity)
            workingCalibration = savedCalibration
            if (activity.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
                reportError("Camera permission is not available on this tablet.")
                return@runOnUiThread
            }
            // Hardware WebView composition can otherwise flatten the transparent
            // page over the TextureView on this tablet. Restrict the workaround
            // to the native-camera window and restore normal rendering on stop.
            webView.setLayerType(View.LAYER_TYPE_SOFTWARE, null)
            webView.setBackgroundColor(android.graphics.Color.TRANSPARENT)
            container.setBackgroundColor(android.graphics.Color.TRANSPARENT)
            // Keep the live camera independent from WebView compositing. The
            // existing WebView returns for review, retake, and submit.
            webView.visibility = View.INVISIBLE
            createPreviewOverlay()
        }
    }

    private fun createPreviewOverlay() {
        val overlay = FrameLayout(activity).apply {
            setBackgroundColor(Color.BLACK)
        }
        val texture = TextureView(activity).apply {
            isOpaque = true
            surfaceTextureListener = surfaceListener
        }
        overlay.addView(texture, FrameLayout.LayoutParams(-1, -1))
        nativeControls = (if (calibrationMode) createCalibrationControls() else createNativeControls()).also { controls ->
            overlay.addView(controls, nativeControlsLayoutParams())
        }
        val params = FrameLayout.LayoutParams(-1, -1)
        params.gravity = android.view.Gravity.TOP
        // Place the live camera above the WebView so it cannot be occluded by
        // the WebView renderer. The native action bar remains above the camera.
        container.addView(overlay, params)
        containerLayoutListener = View.OnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
            nativeControls?.layoutParams = nativeControlsLayoutParams()
            applyPreviewTransform()
        }.also(container::addOnLayoutChangeListener)
        cameraOverlay = overlay
        previewView = texture
        if (texture.isAvailable) openCamera()
    }

    @JavascriptInterface
    fun capture() {
        activity.runOnUiThread {
            val device = cameraDevice
            val session = captureSession
            val reader = imageReader
            if (device == null || session == null || reader == null || captureInProgress) {
                reportError("Camera is still starting. Please try again in a moment.")
                return@runOnUiThread
            }
            captureInProgress = true
            runCatching {
                val request = device.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE).apply {
                    addTarget(reader.surface)
                    set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO)
                    set(CaptureRequest.JPEG_ORIENTATION, jpegOrientation())
                }
                session.capture(request.build(), object : CameraCaptureSession.CaptureCallback() {}, handler)
            }.onFailure {
                captureInProgress = false
                reportError("Camera capture could not start. Please try again.")
            }
        }
    }

    @JavascriptInterface
    fun captureDebugFrame() {
        if (!isDebugBuild()) return
        activity.runOnUiThread {
            val device = cameraDevice
            val session = captureSession
            val reader = imageReader
            val texture = previewView
            if (device == null || session == null || reader == null || texture == null) {
                reportDebug("Camera is not ready for a diagnostic frame.")
                return@runOnUiThread
            }
            val sourceBitmap = texture.bitmap
            if (sourceBitmap == null || sourceBitmap.width <= 0 || sourceBitmap.height <= 0) {
                reportDebug("The rendered preview frame was unavailable.")
                return@runOnUiThread
            }
            val sourceSaved = writeDebugBitmap(sourceBitmap, "camera_source_buffer.jpg")
            val renderedBitmap = renderPreviewDiagnostic(sourceBitmap)
            val previewSaved = sourceSaved && renderedBitmap != null &&
                writeDebugBitmap(renderedBitmap, "camera_preview_capture.jpg") &&
                writeDebugBitmap(renderedBitmap, "rendered_preview_frame.jpg")
            val debugSaved = sourceSaved && renderedBitmap != null &&
                writeDebugBitmap(renderedBitmap, "camera_debug_frame.jpg")
            renderedBitmap?.recycle()
            sourceBitmap.recycle()
            if (!previewSaved || !debugSaved) {
                reportDebug("The diagnostic preview frame could not be saved.")
                return@runOnUiThread
            }
            debugCapturePending = true
            runCatching {
                val request = device.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE).apply {
                    addTarget(reader.surface)
                    set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO)
                    set(CaptureRequest.JPEG_ORIENTATION, jpegOrientation())
                }
                session.capture(request.build(), object : CameraCaptureSession.CaptureCallback() {}, handler)
            }.onFailure {
                debugCapturePending = false
                reportDebug("The diagnostic still capture could not start.")
            }
        }
    }

    @JavascriptInterface
    fun saveDebugDataUrl(dataUrl: String) {
        if (!isDebugBuild()) return
        activity.runOnUiThread {
            val encoded = dataUrl.substringAfter(',', "")
            val bytes = runCatching { Base64.decode(encoded, Base64.DEFAULT) }.getOrNull()
            if (bytes == null || bytes.isEmpty() || !writeDebugBytes(bytes, "camera_uploaded_photo.jpg")) {
                reportDebug("The diagnostic upload artifact could not be saved.")
                return@runOnUiThread
            }
            writeDebugBytes(bytes, "uploaded_backend_photo.jpg")
            reportDebug("camera_uploaded_photo.jpg and uploaded_backend_photo.jpg saved from the upload payload.")
        }
    }

    @JavascriptInterface
    fun stop() {
        if (Looper.myLooper() == Looper.getMainLooper()) stopOnMainThread()
        else activity.runOnUiThread(::stopOnMainThread)
    }

    private fun openCamera() {
        if (cameraOpenRequested || previewView?.surfaceTexture == null) return
        cameraOpenRequested = true
        val manager = activity.getSystemService(CameraManager::class.java)
        val cameraId = runCatching {
            val preferred = activeCalibration()?.cameraId
            manager.cameraIdList.firstOrNull { id -> id == preferred }
                ?: manager.cameraIdList.firstOrNull { id ->
                    manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) ==
                        CameraCharacteristics.LENS_FACING_FRONT
                } ?: manager.cameraIdList.firstOrNull()
        }.getOrNull()
        if (cameraId == null) {
            cameraOpenRequested = false
            reportError("Camera unavailable.\nPlease notify the front desk.")
            return
        }
        try {
            val characteristics = manager.getCameraCharacteristics(cameraId)
            activeCameraId = cameraId
            if (calibrationMode && workingCalibration?.cameraId == null) {
                workingCalibration = (workingCalibration
                    ?: CameraCalibration(null, 0, false, 1f, 0f, 0f))
                    .copy(cameraId = cameraId)
            }
            previewLensFacing = characteristics.get(CameraCharacteristics.LENS_FACING)
            sensorOrientation = characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION)
                ?: MESWAO_FRONT_SENSOR_ORIENTATION
            val streamMap = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                ?: run {
                    cameraOpenRequested = false
                    reportError("Camera unavailable.\nPlease notify the front desk.")
                    return
                }
            val jpegSize = chooseOutputSize(streamMap.getOutputSizes(ImageFormat.JPEG).orEmpty())
            previewSize = choosePreviewSize(
                streamMap.getOutputSizes(SurfaceTexture::class.java).orEmpty(),
                jpegSize
            )
            previewView?.surfaceTexture?.setDefaultBufferSize(previewSize!!.width, previewSize!!.height)
            Log.d(
                TAG,
                "cameraId=$activeCameraId cameraLabel=Camera-$activeCameraId facing=$previewLensFacing sensor=$sensorOrientation " +
                    "activityOrientation=${activity.resources.configuration.orientation} " +
                    "displayRotation=${displayRotationDegrees()} preview=${previewSize!!.width}x${previewSize!!.height} " +
                    "rotation=${previewRotation()} mirrored=false"
            )
            applyPreviewTransform()
            imageReader = ImageReader.newInstance(jpegSize.width, jpegSize.height, ImageFormat.JPEG, 2).also { reader ->
                reader.setOnImageAvailableListener({ source ->
                    val image = source.acquireLatestImage() ?: return@setOnImageAvailableListener
                    try {
                        val buffer = image.planes.firstOrNull()?.buffer
                        if (buffer == null) {
                            reportError("Camera unavailable.\nPlease notify the front desk.")
                        } else {
                            val bytes = ByteArray(buffer.remaining())
                            buffer.get(bytes)
                            if (isDebugBuild()) {
                                writeDebugBytes(bytes, "camera_raw_frame.jpg")
                                writeDebugBytes(bytes, "raw_camera_frame.jpg")
                            }
                            if (isDebugBuild() && debugCapturePending) {
                                debugCapturePending = false
                                writeDebugBytes(bytes, "camera_take_photo_capture.jpg")
                                writeDebugBytes(bytes, "take_photo_capture.jpg")
                                reportDebug(
                                    "Camera artifacts saved. cameraId=$activeCameraId " +
                                        "stream=${previewSize?.width}x${previewSize?.height} " +
                                        "preview=${previewView?.width}x${previewView?.height} " +
                                        "previewRotation=${previewTransformRotation()} " +
                                        "jpegRotation=${jpegOrientation()} mirrored=false"
                                )
                                return@setOnImageAvailableListener
                            }
                            if (isDebugBuild()) {
                                writeDebugBytes(bytes, "camera_take_photo_capture.jpg")
                                writeDebugBytes(bytes, "take_photo_capture.jpg")
                            }
                            val calibrated = activeCalibration()?.let { applyCalibrationToJpeg(bytes, it) } ?: bytes
                            deliverPhoto(calibrated)
                        }
                    } finally {
                        image.close()
                    }
                }, handler)
            }
            manager.openCamera(cameraId, object : CameraDevice.StateCallback() {
                override fun onOpened(camera: CameraDevice) {
                    cameraDevice = camera
                    createSession(camera)
                }

                override fun onDisconnected(camera: CameraDevice) {
                    camera.close()
                    cameraDevice = null
                    cameraOpenRequested = false
                    reportError("Camera unavailable.\nPlease notify the front desk.")
                }

                override fun onError(camera: CameraDevice, error: Int) {
                    camera.close()
                    cameraDevice = null
                    cameraOpenRequested = false
                    reportError("Camera unavailable.\nPlease notify the front desk.")
                }
            }, handler)
        } catch (_: SecurityException) {
            cameraOpenRequested = false
            reportError("Camera permission is not available on this tablet.")
        } catch (_: CameraAccessException) {
            cameraOpenRequested = false
            reportError("Camera unavailable.\nPlease notify the front desk.")
        }
    }

    private fun createSession(camera: CameraDevice) {
        val readerSurface = imageReader?.surface ?: return
        val texture = previewView?.surfaceTexture ?: return
        val surface = Surface(texture)
        previewSurface?.release()
        previewSurface = surface
        runCatching {
            camera.createCaptureSession(listOf(surface, readerSurface), object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(session: CameraCaptureSession) {
                    captureSession = session
                    runCatching {
                        val request = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
                            addTarget(surface)
                            set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO)
                        }
                        session.setRepeatingRequest(request.build(), null, handler)
                        applyPreviewTransform()
                    }.onFailure {
                        reportError("Camera unavailable.\nPlease notify the front desk.")
                    }
                }

                override fun onConfigureFailed(session: CameraCaptureSession) {
                    reportError("Camera unavailable.\nPlease notify the front desk.")
                }
            }, handler)
        }.onFailure {
            reportError("Camera unavailable.\nPlease notify the front desk.")
        }
    }

    private fun createCalibrationControls(): ScrollView {
        val content = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(34), dp(18), dp(34), dp(18))
            setBackgroundColor(Color.WHITE)
        }
        content.addView(TextView(activity).apply {
            text = "CAMERA CALIBRATION MODE"
            setTextColor(Color.rgb(241, 90, 42))
            textSize = 18f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })
        content.addView(TextView(activity).apply {
            text = "Hold an upright sign reading TOP ↑ and ELITE RF. Adjust until it is upright, readable, and natural."
            setTextColor(Color.rgb(30, 55, 72))
            textSize = 14f
            setPadding(0, dp(8), 0, dp(8))
        })
        calibrationStatus = TextView(activity).apply {
            setTextColor(Color.rgb(11, 103, 178))
            textSize = 13f
        }
        content.addView(calibrationStatus)
        val cameraRow = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
        cameraChoices().forEach { (id, label) ->
            cameraRow.addView(calibrationButton(label) { selectCamera(id) },
                LinearLayout.LayoutParams(0, dp(48), 1f).apply { rightMargin = dp(8) })
        }
        if (cameraRow.childCount > 0) content.addView(cameraRow)
        content.addView(calibrationRow(
            "ROTATE 90° LEFT" to { changeCalibration { it.copy(rotation = it.rotation - 90) } },
            "ROTATE 90° RIGHT" to { changeCalibration { it.copy(rotation = it.rotation + 90) } },
            "FLIP HORIZONTALLY" to { changeCalibration { it.copy(mirror = !it.mirror) } }
        ))
        content.addView(calibrationRow(
            "ZOOM IN" to { changeCalibration { it.copy(zoom = (it.zoom * 1.1f).coerceAtMost(3f)) } },
            "ZOOM OUT" to { changeCalibration { it.copy(zoom = (it.zoom / 1.1f).coerceAtLeast(1f)) } },
            "RESET" to { resetCalibration() }
        ))
        content.addView(calibrationRow(
            "STRETCH WIDER" to { changeCalibration { it.copy(horizontalStretch = (it.horizontalStretch + 0.05f).coerceAtMost(1.5f)) } },
            "STRETCH NARROWER" to { changeCalibration { it.copy(horizontalStretch = (it.horizontalStretch - 0.05f).coerceAtLeast(0.75f)) } }
        ))
        content.addView(calibrationRow(
            "MOVE LEFT" to { changeCalibration { it.copy(offsetX = it.offsetX - 0.05f) } },
            "MOVE RIGHT" to { changeCalibration { it.copy(offsetX = it.offsetX + 0.05f) } },
            "MOVE UP" to { changeCalibration { it.copy(offsetY = it.offsetY - 0.05f) } },
            "MOVE DOWN" to { changeCalibration { it.copy(offsetY = it.offsetY + 0.05f) } }
        ))
        val actionRow = calibrationRow(
            "SAVE AND LOCK" to { saveCalibration() },
            "CANCEL" to { cancelCalibration() }
        )
        content.addView(actionRow)
        updateCalibrationStatus()
        return ScrollView(activity).apply {
            isFillViewport = true
            setBackgroundColor(Color.WHITE)
            addView(content, FrameLayout.LayoutParams(-1, -2))
        }
    }

    private fun calibrationRow(vararg actions: Pair<String, () -> Unit>): LinearLayout =
        LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(6), 0, 0)
            actions.forEachIndexed { index, (label, action) ->
                addView(calibrationButton(label, action), LinearLayout.LayoutParams(0, dp(48), 1f).apply {
                    if (index < actions.lastIndex) rightMargin = dp(8)
                })
            }
        }

    private fun calibrationButton(label: String, action: () -> Unit): Button =
        Button(activity).apply {
            text = label
            setTextColor(Color.WHITE)
            textSize = 11f
            setOnClickListener { action() }
            setBackgroundColor(Color.rgb(20, 120, 187))
        }

    private fun cameraChoices(): List<Pair<String, String>> {
        val manager = activity.getSystemService(CameraManager::class.java)
        return runCatching {
            manager.cameraIdList.mapNotNull { id ->
                val facing = manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING)
                val label = when (facing) {
                    CameraCharacteristics.LENS_FACING_FRONT -> "FRONT CAMERA"
                    CameraCharacteristics.LENS_FACING_BACK -> "REAR CAMERA"
                    CameraCharacteristics.LENS_FACING_EXTERNAL -> "EXTERNAL USB CAMERA"
                    else -> null
                }
                label?.let { id to it }
            }.distinctBy { it.second }
        }.getOrDefault(emptyList())
    }

    private fun changeCalibration(change: (CameraCalibration) -> CameraCalibration) {
        val current = workingCalibration ?: CameraCalibration(activeCameraId, 0, false, 1f, 0f, 0f)
        workingCalibration = change(current).normalized()
        updateCalibrationStatus()
        applyPreviewTransform()
    }

    private fun resetCalibration() {
        workingCalibration = CameraCalibration(activeCameraId ?: workingCalibration?.cameraId, 0, false, 1f, 0f, 0f)
        updateCalibrationStatus()
        applyPreviewTransform()
    }

    private fun selectCamera(cameraId: String) {
        changeCalibration { it.copy(cameraId = cameraId) }
        if (activeCameraId == cameraId) return
        closeCamera()
        imageReader?.setOnImageAvailableListener(null, null)
        imageReader?.close()
        imageReader = null
        previewSurface?.release()
        previewSurface = null
        cameraOpenRequested = false
        previewReadyNotified = false
        openCamera()
    }

    private fun updateCalibrationStatus() {
        val value = workingCalibration ?: savedCalibration ?: CameraCalibration(activeCameraId, 0, false, 1f, 0f, 0f)
        calibrationStatus?.text = "Rotation ${value.rotation}°   Mirror ${if (value.mirror) "on" else "off"}   Zoom %.2fx   Horizontal %.2fx   Offset %.2f, %.2f".format(
            value.zoom, value.horizontalStretch, value.offsetX, value.offsetY
        )
    }

    private fun saveCalibration() {
        val value = (workingCalibration ?: CameraCalibration(activeCameraId, 0, false, 1f, 0f, 0f))
            .copy(cameraId = activeCameraId ?: workingCalibration?.cameraId)
            .normalized()
        CameraCalibration.save(activity, value)
        savedCalibration = value
        calibrationMode = false
        workingCalibration = null
        (activity as? MainActivity)?.onCameraCalibrationFinished()
        stopOnMainThread()
        webView.post { webView.evaluateJavascript("window.eliteRfFinishCalibration?.()", null) }
    }

    private fun cancelCalibration() {
        calibrationMode = false
        workingCalibration = null
        (activity as? MainActivity)?.onCameraCalibrationFinished()
        stopOnMainThread()
        webView.post { webView.evaluateJavascript("window.eliteRfCancelCalibration?.()", null) }
    }

    private fun createNativeControls(): LinearLayout {
        val copy = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(48), dp(26), dp(48), dp(26))
            setBackgroundColor(Color.WHITE)
        }
        val copyColumn = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
        }
        copyColumn.addView(TextView(activity).apply {
            text = "ONE LAST STEP"
            setTextColor(Color.rgb(241, 90, 42))
            textSize = 14f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })
        copyColumn.addView(TextView(activity).apply {
            text = "Please take your photo"
            setTextColor(Color.rgb(7, 103, 174))
            textSize = 28f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(0, dp(6), 0, 0)
        })
        copyColumn.addView(TextView(activity).apply {
            text = "Align your face and shoulders with the guide, then take your photo."
            setTextColor(Color.rgb(108, 129, 144))
            textSize = 16f
            setPadding(0, dp(6), 0, 0)
        })
        copyColumn.addView(TextView(activity).apply {
            text = "Your photo is used for visitor identification and security."
            setTextColor(Color.rgb(121, 144, 156))
            textSize = 12f
            setPadding(0, dp(10), 0, 0)
        })
        copy.addView(copyColumn, LinearLayout.LayoutParams(0, -1, 1f))
        nativeCaptureButton = Button(activity).apply {
            text = "Take photo"
            isEnabled = false
            setTextColor(Color.WHITE)
            textSize = 18f
            setOnClickListener { capture() }
            setBackgroundColor(Color.rgb(20, 120, 187))
        }
        copy.addView(nativeCaptureButton, LinearLayout.LayoutParams(dp(280), dp(68)).apply {
            gravity = Gravity.CENTER_VERTICAL
            leftMargin = dp(32)
        })
        return copy
    }

    private fun nativeControlsLayoutParams(): FrameLayout.LayoutParams =
        FrameLayout.LayoutParams(
            -1,
            maxOf(
                dp(if (calibrationMode) 420 else 220),
                (container.height * if (calibrationMode) 0.42f else 0.30f).toInt()
            )
        ).apply {
            gravity = Gravity.BOTTOM
        }

    private fun dp(value: Int): Int = (value * activity.resources.displayMetrics.density).toInt()

    private fun applyPreviewTransform() {
        val view = previewView ?: return
        val size = previewSize ?: return
        val viewWidth = view.width.toFloat()
        val viewHeight = view.height.toFloat()
        if (viewWidth <= 0f || viewHeight <= 0f) return
        val calibration = activeCalibration()
        val fit = if (calibration != null) {
            manualTransformMatrix(size.width.toFloat(), size.height.toFloat(), viewWidth, viewHeight, calibration)
        } else {
            previewTransformMatrix(size.width.toFloat(), size.height.toFloat(), viewWidth, viewHeight)
        } ?: return
        Log.d(
            TAG,
            "preview cameraId=$activeCameraId display=${viewWidth.toInt()}x${viewHeight.toInt()} " +
                "stream=${size.width}x${size.height} rotation=${previewTransformRotation()} " +
                "jpegRotation=${jpegOrientation()} manualCalibration=${calibration != null} " +
                "fitCompleteFrame=true mirrored=false"
        )
        view.setTransform(fit)
    }

    private fun previewTransformMatrix(
        sourceWidth: Float,
        sourceHeight: Float,
        viewWidth: Float,
        viewHeight: Float
    ): Matrix? {
        if (viewWidth <= 0f || viewHeight <= 0f) return null
        val source = RectF(0f, 0f, sourceWidth, sourceHeight)
        val rotate = Matrix().apply {
            setRotate(previewTransformRotation().toFloat(), source.centerX(), source.centerY())
        }
        val rotated = RectF(source)
        rotate.mapRect(rotated)
        val target = RectF(0f, 0f, viewWidth, viewHeight)
        val scale = min(target.width() / rotated.width(), target.height() / rotated.height())
        return Matrix(rotate).apply {
            // Uniformly fit the complete camera frame without cropping or stretching.
            postScale(scale, scale, source.centerX(), source.centerY())
            val fitted = RectF(source)
            mapRect(fitted)
            postTranslate(target.centerX() - fitted.centerX(), target.centerY() - fitted.centerY())
        }
    }

    private fun manualTransformMatrix(
        sourceWidth: Float,
        sourceHeight: Float,
        viewWidth: Float,
        viewHeight: Float,
        calibration: CameraCalibration
    ): Matrix? {
        if (viewWidth <= 0f || viewHeight <= 0f) return null
        val source = RectF(0f, 0f, sourceWidth, sourceHeight)
        val matrix = Matrix().apply {
            setRotate(calibration.rotation.toFloat(), source.centerX(), source.centerY())
            if (calibration.mirror) postScale(-1f, 1f, source.centerX(), source.centerY())
        }
        val rotated = RectF(source)
        matrix.mapRect(rotated)
        val scale = maxOf(viewWidth / rotated.width(), viewHeight / rotated.height()) * calibration.zoom
        matrix.postScale(scale, scale, source.centerX(), source.centerY())
        val fitted = RectF(source)
        matrix.mapRect(fitted)
        matrix.postTranslate(
            viewWidth / 2f - fitted.centerX() + calibration.offsetX * viewWidth,
            viewHeight / 2f - fitted.centerY() + calibration.offsetY * viewHeight
        )
        matrix.postScale(calibration.horizontalStretch, 1f, viewWidth / 2f, viewHeight / 2f)
        return matrix
    }

    private fun activeCalibration(): CameraCalibration? =
        if (calibrationMode) workingCalibration ?: savedCalibration else savedCalibration

    private fun renderPreviewDiagnostic(source: Bitmap): Bitmap? = runCatching {
        val rendered = Bitmap.createBitmap(source.width, source.height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(rendered)
        canvas.drawColor(Color.BLACK)
        val calibration = activeCalibration()
        val matrix = (if (calibration != null) {
            manualTransformMatrix(
                source.width.toFloat(), source.height.toFloat(),
                source.width.toFloat(), source.height.toFloat(), calibration
            )
        } else previewTransformMatrix(
            source.width.toFloat(), source.height.toFloat(),
            source.width.toFloat(), source.height.toFloat()
        )) ?: return@runCatching null
        canvas.drawBitmap(source, matrix, Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG))
        rendered
    }.getOrNull()

    private fun applyCalibrationToJpeg(bytes: ByteArray, calibration: CameraCalibration): ByteArray =
        runCatching {
            val source = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                ?: return@runCatching bytes
            val outputWidth = maxOf(source.width, source.height)
            val outputHeight = minOf(source.width, source.height)
            val transformed = Bitmap.createBitmap(outputWidth, outputHeight, Bitmap.Config.ARGB_8888)
            val canvas = Canvas(transformed)
            canvas.drawColor(Color.BLACK)
            val matrix = manualTransformMatrix(
                source.width.toFloat(), source.height.toFloat(),
                outputWidth.toFloat(), outputHeight.toFloat(), calibration
            ) ?: return@runCatching bytes
            canvas.drawBitmap(source, matrix, Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG))
            val output = ByteArrayOutputStream()
            check(transformed.compress(Bitmap.CompressFormat.JPEG, 95, output))
            source.recycle()
            transformed.recycle()
            output.toByteArray()
        }.getOrElse { bytes }

    private fun deliverPhoto(bytes: ByteArray) {
        captureInProgress = false
        val dataUrl = "data:image/jpeg;base64,${Base64.encodeToString(bytes, Base64.NO_WRAP)}"
        activity.runOnUiThread {
            stopOnMainThread()
            webView.visibility = View.VISIBLE
            webView.evaluateJavascript(
                "window.eliteRfNativeCameraResult(${JSONObject.quote(dataUrl)})",
                null
            )
        }
    }

    private fun reportError(message: String) {
        activity.runOnUiThread {
            if (calibrationMode) {
                calibrationMode = false
                workingCalibration = null
                (activity as? MainActivity)?.onCameraCalibrationFinished()
            }
            // A failed native start must not leave the WebView hidden behind a
            // removed or unusable camera surface.
            if (cameraOverlay != null) stopOnMainThread()
            webView.visibility = View.VISIBLE
            webView.evaluateJavascript("window.eliteRfNativeCameraError(${JSONObject.quote(message)})", null)
        }
    }

    private fun reportDebug(message: String) {
        if (!isDebugBuild()) return
        activity.runOnUiThread {
            webView.evaluateJavascript(
                "window.eliteRfNativeCameraDebugResult(${JSONObject.quote(message)})",
                null
            )
        }
    }

    private fun writeDebugBitmap(bitmap: Bitmap, filename: String): Boolean = runCatching {
        FileOutputStream(File(activity.filesDir, filename)).use { output ->
            check(bitmap.compress(Bitmap.CompressFormat.JPEG, 95, output))
        }
    }.isSuccess

    private fun writeDebugBytes(bytes: ByteArray, filename: String): Boolean = runCatching {
        File(activity.filesDir, filename).writeBytes(bytes)
    }.isSuccess

    private fun isDebugBuild(): Boolean =
        activity.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0

    private fun choosePreviewSize(sizes: Array<out Size>, referenceSize: Size): Size {
        if (sizes.isEmpty()) return Size(1920, 1080)
        // Match the preview to the still stream's aspect. View dimensions are
        // not reliable yet when Camera2 is first opened on this tablet.
        val targetAspect = referenceSize.width.toFloat() / referenceSize.height.toFloat()
        return sizes.minWithOrNull(
            compareBy<Size> {
                abs(ln((it.width.toFloat() / it.height.toFloat()) / targetAspect))
            }.thenByDescending { it.width.toLong() * it.height.toLong() }
        ) ?: sizes.first()
    }

    private fun chooseOutputSize(sizes: Array<out Size>): Size {
        if (sizes.isEmpty()) return Size(1280, 720)
        return sizes.minWithOrNull(
            compareBy<Size> {
                abs(ln((it.width.toFloat() / it.height.toFloat()) / MESWAO_NATURAL_LANDSCAPE_ASPECT))
            }.thenByDescending { it.width.toLong() * it.height.toLong() }
        ) ?: sizes.first()
    }

    private fun previewRotation(): Int = if (previewLensFacing == CameraCharacteristics.LENS_FACING_FRONT) {
        (sensorOrientation + displayRotationDegrees()) % 360
    } else {
        (sensorOrientation - displayRotationDegrees() + 360) % 360
    }

    private fun previewTransformRotation(): Int = if (previewLensFacing == CameraCharacteristics.LENS_FACING_FRONT) {
        (previewRotation() + MESWAO_FRONT_PREVIEW_OFFSET) % 360
    } else {
        previewRotation()
    }

    private fun displayRotationDegrees(): Int = when (activity.windowManager.defaultDisplay.rotation) {
        Surface.ROTATION_90 -> 90
        Surface.ROTATION_180 -> 180
        Surface.ROTATION_270 -> 270
        else -> 0
    }

    private fun jpegOrientation(): Int = if (activeCalibration() != null) 0 else previewRotation()

    private fun closeCamera() {
        captureSession?.close()
        captureSession = null
        cameraDevice?.close()
        cameraDevice = null
    }

    private fun stopOnMainThread() {
        captureInProgress = false
        closeCamera()
        imageReader?.setOnImageAvailableListener(null, null)
        imageReader?.close()
        imageReader = null
        previewSurface?.release()
        previewSurface = null
        cameraOverlay?.let { container.removeView(it) }
        containerLayoutListener?.let(container::removeOnLayoutChangeListener)
        containerLayoutListener = null
        cameraOverlay = null
        nativeControls = null
        nativeCaptureButton = null
        previewView = null
        previewSize = null
        previewLensFacing = null
        activeCameraId = null
        sensorOrientation = MESWAO_FRONT_SENSOR_ORIENTATION
        cameraOpenRequested = false
        previewReadyNotified = false
        debugCapturePending = false
        // Always restore the page after native capture stops, including
        // disconnects, failed starts, and interrupted captures.
        webView.visibility = View.VISIBLE
        webView.setLayerType(View.LAYER_TYPE_NONE, null)
    }
}
