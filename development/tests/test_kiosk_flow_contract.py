from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1].parent


class KioskFlowContractTests(unittest.TestCase):
    def test_production_visitor_flow_has_no_temporary_autofill(self):
        source = (ROOT / "static" / "kiosk.js").read_text()
        self.assertNotIn("fillTemporaryTestDetails", source)
        self.assertNotIn("pendingTestHostSelection", source)
        self.assertNotIn("logoTapCount", source)
        self.assertNotIn("test.visitor@example.com", source)
        self.assertIn('let cameraCalibrationMode = location.hash === "#camera-calibration";', source)
        self.assertIn("function openCameraCalibrationStep()", source)

    def test_camera_bar_is_compact_without_camera_configuration_changes(self):
        source = (ROOT / "development" / "android-kiosk" / "app" / "src" / "main" / "java" / "com" / "eliterf" / "kiosk" / "CameraXCaptureActivity.kt").read_text()
        self.assertIn("dp(132), Gravity.BOTTOM", source)
        self.assertIn("dp(50)", source)
        self.assertIn("PreviewView.ScaleType.FIT_CENTER", source)
        self.assertIn("TARGET_WIDTH = 2560", source)
        self.assertIn("TARGET_HEIGHT = 1920", source)

    def test_frontend_requires_confirmed_photo_and_record_before_success(self):
        source = (ROOT / "static" / "kiosk.js").read_text()
        self.assertIn("data.photo_saved !== true", source)
        self.assertIn("data.record_saved !== true", source)
        self.assertIn('showMessage("");', source)
        self.assertIn("discardCapturedPhoto", source)

    def test_backend_contract_separates_photo_record_and_notification_status(self):
        source = (ROOT / "kiosk_server" / "server.py").read_text()
        self.assertIn('"photo_saved": final_photo.is_file()', source)
        self.assertIn('"record_saved": True', source)
        self.assertIn('"notification_status": notification_status', source)

    def test_native_capture_enters_review_before_async_photo_loading(self):
        source = (ROOT / "static" / "kiosk.js").read_text()
        ready = source.index("window.eliteRfNativeCaptureReady")
        load = source.index("loadNativeCapturedPhoto();", ready)
        self.assertIn("showNativeReviewLoading();", source[ready:load])
        self.assertIn('video.hidden = true;', source[source.index("function showNativeReviewLoading"):ready])
        self.assertIn("finishBtn.disabled = true;", source[source.index("function showNativeReviewLoading"):ready])

    def test_native_capture_does_not_restore_browser_video_placeholder(self):
        source = (ROOT / "static" / "kiosk.js").read_text()
        launch = source.index("function launchNativeCapture")
        stop = source.index("function stopCamera")
        self.assertIn("video.hidden = true;", source[launch:stop])
        stop_body = source[stop:source.index("function setPhotoState", stop)]
        self.assertNotIn("video.hidden = false;", stop_body)

    def test_visitor_flow_has_reconnect_overlay_and_friendly_recovery(self):
        html = (ROOT / "static" / "kiosk.html").read_text()
        css = (ROOT / "static" / "kiosk.css").read_text()
        source = (ROOT / "static" / "kiosk.js").read_text()
        self.assertIn('id="connectionOverlay" class="connection-overlay"', html)
        self.assertIn('id="connectionRetryBtn"', html)
        self.assertIn('.connection-overlay[hidden] { display:none; }', css)
        self.assertIn('window.addEventListener("offline"', source)
        self.assertIn('window.addEventListener("online"', source)
        self.assertIn('setConnectionStatus("reconnecting"', source)
        self.assertIn('function friendlyMessage(message, fallback)', source)
        self.assertIn('We could not complete the photo. Please try again or ask reception for help.', source)

    def test_visitor_flow_locks_transitions_and_cleans_abandoned_sessions(self):
        source = (ROOT / "static" / "kiosk.js").read_text()
        self.assertIn('let navigationLocked = false;', source)
        self.assertIn('let photoActionLocked = false;', source)
        self.assertIn('const ABANDONMENT_MS = 90_000;', source)
        self.assertIn('if (navigationLocked || submitting) return;', source)
        self.assertIn('if (submitting || photoActionLocked) return;', source)
        self.assertIn('finishBtn.setAttribute("aria-busy", "true");', source)
        self.assertIn('clearTimeout(abandonmentTimer);', source)
        self.assertIn('if (!submitting) resetKiosk();', source)
        self.assertIn('form.reset();', source)
        self.assertIn('closeEmployeePicker();', source)
        self.assertIn('setSelectedHost("");', source)
        self.assertIn('setTimeout(resetKiosk, 10000);', source)

    def test_native_camera_errors_are_safe_for_visitors(self):
        project_root = ROOT / "development" / "android-kiosk" / "app" / "src" / "main" / "java" / "com" / "eliterf" / "kiosk"
        source = (project_root / "CameraXCaptureActivity.kt").read_text()
        self.assertIn("private fun visitorCameraMessage(message: String): String", source)
        self.assertIn("statusText.text = visitorCameraMessage(message)", source)
        self.assertNotIn("showError(error.message ?:", source)


if __name__ == "__main__":
    unittest.main()
