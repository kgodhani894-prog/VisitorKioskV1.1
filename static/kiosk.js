const form = document.getElementById("checkinForm");
const stepOne = document.getElementById("stepOne");
const stepTwo = document.getElementById("stepTwo");
const stepThree = document.getElementById("stepThree");
const employeeSelect = document.getElementById("employeeSelect");
const employeePicker = document.getElementById("employeePicker");
const employeeOptions = document.getElementById("employeeOptions");
const emailInput = form.elements.email;
const emailMessage = document.getElementById("emailMessage");
const video = document.getElementById("video");
const canvas = document.getElementById("canvas");
const preview = document.getElementById("photoPreview");
const formMessage = document.getElementById("formMessage");
const cameraMessage = document.getElementById("cameraMessage");
const captureBtn = document.getElementById("captureBtn");
const retakeBtn = document.getElementById("retakeBtn");
const finishBtn = document.getElementById("finishBtn");
const photoBackBtn = document.getElementById("photoBackBtn");
const backToCheckinBtn = document.getElementById("backToCheckinBtn");
const clearFormBtn = document.getElementById("clearFormBtn");
const clearFormDialog = document.getElementById("clearFormDialog");
const cancelClearFormBtn = document.getElementById("cancelClearFormBtn");
const confirmClearFormBtn = document.getElementById("confirmClearFormBtn");
const nextBtn = document.getElementById("nextBtn");
const connectionOverlay = document.getElementById("connectionOverlay");
const connectionTitle = document.getElementById("connectionTitle");
const connectionDetail = document.getElementById("connectionDetail");
const connectionRetryBtn = document.getElementById("connectionRetryBtn");
const companyInput = form.elements.company;
const photoPage = document.getElementById("stepTwo");
const photoHeading = document.getElementById("photoHeading");
const photoInstruction = document.getElementById("photoInstruction");
const photoSecurity = document.getElementById("photoSecurity");
const cameraDebugMode = location.hash === "#camera-debug";
let cameraCalibrationMode = location.hash === "#camera-calibration";
let cameraCalibrationStarted = false;
let cameraDebugCaptureButton;
let cameraDebugOpenButton;
let cameraDebugStatus;
let capturedPhoto = "";
let stream;
let nativeCamera = false;
let resetTimer;
let submitting = false;
let pendingNativeRestored = false;
let pickerPointerActivation = false;
let navigationLocked = false;
let photoActionLocked = false;
let abandonmentTimer;
let employeeRetryTimer;
const ABANDONMENT_MS = 90_000;

function showMessage(message) { formMessage.textContent = message; formMessage.hidden = !message; }
function friendlyMessage(message, fallback) {
  const text = String(message || "").toLowerCase();
  if (text.includes("network") || text.includes("fetch") || text.includes("server") || text.includes("connection")) {
    return "The check-in service is temporarily unavailable. Please wait a moment and try again.";
  }
  if (text.includes("camera") || text.includes("capture") || text.includes("photo") || text.includes("jpeg")) {
    return "We could not complete the photo. Please try again or ask reception for help.";
  }
  return fallback;
}

function setConnectionStatus(state, detail) {
  connectionOverlay.dataset.state = state;
  connectionTitle.textContent = state === "lost" ? "Connection lost" : "Reconnecting…";
  connectionDetail.textContent = detail || "Please wait while we restore the check-in service.";
  connectionRetryBtn.hidden = state !== "lost";
  connectionOverlay.hidden = false;
}

function clearConnectionStatus() {
  connectionOverlay.hidden = true;
  connectionOverlay.removeAttribute("data-state");
}

function scheduleEmployeeRetry() {
  clearTimeout(employeeRetryTimer);
  employeeRetryTimer = setTimeout(loadEmployees, 5000);
}

