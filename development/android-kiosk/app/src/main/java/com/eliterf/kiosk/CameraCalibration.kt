package com.eliterf.kiosk

import android.content.Context

/** Persistent, device-local manual camera calibration for the MESWAO tablet. */
data class CameraCalibration(
    val cameraId: String?,
    val rotation: Int,
    val mirror: Boolean,
    val zoom: Float,
    val offsetX: Float,
    val offsetY: Float,
    val horizontalStretch: Float = 1f,
    val fitMode: String = FIT_COVER
) {
    fun normalized(): CameraCalibration = copy(
        rotation = ((rotation % 360) + 360) % 360,
        zoom = zoom.coerceIn(1f, 3f),
        offsetX = offsetX.coerceIn(-1f, 1f),
        offsetY = offsetY.coerceIn(-1f, 1f),
        horizontalStretch = horizontalStretch.coerceIn(0.75f, 1.5f),
        fitMode = FIT_COVER
    )

    companion object {
        const val FIT_COVER = "cover"
        private const val PREFS = "camera_calibration"
        private const val ENABLED = "enabled"
        private const val CAMERA_ID = "camera_id"
        private const val ROTATION = "rotation"
        private const val MIRROR = "mirror"
        private const val ZOOM = "zoom"
        private const val OFFSET_X = "offset_x"
        private const val OFFSET_Y = "offset_y"
        private const val HORIZONTAL_STRETCH = "horizontal_stretch"
        private const val FIT_MODE = "fit_mode"

        fun load(context: Context): CameraCalibration? {
            val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            if (!prefs.getBoolean(ENABLED, false)) return null
            return CameraCalibration(
                prefs.getString(CAMERA_ID, null),
                prefs.getInt(ROTATION, 0),
                prefs.getBoolean(MIRROR, false),
                prefs.getFloat(ZOOM, 1f),
                prefs.getFloat(OFFSET_X, 0f),
                prefs.getFloat(OFFSET_Y, 0f),
                prefs.getFloat(HORIZONTAL_STRETCH, 1f),
                prefs.getString(FIT_MODE, FIT_COVER) ?: FIT_COVER
            ).normalized()
        }

        fun save(context: Context, calibration: CameraCalibration) {
            val value = calibration.normalized()
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putBoolean(ENABLED, true)
                .putString(CAMERA_ID, value.cameraId)
                .putInt(ROTATION, value.rotation)
                .putBoolean(MIRROR, value.mirror)
                .putFloat(ZOOM, value.zoom)
                .putFloat(OFFSET_X, value.offsetX)
                .putFloat(OFFSET_Y, value.offsetY)
                .putFloat(HORIZONTAL_STRETCH, value.horizontalStretch)
                .putString(FIT_MODE, value.fitMode)
                // Calibration must survive an immediate activity restart or reboot.
                .commit()
        }
    }
}
