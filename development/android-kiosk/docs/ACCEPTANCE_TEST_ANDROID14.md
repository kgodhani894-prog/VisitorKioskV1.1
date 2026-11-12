# MESWAO Android 14 Acceptance Tests

These are required physical checks for the MESWAO tablet. Results below reflect the
current controlled debug validation; production release acceptance remains separate.

- Device Owner assignment succeeds and the app reports true Device Owner status.
- `LOCK_TASK_MODE_LOCKED` is active; Home, Recents, Back, notifications, Quick Settings, Settings, and other apps cannot be reached.
- The ordinary power menu and volume controls are unavailable; hardware forced shutdown remains an unavoidable exception.
- Admin routes, HTTP, custom schemes, external URLs, downloads, and certificate errors are blocked.
- Camera and keyboard work on every field without repeated permission prompts.
- The display remains awake during an inactivity period longer than the tablet's normal screen timeout.
- Android's camera indicator is inactive on details, completion, error, and administrator screens; it is active only while the photo preview is open.
- A fictional check-in reaches the company backend, confirms after 15 seconds, and an abandoned form resets after 60 seconds.
- Wi-Fi interruption, backend restart, WebView renderer restart, Activity recreation, and tablet reboot recover to the kiosk.
- Five taps in the upper-left branding area require the correct PIN; five failures lock for five minutes; recovery code works.

## Native Camera Capture Validation

- The capture intent resolves to `com.mediatek.camera/.CaptureActivity`.
- `NEXT` opens that activity directly while `LOCK_TASK_MODE_LOCKED` remains active; no Elite RF camera page appears first.
- MESWAO camera ID `0` is Back and camera ID `1` is Front. The camera app does not honor the standard front-camera intent hints on this tablet.
- One-time setup: open the native Camera from administrator maintenance, switch to the front lens using the manufacturer control, then return to the kiosk. The next Elite RF capture intent reopened camera ID `1` (Front).
- Native capture returned a valid full-resolution JPEG and the Elite RF review screen exposed `RETAKE PHOTO` and `USE PHOTO`.
- Android Back from review returned to the clean welcome form and removed the app-private capture file.
- Home, Recents, notification shade, and the native gallery thumbnail remained unable to leave the locked capture flow during controlled checks.
- The MESWAO camera rejects the app-private FileProvider output and rejects `IS_PENDING` MediaStore output. The device-specific handoff uses a randomized hidden temporary MediaStore item, copies it into app-private cache, and deletes the shared item immediately after capture.
- Exact camera framing and front-facing appearance require direct visual confirmation because `FLAG_SECURE` prevents ADB screenshots.
- Real email delivery was not tested; disposable/synthetic tests must be used unless a controlled email test is explicitly authorized.
- A short power-button press wakes immediately back to the locked kiosk; the ordinary power menu remains unavailable.
- Maintenance mode relocks automatically after five minutes.
- Production build is not debuggable and visitor PII is absent from logs.
- Before final activation, ADB verifies the exact Device Owner, foreground activity, and `LOCK_TASK_MODE_LOCKED`.
- The authenticated **Activate production lockdown** action succeeds, reports production lockdown active, and disables further USB debugging access.
- Run a continuous 72-hour kiosk test.

Record date, tablet serial, APK version, backend URL, and each pass/fail result in the company pilot record.