function armAbandonmentTimer() {
  clearTimeout(abandonmentTimer);
  const hasDetails = [...form.elements].some(element => element.type !== "radio" && element.value?.trim()) ||
    [...form.querySelectorAll('input[type="radio"]')].some(element => element.checked);
  if (submitting || (!hasDetails && stepTwo.hidden)) return;
  abandonmentTimer = setTimeout(() => {
    if (!submitting) resetKiosk();
  }, ABANDONMENT_MS);
}

function setBusy(button, busy, label) {
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
  if (label) button.textContent = label;
}

function restoreNextButton() {
  navigationLocked = false;
  setBusy(nextBtn, false);
  nextBtn.innerHTML = 'Next <span aria-hidden="true">→</span>';
}
function setEmailMessage(message) {
  emailMessage.textContent = message;
  emailMessage.hidden = !message;
}

function validateEmail(showError) {
  const value = emailInput.value.trim();
  let message = "";
  if (!value) message = "Email is required.";
  else if (emailInput.validity.typeMismatch) message = "Enter a valid email address, such as name@example.com.";
  if (showError || !emailMessage.hidden) setEmailMessage(message);
  return !message;
}

function closeClearFormDialog() {
  clearFormDialog.hidden = true;
  clearFormBtn.focus();
}

function clearForm() {
  clearTimeout(abandonmentTimer);
  form.reset();
  setSelectedHost("");
  validateEmail(false);
  showMessage("");
  closeClearFormDialog();
}

function closeEmployeePicker() {
  employeeOptions.hidden = true;
  employeePicker.setAttribute("aria-expanded", "false");
}

function openEmployeePicker() {
  if (!employeeOptions.childElementCount) return;
  employeeOptions.hidden = false;
  employeePicker.setAttribute("aria-expanded", "true");
  requestAnimationFrame(positionEmployeePicker);
}

function positionEmployeePicker() {
  if (employeeOptions.hidden) return;
  const pickerBounds = employeePicker.getBoundingClientRect();
  const optionsHeight = Math.min(260, employeeOptions.scrollHeight + 16);
  const roomAbove = pickerBounds.top;
  const roomBelow = window.innerHeight - pickerBounds.bottom;
  employeeOptions.classList.toggle("opens-down", roomAbove < optionsHeight && roomBelow > roomAbove);
}

function setSelectedHost(value) {
  employeeSelect.value = value;
  employeePicker.textContent = selectedHostName();
  for (const option of employeeOptions.querySelectorAll('[role="option"]')) {
    option.setAttribute("aria-selected", String(option.dataset.value === value));
  }
}

function renderEmployeePicker(employees) {
  employeeOptions.replaceChildren();
  for (const employee of employees) {
    const option = document.createElement("button");
    option.type = "button";
    option.className = "host-option";
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", "false");
    option.dataset.value = employee.id;
    option.textContent = employee.name;
    option.addEventListener("click", () => {
      setSelectedHost(String(employee.id));
      closeEmployeePicker();
    });
    employeeOptions.appendChild(option);
  }
  setSelectedHost(employeeSelect.value);
}

function selectedHostName() { return employeeSelect.options[employeeSelect.selectedIndex]?.textContent || "Your host"; }

function setupCameraDebugPanel() {
  if (!cameraDebugMode) return;
  const panel = document.createElement("section");
  panel.className = "camera-debug-panel";
  panel.setAttribute("aria-label", "Development camera diagnostic");
  panel.innerHTML = `
    <strong>Development camera diagnostic</strong>
    <span>Hold an upright sign reading TOP ↑ and ELITE RF in front of the tablet.</span>
    <button type="button" id="cameraDebugOpen">Open camera step</button>
    <button type="button" id="cameraDebugCapture" disabled>Capture camera artifacts</button>
    <pre id="cameraDebugStatus" aria-live="polite">Waiting for the camera preview.</pre>`;
  document.body.append(panel);
  cameraDebugOpenButton = panel.querySelector("#cameraDebugOpen");
  cameraDebugCaptureButton = panel.querySelector("#cameraDebugCapture");
  cameraDebugStatus = panel.querySelector("#cameraDebugStatus");
  cameraDebugOpenButton.addEventListener("click", () => {
    const open = () => {
      if (employeeSelect.value) {
        document.getElementById("nextBtn").click();
      } else if (employeeSelect.options.length < 2) {
        setTimeout(open, 250);
      } else {
        setSelectedHost(employeeSelect.options[1].value);
        document.getElementById("nextBtn").click();
      }
    };
    open();
  });
  cameraDebugCaptureButton.addEventListener("click", () => {
    cameraDebugCaptureButton.disabled = true;
    cameraDebugStatus.textContent = "Capturing rendered preview and Camera2 still...";
    window.EliteRfNativeCamera?.captureDebugFrame();
  });
}

async function loadEmployees() {
  try {
    const response = await fetch("/api/employees");
    const data = await response.json();
    const selectedValue = employeeSelect.value;
    employeeSelect.replaceChildren(new Option("Select your host", ""));
    for (const employee of data.employees) {
      const option = document.createElement("option");
      option.value = employee.id;
      option.textContent = employee.name;
      employeeSelect.appendChild(option);
    }
    renderEmployeePicker(data.employees);
    if (data.employees.some(employee => String(employee.id) === selectedValue)) employeeSelect.value = selectedValue;
    setSelectedHost(employeeSelect.value);
    clearTimeout(employeeRetryTimer);
    clearConnectionStatus();
    restorePendingNativeCapture();
  } catch {
    showMessage("");
    setConnectionStatus(navigator.onLine === false ? "lost" : "reconnecting");
    scheduleEmployeeRetry();
  }
}

function validateDetails() {
  if (!validateEmail(true)) {
    emailInput.focus();
    return false;
  }
  for (const field of form.querySelectorAll("#stepOne input, #stepOne select")) {
    if (!field.checkValidity()) { field.reportValidity(); return false; }
  }
  return true;
}

function nativeCapturePayload() {
  return JSON.stringify(Object.fromEntries(new FormData(form).entries()));
}

function launchNativeCapture() {
  const bridge = window.EliteRfNativeCamera;
  if (!bridge?.launchCapture) return false;
  // The native activity owns the camera now. Keep the browser video surface
  // hidden so Android WebView cannot flash its media-error/play placeholder
  // underneath the native preview or during the return to review.
  video.pause();
  video.hidden = true;
  bridge.resetCaptureForRetry?.();
  const launched = bridge.launchCapture(nativeCapturePayload());
  if (launched !== true) {
    cameraMessage.textContent = "The camera is unavailable. Please try again or ask reception for help.";
    cameraMessage.hidden = false;
    restoreNextButton();
    return false;
  }
  return true;
}

function restorePendingNativeCapture() {
  if (pendingNativeRestored) return;
  const bridge = window.EliteRfNativeCamera;
  const raw = bridge?.pendingCapturePayload?.();
  if (!raw) return;
  try {
    const payload = JSON.parse(raw);
    for (const name of ["first_name", "last_name", "phone", "email", "company"]) {
      if (typeof payload[name] === "string" && form.elements[name]) form.elements[name].value = payload[name];
    }
    if (payload.us_citizen === "yes" || payload.us_citizen === "no") {
      form.querySelector(`input[name="us_citizen"][value="${payload.us_citizen}"]`).checked = true;
    }
    if (payload.employee_id) setSelectedHost(String(payload.employee_id));
    pendingNativeRestored = true;
    bridge.resetCaptureForRetry?.();
    returnToDetails();
    restoreNextButton();
    showMessage("The photo was not completed. Please try again.");
  } catch {
    pendingNativeRestored = true;
  }
}

async function startCamera() {
  cameraMessage.hidden = true;
  captureBtn.disabled = true;
  if (stream || nativeCamera) return;
  if (cameraCalibrationMode || !window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    nativeCamera = true;
    video.hidden = true;
    document.body.classList.add("native-camera-mode");
    window.EliteRfNativeCamera?.start(0, 0, 0, 0, cameraCalibrationMode);
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
    video.srcObject = stream;
    const track = stream.getVideoTracks()[0];
    if (!track || track.readyState !== "live") throw new Error("NotReadableError");
    await new Promise(resolve => {
      if (video.readyState >= 1) resolve();
      else video.addEventListener("loadedmetadata", resolve, { once: true });
    });
    await video.play();
    if (!video.srcObject || video.srcObject.getVideoTracks().length === 0 || video.videoWidth <= 0 || video.videoHeight <= 0 || video.readyState < 2) {
      throw new Error("NotReadableError");
    }
    track.addEventListener("ended", () => {
      window.eliteRfNativeCameraError("Camera unavailable.\nPlease notify the front desk.");
    }, { once: true });
    captureBtn.disabled = false;
  } catch {
    stopCamera();
    setPhotoState("live");
    cameraMessage.textContent = "The camera could not start. Please try again or ask reception for help.";
    cameraMessage.hidden = false;
  }
}

async function finishCameraCalibration() {
  cameraCalibrationMode = false;
  window.EliteRfNativeCamera?.stop();
  nativeCamera = false;
  document.body.classList.remove("native-camera-mode");
  setPhotoState("live");
  cameraMessage.hidden = true;
  history.replaceState(null, "", location.pathname + location.search);
  // Let the native controller finish removing its calibration surface before
  // asking it to create the normal visitor preview again.
  await new Promise(resolve => setTimeout(resolve, 100));
  // SAVE AND LOCK stops the calibration controller before returning here.
  // Start the same visitor camera path used by the normal photo flow and let
  // its readiness callback enable TAKE PHOTO only after live pixels arrive.
  if (!photoPage.hidden && !capturedPhoto) await startCamera();
}

window.eliteRfFinishCalibration = finishCameraCalibration;
window.eliteRfCancelCalibration = finishCameraCalibration;

function stopCamera() {
  // Always close the native controller, even if the JS state was already reset.
  window.EliteRfNativeCamera?.stop();
  nativeCamera = false;
  document.body.classList.remove("native-camera-mode");
  stream?.getTracks().forEach(track => track.stop());
  video.srcObject = null;
  stream = undefined;
}

function setPhotoState(state) {
  photoPage.dataset.reviewing = String(state === "review");
  if (state === "review") {
    photoHeading.textContent = "Is this photo okay?";
    photoInstruction.textContent = "Make sure your face is clearly visible.";
    photoSecurity.hidden = true;
    captureBtn.hidden = true;
    photoBackBtn.hidden = true;
    retakeBtn.hidden = false;
    finishBtn.hidden = false;
    finishBtn.disabled = false;
    finishBtn.textContent = "USE PHOTO";
    finishBtn.removeAttribute("aria-busy");
    return;
  }
  if (state === "processing") {
    photoHeading.textContent = "Completing your check-in…";
    photoInstruction.textContent = "Completing your check-in…\nPlease wait.";
    photoSecurity.hidden = true;
    captureBtn.hidden = true;
    photoBackBtn.hidden = true;
    retakeBtn.hidden = true;
    finishBtn.hidden = true;
    finishBtn.removeAttribute("aria-busy");
    return;
  }
  photoHeading.textContent = "Please take your photo";
  photoInstruction.textContent = "Look at the camera and center your face and shoulders in the frame.";
  photoSecurity.hidden = false;
  captureBtn.hidden = false;
  photoBackBtn.hidden = false;
  photoBackBtn.disabled = false;
  captureBtn.disabled = true;
  captureBtn.textContent = "TAKE PHOTO";
  captureBtn.removeAttribute("aria-busy");
  retakeBtn.hidden = true;
  retakeBtn.disabled = false;
  retakeBtn.textContent = "RETAKE PHOTO";
  retakeBtn.removeAttribute("aria-busy");
  finishBtn.hidden = true;
  finishBtn.disabled = false;
}

function showCapturedPhoto(dataUrl) {
  capturedPhoto = dataUrl;
  photoActionLocked = false;
  photoPage.dataset.reviewLoading = "false";
  preview.src = capturedPhoto;
  preview.hidden = false;
  video.hidden = true;
  setPhotoState("review");
  retakeBtn.disabled = false;
  stopCamera();
}

function showNativeReviewLoading() {
  photoPage.dataset.reviewLoading = "true";
  setPhotoState("review");
  photoHeading.textContent = "Preparing your photo…";
  photoInstruction.textContent = "Please wait a moment.";
  video.hidden = true;
  retakeBtn.disabled = true;
  finishBtn.disabled = true;
}

window.eliteRfNativeCameraReady = () => {
  if (nativeCamera && !photoPage.hidden && !capturedPhoto) {
    photoActionLocked = false;
    setBusy(captureBtn, false);
    captureBtn.textContent = "TAKE PHOTO";
  }
  if (cameraDebugCaptureButton) {
    cameraDebugCaptureButton.disabled = false;
    cameraDebugStatus.textContent = "Live preview frame ready. Capture when the sign is upright and visible.";
  }
};

window.eliteRfNativeCameraDebugResult = (message) => {
  if (cameraDebugStatus) cameraDebugStatus.textContent = message;
};

window.eliteRfNativeCameraResult = (dataUrl) => {
  showCapturedPhoto(dataUrl);
};

function showNativeReviewPhoto(dataUrl) {
  document.body.classList.add("photo-mode");
  stepOne.hidden = true;
  stepTwo.hidden = false;
  showCapturedPhoto(dataUrl);
}

window.eliteRfNativeCaptureResult = (dataUrl) => showNativeReviewPhoto(dataUrl);

async function loadNativeCapturedPhoto() {
  const bridge = window.EliteRfNativeCamera;
  const size = Number(bridge?.capturedPhotoSize?.() || 0);
  if (!Number.isInteger(size) || size <= 0) {
    window.eliteRfNativeCaptureError?.("The captured photo could not be read. Please try again.");
    return;
  }
  const chunks = [];
  for (let offset = 0; offset < size; offset += 49152) {
    const chunk = bridge.capturedPhotoChunk(offset, Math.min(49152, size - offset));
    if (!chunk) {
      window.eliteRfNativeCaptureError?.("The captured photo could not be read. Please try again.");
      return;
    }
    chunks.push(chunk);
    await Promise.resolve();
  }
  showNativeReviewPhoto(`data:image/jpeg;base64,${chunks.join("")}`);
}

window.eliteRfNativeCaptureReady = () => {
  showNativeReviewLoading();
  loadNativeCapturedPhoto();
};

window.eliteRfNativeCaptureCancelled = () => {
  if (capturedPhoto || submitting ||
      (photoPage.dataset.reviewing === "true" && photoPage.dataset.reviewLoading !== "true")) return;
  returnToDetails();
};

window.eliteRfNativeCaptureError = (message) => {
  if (capturedPhoto || submitting ||
      (photoPage.dataset.reviewing === "true" && photoPage.dataset.reviewLoading !== "true")) return;
  returnToDetails();
  showMessage(friendlyMessage(message, "The photo was not completed. Please try again."));
};

window.eliteRfNativeCameraError = (message) => {
  stopCamera();
  setPhotoState("live");
  captureBtn.disabled = true;
  photoActionLocked = false;
  cameraMessage.textContent = friendlyMessage(message, "The camera could not start. Please try again or ask reception for help.");
  cameraMessage.hidden = false;
};

function resetKiosk() {
  clearTimeout(resetTimer);
  clearTimeout(abandonmentTimer);
  document.body.classList.remove("photo-mode");
  document.body.classList.remove("completion-mode");
  stopCamera();
  window.EliteRfNativeCamera?.discardCapturedPhoto?.();
  form.reset();
  closeEmployeePicker();
  setSelectedHost("");
  validateEmail(false);
  capturedPhoto = "";
  preview.src = "";
  preview.hidden = true;
  video.hidden = false;
  photoPage.dataset.reviewing = "false";
  photoPage.dataset.reviewLoading = "false";
  cameraMessage.hidden = true;
  showMessage("");
  setPhotoState("live");
  submitting = false;
  navigationLocked = false;
  photoActionLocked = false;
  restoreNextButton();
  document.getElementById("visitorName").textContent = "";
  document.getElementById("hostName").textContent = "";
  stepThree.hidden = true;
  form.hidden = false;
  stepTwo.hidden = true;
  stepOne.hidden = false;
}

function returnToDetails() {
  if (submitting) return;
  clearTimeout(resetTimer);
  clearTimeout(abandonmentTimer);
  stopCamera();
  window.EliteRfNativeCamera?.resetCaptureForRetry?.();
  window.EliteRfNativeCamera?.discardCapturedPhoto?.();
  document.body.classList.remove("photo-mode");
  document.body.classList.remove("completion-mode");
  capturedPhoto = "";
  preview.src = "";
  preview.hidden = true;
  video.hidden = false;
  photoPage.dataset.reviewing = "false";
  photoPage.dataset.reviewLoading = "false";
  cameraMessage.textContent = "";
  cameraMessage.hidden = true;
  setPhotoState("live");
  navigationLocked = false;
  photoActionLocked = false;
  restoreNextButton();
  stepTwo.hidden = true;
  stepOne.hidden = false;
}

window.eliteRfResetKiosk = resetKiosk;

nextBtn.addEventListener("click", async () => {
  if (navigationLocked || submitting) return;
  if (!validateDetails()) return;
  navigationLocked = true;
  setBusy(nextBtn, true, "Opening camera…");
  armAbandonmentTimer();
  showMessage("");
  if (window.EliteRfNativeCamera?.launchCapture) {
    if (!launchNativeCapture()) restoreNextButton();
    return;
  }
  restoreNextButton();
  showMessage("The camera is unavailable. Please ask reception for help.");
});

emailInput.addEventListener("input", () => validateEmail(false));
emailInput.addEventListener("blur", () => validateEmail(true));

employeePicker.addEventListener("click", () => {
  pickerPointerActivation = false;
  const isOpen = !employeeOptions.hidden;
  if (isOpen) closeEmployeePicker();
  else openEmployeePicker();
});

employeePicker.addEventListener("pointerdown", () => {
  pickerPointerActivation = true;
});

// Make the visible picker the next keyboard destination after the last text field.
// Opening on focus lets Gboard's Next action show the host list directly.
employeePicker.addEventListener("focus", () => {
  if (!pickerPointerActivation && employeeOptions.hidden) openEmployeePicker();
});

function moveFromCompanyToHost(event) {
  if (event.key !== "Enter" && event.key !== "Tab") return;
  event.preventDefault();
  openEmployeePicker();
  employeePicker.focus();
}

companyInput.addEventListener("keydown", moveFromCompanyToHost);
companyInput.addEventListener("keyup", event => {
  if (event.key === "Enter") moveFromCompanyToHost(event);
});

document.addEventListener("click", event => {
  if (!event.target.closest(".host-picker")) closeEmployeePicker();
});
window.addEventListener("resize", positionEmployeePicker);

document.getElementById("captureBtn").addEventListener("click", () => {
  if (submitting || photoActionLocked) return;
  photoActionLocked = true;
  setBusy(captureBtn, true, "Taking photo…");
  if (window.EliteRfNativeCamera?.launchCapture && !nativeCamera && !stream) {
    if (!launchNativeCapture()) {
      photoActionLocked = false;
      setPhotoState("live");
    }
    return;
  }
  if (nativeCamera) {
    window.EliteRfNativeCamera?.capture();
    return;
  }
  if (!video.videoWidth) {
    photoActionLocked = false;
    setPhotoState("live");
    cameraMessage.textContent = "The camera is still starting. Please try again in a moment.";
    cameraMessage.hidden = false;
    return;
  }
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const context = canvas.getContext("2d");
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  showCapturedPhoto(canvas.toDataURL("image/jpeg", 0.85));
});

retakeBtn.addEventListener("click", async () => {
  if (submitting || photoActionLocked) return;
  photoActionLocked = true;
  setBusy(retakeBtn, true, "Opening camera…");
  capturedPhoto = "";
  window.EliteRfNativeCamera?.discardCapturedPhoto?.();
  preview.hidden = true;
  video.hidden = false;
  photoPage.dataset.reviewLoading = "false";
  setPhotoState("live");
  if (window.EliteRfNativeCamera?.launchCapture && !nativeCamera && !stream) {
    if (!launchNativeCapture()) {
      photoActionLocked = false;
      setPhotoState("review");
    }
    return;
  }
  await startCamera();
  photoActionLocked = false;
});

finishBtn.addEventListener("click", submitCheckin);

photoBackBtn.addEventListener("click", returnToDetails);

clearFormBtn.addEventListener("click", () => {
  clearFormDialog.hidden = false;
  cancelClearFormBtn.focus();
});

cancelClearFormBtn.addEventListener("click", closeClearFormDialog);
confirmClearFormBtn.addEventListener("click", clearForm);

async function submitCheckin() {
  if (!capturedPhoto || submitting) return;
  submitting = true;
  clearTimeout(abandonmentTimer);
  captureBtn.disabled = true;
  finishBtn.disabled = true;
  finishBtn.setAttribute("aria-busy", "true");
  finishBtn.textContent = "Submitting…";
  setPhotoState("processing");
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.photo = capturedPhoto;
  const visitorName = payload.first_name;
  const hostName = selectedHostName();
  if (cameraDebugMode) window.EliteRfNativeCamera?.saveDebugDataUrl(capturedPhoto);
  let data;
  try {
    const response = await fetch("/api/checkins", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
    data = await response.json();
    if (!data.ok) throw new Error(data.error || "Check-in failed.");
    if (data.photo_saved !== true || data.record_saved !== true) {
      throw new Error("Your photo and visitor record could not be confirmed. Please try again.");
    }
  } catch (error) {
    submitting = false;
    setPhotoState("review");
    showMessage(friendlyMessage(error.message, "We could not complete your check-in. Please try again or ask reception for help."));
    return;
  }
  form.hidden = true;
  // A capture/review error must never leak onto the successful completion page.
  // Cleanup is intentionally after the confirmed backend response above.
  showMessage("");
  document.body.classList.add("completion-mode");
  document.getElementById("visitorName").textContent = visitorName;
  document.getElementById("hostName").textContent = hostName;
  stepThree.hidden = false;
  window.EliteRfNativeCamera?.discardCapturedPhoto?.();
  stopCamera();
  resetTimer = setTimeout(resetKiosk, 10000);
}

backToCheckinBtn.addEventListener("click", resetKiosk);

form.addEventListener("submit", event => { event.preventDefault(); submitCheckin(); });

form.addEventListener("input", armAbandonmentTimer);
form.addEventListener("change", armAbandonmentTimer);
window.addEventListener("offline", () => setConnectionStatus("lost", "Please check the network connection. We will keep trying automatically."));
window.addEventListener("online", () => {
  setConnectionStatus("reconnecting", "Connection restored. Reconnecting to the check-in service…");
  loadEmployees();
});
connectionRetryBtn.addEventListener("click", () => {
  setConnectionStatus("reconnecting");
  loadEmployees();
});

function openCameraCalibrationStep() {
  if (!cameraCalibrationMode || cameraCalibrationStarted) return;
  cameraCalibrationStarted = true;
  showMessage("");
  document.body.classList.add("photo-mode");
  stepOne.hidden = true;
  stepTwo.hidden = false;
  setPhotoState("live");
  startCamera();
}

window.addEventListener("pagehide", stopCamera);
setupCameraDebugPanel();
loadEmployees();
openCameraCalibrationStep();
setInterval(loadEmployees, 30000);
