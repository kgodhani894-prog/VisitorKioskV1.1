const loginPanel = document.getElementById("loginPanel");
const dashboard = document.getElementById("dashboard");
const authTitle = document.getElementById("authTitle");
const authHelp = document.getElementById("authHelp");
const setupNotice = document.getElementById("setupNotice");
const confirmPassword = document.getElementById("confirmPassword");
const authBtn = document.getElementById("authBtn");
const visitsBox = document.getElementById("visits");
const employeesBox = document.getElementById("employees");
const healthBox = document.getElementById("health");
const emptyState = document.getElementById("emptyState");
const emptyEmployeesState = document.getElementById("emptyEmployeesState");
const photoDialog = document.getElementById("photoDialog");
const dialogPhoto = document.getElementById("dialogPhoto");
const employeeForm = document.getElementById("employeeForm");
const employeeId = document.getElementById("employeeId");
const employeeName = document.getElementById("employeeName");
const employeeEmail = document.getElementById("employeeEmail");
const employeeActive = document.getElementById("employeeActive");
const notificationForm = document.getElementById("notificationForm");
const notificationsEnabled = document.getElementById("notificationsEnabled");
const messageTemplate = document.getElementById("messageTemplate");
const emailStatus = document.getElementById("emailStatus");
const testEmailForm = document.getElementById("testEmailForm");
const testEmailEmployee = document.getElementById("testEmailEmployee");
const m365ConnectionStatus = document.getElementById("m365ConnectionStatus");
const m365SetupHelp = document.getElementById("m365SetupHelp");
const connectM365Btn = document.getElementById("connectM365Btn");
const disconnectM365Btn = document.getElementById("disconnectM365Btn");
const m365SetupForm = document.getElementById("m365SetupForm");
const m365SetupStatus = document.getElementById("m365SetupStatus");
const generateM365CertBtn = document.getElementById("generateM365CertBtn");
const downloadM365CertLink = document.getElementById("downloadM365CertLink");
const m365CertificateFile = document.getElementById("m365CertificateFile");
const m365PrivateKeyFile = document.getElementById("m365PrivateKeyFile");
const uploadM365CertBtn = document.getElementById("uploadM365CertBtn");

let ownerSetupMode = false;
let setupAllowed = true;
let employeeCache = [];

document.getElementById("authBtn").addEventListener("click", submitAuth);
document.getElementById("password").addEventListener("keydown", event => { if (event.key === "Enter") submitAuth(); });
confirmPassword.addEventListener("keydown", event => { if (event.key === "Enter") submitAuth(); });
document.getElementById("refreshBtn").addEventListener("click", loadDashboard);
document.getElementById("exportBtn").addEventListener("click", () => { window.location.href = "/api/admin/visits/export.csv"; });
document.getElementById("refreshEmployeesBtn").addEventListener("click", loadEmployees);
document.getElementById("closePhoto").addEventListener("click", () => photoDialog.close());
document.getElementById("cancelEmployeeBtn").addEventListener("click", resetEmployeeForm);
employeeForm.addEventListener("submit", saveEmployee);
document.querySelectorAll(".tab-button[data-tab]").forEach(buttonElement => buttonElement.addEventListener("click", () => showTab(buttonElement.dataset.tab)));
notificationForm.addEventListener("submit", saveNotificationSettings);
testEmailForm.addEventListener("submit", sendTestEmail);
connectM365Btn.addEventListener("click", () => { window.location.href = "/api/admin/notifications/connect"; });
disconnectM365Btn.addEventListener("click", disconnectMicrosoft365);
m365SetupForm.addEventListener("submit", saveMicrosoft365Setup);
generateM365CertBtn.addEventListener("click", generateMicrosoft365Certificate);
uploadM365CertBtn.addEventListener("click", uploadMicrosoft365Certificate);

const m365Result = new URLSearchParams(window.location.search).get("m365");
if (m365Result) {
  const messages = {connected: "Microsoft 365 account connected.", disconnected: "Microsoft 365 account disconnected.", error: "Microsoft 365 could not connect. Check the host setup and try again."};
  window.setTimeout(() => alert(messages[m365Result] || messages.error), 0);
  window.history.replaceState({}, "", window.location.pathname);
}

function enterSetupMode() {
  ownerSetupMode = true;
  authBtn.disabled = false;
  setupNotice.hidden = true;
  authTitle.textContent = "Set owner password";
  authHelp.textContent = "Choose the initial password for the kiosk owner. This is a one-time setup.";
  document.getElementById("password").placeholder = "Owner password";
  document.getElementById("password").autocomplete = "new-password";
  confirmPassword.hidden = false;
  confirmPassword.value = "";
  authBtn.textContent = "Save owner password";
}

function enterRemoteSetupMode() {
  ownerSetupMode = true;
  authBtn.disabled = true;
  setupNotice.hidden = false;
  setupNotice.textContent = "First-run setup must be completed from the host computer attached to this kiosk.";
  authTitle.textContent = "Host computer setup required";
  authHelp.textContent = "This browser can see the kiosk, but the one-time owner setup can only be finished on the host computer.";
  document.getElementById("password").placeholder = "Owner password";
  document.getElementById("password").autocomplete = "new-password";
  confirmPassword.hidden = false;
  confirmPassword.value = "";
  authBtn.textContent = "Save owner password";
}

function enterLoginMode() {
  ownerSetupMode = false;
  authBtn.disabled = false;
  setupNotice.hidden = true;
  setupNotice.textContent = "";
  authTitle.textContent = "Admin sign in";
  authHelp.textContent = "Use the password set for this kiosk system.";
  document.getElementById("password").placeholder = "Admin password";
  document.getElementById("password").autocomplete = "current-password";
  confirmPassword.hidden = true;
  confirmPassword.value = "";
  authBtn.textContent = "Log in";
}

function showAdminDashboard() {
  loginPanel.hidden = true;
  dashboard.hidden = false;
  loadDashboard();
  loadEmployees();
  if (m365Result) showTab("notifications");
  else loadNotificationSettings();
}

async function loadAuthState() {
  const response = await fetch("/api/admin/setup-state");
  if (!response.ok) return;
  const data = await response.json();
  setupAllowed = data.setup_allowed !== false;
  if (data.setup_required && setupAllowed) enterSetupMode();
  else if (data.setup_required) enterRemoteSetupMode();
  else {
    const sessionResponse = await fetch("/api/admin/settings");
    if (sessionResponse.ok) {
      showAdminDashboard();
      return;
    }
    enterLoginMode();
  }
}

async function submitAuth() {
  const password = document.getElementById("password").value;
  const payload = ownerSetupMode ? {password, confirm_password: confirmPassword.value} : {password};
  const endpoint = ownerSetupMode ? "/api/admin/setup" : "/api/admin/login";
  const response = await fetch(endpoint, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 403 && data.error && data.error.includes("host computer")) {
      enterRemoteSetupMode();
    }
    if (response.status === 409 && data.error && data.error.includes("Owner password setup is required")) {
      enterSetupMode();
    }
    if (response.status === 409 && data.error && data.error.includes("already set")) {
      enterLoginMode();
    }
    return alert(data.error || (ownerSetupMode ? "Could not save owner password." : "Incorrect password."));
  }
  if (ownerSetupMode && !setupAllowed) return alert("First-run setup must be completed from the host computer.");
  showAdminDashboard();
}

function showTab(tab) {
  document.getElementById("visitsPanel").hidden = tab !== "visits";
  document.getElementById("employeesPanel").hidden = tab !== "employees";
  document.getElementById("notificationsPanel").hidden = tab !== "notifications";
  document.querySelectorAll(".tab-button").forEach(buttonElement => buttonElement.classList.toggle("active", buttonElement.dataset.tab === tab));
  if (tab === "employees") loadEmployees();
  if (tab === "notifications") loadNotificationSettings();
}

function textCell(value, className = "") { const cell = document.createElement("td"); cell.textContent = value || "—"; if (className) cell.className = className; return cell; }
function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}
function localTime(timestamp) {
  const date = new Date(`${timestamp.replace(" ", "T")}Z`);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString("en-US", {timeZone:"America/Chicago", dateStyle:"short", timeStyle:"medium"});
}
function button(label, className, action) { const element = document.createElement("button"); element.type = "button"; element.textContent = label; element.className = className; element.addEventListener("click", action); return element; }

async function loadDashboard() {
  const healthResponse = await fetch("/api/admin/health");
  if (!healthResponse.ok) return logoutView();
  const health = await healthResponse.json();
  const storageMessages = (health.storage || []).filter(item => item.status !== "ok").map(item => {
    if (item.status === "unknown") return `${item.area} storage capacity check unavailable`;
    if (item.status === "unconfigured") return "Secondary backup destination not configured";
    if (item.status === "unmounted") return "Secondary backup destination is not mounted; backup copy paused";
    if (item.area === "backup freshness" && item.status === "missing") return "Backup critical: no successful backup has been recorded";
    if (item.area === "backup freshness" && item.status === "unknown") return "Backup status unavailable";
    if (item.area === "backup freshness" && item.status === "warning") return "Backup warning: latest success is over 30 hours old";
    if (item.area === "backup freshness" && item.status === "critical") return "Backup critical: latest success is at least 48 hours old";
    return `${item.area} storage ${item.status}: ${item.used_percent}% used (${formatBytes(item.free_bytes)} free)`;
  });
  if (health.pending_photo_deletions) storageMessages.push(`${health.pending_photo_deletions} photo deletion(s) awaiting cleanup`);
  healthBox.textContent = `${health.visits} total visitor records · ${health.employees} employees · ${health.failed_notifications} failed notifications${storageMessages.length ? ` · ${storageMessages.join(" · ")}` : " · Storage capacity normal"}`;
  healthBox.classList.toggle("storage-warning", storageMessages.some(message => message.includes("warning")));
  healthBox.classList.toggle("storage-critical", storageMessages.some(message => message.includes("critical") || message.includes("unavailable") || message.includes("not configured") || message.includes("not mounted") || message.includes("awaiting cleanup")));
  const response = await fetch("/api/admin/visits");
  const data = await response.json();
  visitsBox.replaceChildren();
  emptyState.hidden = data.visits.length !== 0;
  for (const visit of data.visits) visitsBox.appendChild(buildVisitRow(visit));
}

function buildVisitRow(visit) {
  const row = document.createElement("tr");
  row.appendChild(textCell(localTime(visit.created_at))); row.appendChild(textCell(visit.first_name, "visitor-name")); row.appendChild(textCell(visit.last_name, "visitor-name")); row.appendChild(textCell(visit.phone)); row.appendChild(textCell(visit.email)); row.appendChild(textCell(visit.company)); row.appendChild(textCell(visit.employee_name)); row.appendChild(textCell(visit.kiosk_id));
  const photoCell = document.createElement("td"); photoCell.appendChild(button("View", "table-button", () => { dialogPhoto.src = `/photos/${encodeURIComponent(visit.photo_filename)}`; photoDialog.showModal(); })); row.appendChild(photoCell);
  const actionsCell = document.createElement("td"); actionsCell.appendChild(button("Delete", "table-button delete", () => deleteVisit(visit))); row.appendChild(actionsCell);
  return row;
}

async function deleteVisit(visit) {
  const name = `${visit.first_name} ${visit.last_name}`.trim();
  if (!confirm(`Permanently delete ${name || "this visitor"} and its photo from the live kiosk? Existing backup copies may remain until normal backup rotation.`)) return;
  const response = await fetch(`/api/admin/visits/${visit.id}`, {
    method: "DELETE",
    headers: {"X-Kiosk-Admin-Action": "delete-visit"},
  });
  const data = await response.json();
  if (!response.ok && response.status !== 202) return alert(data.error || "Could not delete this visitor record.");
  if (data.photo_cleanup_pending) {
    alert("The visitor record was deleted. Photo removal is queued and will retry automatically; check the dashboard health warning if it remains.");
  } else {
    alert("The visitor record and photo were deleted from the live kiosk. Existing backup copies will expire through normal backup rotation.");
  }
  await loadDashboard();
}

async function loadEmployees() {
  const response = await fetch("/api/admin/employees");
  if (!response.ok) return logoutView();
  const data = await response.json();
  employeeCache = data.employees;
  employeesBox.replaceChildren();
  emptyEmployeesState.hidden = data.employees.length !== 0;
  for (const employee of data.employees) employeesBox.appendChild(buildEmployeeRow(employee));
  renderTestEmailEmployees();
}

function buildEmployeeRow(employee) {
  const row = document.createElement("tr");
  row.appendChild(textCell(employee.name, "visitor-name")); row.appendChild(textCell(employee.email)); row.appendChild(textCell("Email"));
  const activeCell = document.createElement("td"); const active = document.createElement("span"); active.className = `status ${employee.active ? "active" : "inactive"}`; active.textContent = employee.active ? "Active" : "Inactive"; activeCell.appendChild(active); row.appendChild(activeCell);
  const actions = document.createElement("td");
  actions.appendChild(button("Edit", "table-button", () => editEmployee(employee)));
  if (employee.active) {
    actions.appendChild(button("Inactivate", "table-button", () => inactivateEmployee(employee)));
    actions.appendChild(button("Remove", "table-button delete", () => removeEmployee(employee)));
  }
  else actions.appendChild(button("Reactivate", "table-button", () => reactivateEmployee(employee)));
  row.appendChild(actions);
  return row;
}

function editEmployee(employee) {
  employeeId.value = employee.id; employeeName.value = employee.name; employeeEmail.value = employee.email; employeeActive.checked = Boolean(employee.active);
  document.getElementById("employeeFormTitle").textContent = `Edit ${employee.name}`;
  document.getElementById("saveEmployeeBtn").textContent = "Save changes";
  document.getElementById("cancelEmployeeBtn").hidden = false;
  document.getElementById("employeeName").focus();
}

function resetEmployeeForm() {
  employeeForm.reset(); employeeId.value = ""; employeeActive.checked = true;
  document.getElementById("employeeFormTitle").textContent = "Add an employee";
  document.getElementById("saveEmployeeBtn").textContent = "Add employee";
  document.getElementById("cancelEmployeeBtn").hidden = true;
}

async function saveEmployee(event) {
  event.preventDefault();
  const payload = {name:employeeName.value, email:employeeEmail.value, active:employeeActive.checked};
  const editing = Boolean(employeeId.value);
  const url = editing ? `/api/admin/employees/${employeeId.value}` : "/api/admin/employees";
  const response = await fetch(url, {method: editing ? "PATCH" : "POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) return alert(data.error || "Could not save employee.");
  resetEmployeeForm(); loadEmployees(); loadDashboard();
}

async function removeEmployee(employee) {
  if (!confirm(`Are you sure you want to permanently remove ${employee.name}? Their past visitor records will remain, but this employee will no longer be stored.`)) return;
  const response = await fetch(`/api/admin/employees/${employee.id}`, {method:"DELETE"});
  const data = await response.json();
  if (!response.ok || !data.ok) return alert(data.error || "Could not remove employee.");
  alert("Employee permanently removed.");
  resetEmployeeForm(); loadEmployees(); loadDashboard();
}

async function inactivateEmployee(employee) {
  if (!confirm(`Are you sure you want to inactivate ${employee.name}? They will disappear from the visitor kiosk but their records will be kept.`)) return;
  const response = await fetch(`/api/admin/employees/${employee.id}`, {method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({...employee, active:false})});
  const data = await response.json();
  if (!response.ok) return alert(data.error || "Could not inactivate employee.");
  loadEmployees(); loadDashboard();
}

async function reactivateEmployee(employee) {
  const response = await fetch(`/api/admin/employees/${employee.id}`, {method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({...employee, active:true})});
  if (!response.ok) return alert("Could not reactivate employee.");
  loadEmployees(); loadDashboard();
}

function logoutView() { dashboard.hidden = true; loginPanel.hidden = false; loadAuthState(); }

document.getElementById("logoutBtn").addEventListener("click", async () => { await fetch("/api/admin/logout", {method:"POST"}); logoutView(); });

async function loadNotificationSettings() {
  const response = await fetch("/api/admin/settings");
  if (!response.ok) return logoutView();
  const data = await response.json();
  notificationsEnabled.checked = Boolean(data.notifications.enabled);
  messageTemplate.value = data.notifications.message_template || "";
  await loadEmailReadiness();
  renderTestEmailEmployees();
}

async function loadEmailReadiness() {
  const response = await fetch("/api/admin/notifications/readiness");
  if (!response.ok) return;
  const data = await response.json();
  const connection = data.connection || {};
  const oauthSetup = data.oauth_setup || {};
  const details = data.setup_details || {};
  if (data.test_mode) {
    m365SetupForm.querySelectorAll("input,button").forEach(element => { element.disabled = true; });
    m365SetupStatus.textContent = "Microsoft account linking and email are intentionally disabled in safe test mode.";
  } else {
    m365SetupForm.querySelectorAll("input,button").forEach(element => { element.disabled = false; });
    const tenantField = document.getElementById("m365TenantId");
    const clientField = document.getElementById("m365ClientId");
    const redirectField = document.getElementById("m365RedirectUri");
    if (!tenantField.value) tenantField.value = details.tenant_id || "";
    if (!clientField.value) clientField.value = details.client_id || "";
    if (!document.getElementById("m365RedirectUri").value) {
      redirectField.value = details.redirect_uri || (window.location.protocol === "https:" ? `${window.location.origin}/oauth/microsoft/callback` : "");
    }
    tenantField.dataset.saved = details.tenant_id || "";
    clientField.dataset.saved = details.client_id || "";
    redirectField.dataset.saved = details.redirect_uri || "";
    const appDetailsReady = Boolean(oauthSetup.tenant_configured && oauthSetup.application_configured && oauthSetup.redirect_ready);
    generateM365CertBtn.disabled = !appDetailsReady;
    m365CertificateFile.disabled = !appDetailsReady;
    m365PrivateKeyFile.disabled = !appDetailsReady;
    uploadM365CertBtn.disabled = !appDetailsReady;
    generateM365CertBtn.textContent = oauthSetup.certificate_ready ? "Replace pilot certificate" : "Generate pilot certificate";
    downloadM365CertLink.hidden = !details.certificate_thumbprint;
    if (details.configuration_unreadable) {
      m365SetupStatus.textContent = "Saved setup cannot be decrypted. The original host session secret is required to recover it.";
    } else if (details.certificate_thumbprint) {
      const expiration = details.certificate_expires ? new Date(details.certificate_expires).toLocaleDateString() : "unknown";
      m365SetupStatus.textContent = `Certificate ready (expires ${expiration}). Give the downloaded public certificate to IT to add to the Entra app.`;
    } else if (appDetailsReady) {
      m365SetupStatus.textContent = "App details saved. Generate a certificate next, then give its public copy to IT.";
    } else {
      m365SetupStatus.textContent = "Enter the tenant, application ID, and exact HTTPS callback supplied or approved by company IT.";
    }
    m365CertificateHelp.textContent = details.certificate_thumbprint
      ? `Public certificate thumbprint: ${details.certificate_thumbprint}. Ask IT to register this public certificate on the Entra app.`
      : "For production, upload an IT-approved certificate. The generated pilot certificate is self-signed and may not meet company policy.";
  }
  if (connection.connected) {
    m365ConnectionStatus.textContent = `Connected as ${connection.account}. Visitor alerts will be sent from this mailbox.`;
    connectM365Btn.textContent = "Reconnect Microsoft 365";
    disconnectM365Btn.hidden = false;
  } else if (connection.reconnect_required) {
    m365ConnectionStatus.textContent = "The saved Microsoft 365 connection needs to be reconnected.";
    connectM365Btn.textContent = "Reconnect Microsoft 365";
    disconnectM365Btn.hidden = false;
  } else if (connection.legacy_service_ready) {
    m365ConnectionStatus.textContent = "No dashboard-connected mailbox. The server's existing Microsoft 365 service configuration is ready and will send alerts.";
    connectM365Btn.textContent = "Connect Microsoft 365 account";
    disconnectM365Btn.hidden = true;
  } else {
    m365ConnectionStatus.textContent = "Microsoft 365 is not connected yet.";
    connectM365Btn.textContent = "Connect Microsoft 365 account";
    disconnectM365Btn.hidden = true;
  }
  const dashboardUsesHttps = window.location.protocol === "https:";
  connectM365Btn.disabled = Boolean(data.test_mode) || !connection.setup_ready || !dashboardUsesHttps;
  const missing = Object.entries(oauthSetup).filter(([, ready]) => !ready).map(([key]) => ({
    tenant_configured: "tenant ID", application_configured: "application ID", certificate_ready: "sign-in certificate",
    redirect_ready: "HTTPS callback address", token_encryption_ready: "host token-encryption key",
  }[key] || key.replaceAll("_", " ")));
  m365SetupHelp.hidden = false;
  m365SetupHelp.textContent = data.test_mode
    ? "Safe test mode cannot connect to Microsoft or send real email."
    : !dashboardUsesHttps
      ? `Open the dashboard using its trusted HTTPS address before connecting Microsoft 365${missing.length ? `. Still needed: ${missing.join(", ")}` : ""}. Company IT must also register the public certificate and exact callback, and approve delegated Mail.Send.`
      : missing.length
        ? `Still needed before Microsoft sign-in: ${missing.join(", ")}. Company IT must also register the public certificate and exact callback, and approve delegated Mail.Send.`
        : "Local setup is ready. Company IT must register the public certificate and exact callback, and approve delegated Mail.Send before the first sign-in.";
  const oldServiceReady = Boolean(connection.legacy_service_ready);
  testEmailForm.querySelector("button[type=submit]").disabled = !(connection.connected || oldServiceReady);
  emailStatus.textContent = connection.connected
    ? `Ready to send as ${connection.account}.`
    : oldServiceReady ? "Ready to send using the existing server email setup." : "Email sending needs Microsoft 365 account setup.";
}

async function saveMicrosoft365Setup(event) {
  event.preventDefault();
  m365SetupStatus.textContent = "Saving setup securely on this server...";
  const response = await fetch("/api/admin/notifications/setup", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      tenant_id: document.getElementById("m365TenantId").value,
      client_id: document.getElementById("m365ClientId").value,
      redirect_uri: document.getElementById("m365RedirectUri").value,
    }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    m365SetupStatus.textContent = data.error || "Could not save Microsoft 365 setup.";
    return;
  }
  await loadEmailReadiness();
}

async function generateMicrosoft365Certificate() {
  if (document.getElementById("m365TenantId").value !== (document.getElementById("m365TenantId").dataset.saved || "") ||
      document.getElementById("m365ClientId").value !== (document.getElementById("m365ClientId").dataset.saved || "") ||
      document.getElementById("m365RedirectUri").value !== (document.getElementById("m365RedirectUri").dataset.saved || "")) {
    return alert("Save the Microsoft app details before generating its certificate.");
  }
  if (!confirm("Generate or replace this app's sign-in certificate? Replacing it requires IT to register the new public certificate and the mailbox to reconnect.")) return;
  generateM365CertBtn.disabled = true;
  m365SetupStatus.textContent = "Generating a protected certificate on the server...";
  const response = await fetch("/api/admin/notifications/certificate/generate", {method: "POST"});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    m365SetupStatus.textContent = data.error || "Could not generate the certificate.";
    await loadEmailReadiness();
    return;
  }
  await loadEmailReadiness();
}

function fileAsBase64(file) {
  return file.arrayBuffer().then(buffer => {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    }
    return btoa(binary);
  });
}

async function uploadMicrosoft365Certificate() {
  const certificate = m365CertificateFile.files[0];
  const privateKey = m365PrivateKeyFile.files[0];
  if (!certificate || !privateKey) return alert("Choose both the public certificate and its matching private key.");
  if (certificate.size > 2_000_000 || privateKey.size > 2_000_000) return alert("Each certificate file must be no larger than 2 MB.");
  if (!confirm("Send these certificate files to this kiosk server over HTTPS? The private key will be encrypted at rest and never returned to the browser.")) return;
  uploadM365CertBtn.disabled = true;
  m365SetupStatus.textContent = "Encrypting and saving the certificate on the server...";
  const [certificateBase64, privateKeyBase64] = await Promise.all([fileAsBase64(certificate), fileAsBase64(privateKey)]);
  const response = await fetch("/api/admin/notifications/certificate/upload", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({certificate_base64: certificateBase64, private_key_base64: privateKeyBase64}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    m365SetupStatus.textContent = data.error || "Could not save the certificate.";
    await loadEmailReadiness();
    return;
  }
  m365CertificateFile.value = "";
  m365PrivateKeyFile.value = "";
  await loadEmailReadiness();
}

async function disconnectMicrosoft365() {
  if (!confirm("Disconnect the Microsoft 365 sender account? Visitor email alerts will stop unless the existing server email setup is configured.")) return;
  const response = await fetch("/api/admin/notifications/disconnect", {method:"POST"});
  if (!response.ok) return alert("Could not disconnect the Microsoft 365 account.");
  await loadEmailReadiness();
  alert("Microsoft 365 account disconnected.");
}

function renderTestEmailEmployees() {
  const activeEmployees = employeeCache.filter(employee => employee.active);
  testEmailEmployee.replaceChildren();
  for (const employee of activeEmployees) {
    const option = document.createElement("option");
    option.value = employee.id;
    option.textContent = employee.name;
    testEmailEmployee.appendChild(option);
  }
  testEmailEmployee.disabled = activeEmployees.length === 0;
  if (activeEmployees.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No active employees available";
    testEmailEmployee.appendChild(option);
  }
}

async function sendTestEmail(event) {
  event.preventDefault();
  const employeeId = testEmailEmployee.value;
  if (!employeeId) return alert("Choose an active employee first.");
  const response = await fetch("/api/admin/notifications/test", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({employee_id: employeeId}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) return alert(data.error || "Could not send the test email.");
  alert(`Test email ${data.status || "sent"}.`);
}

async function saveNotificationSettings(event) {
  event.preventDefault();
  const response = await fetch("/api/admin/settings", {method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({notifications_enabled:notificationsEnabled.checked,message_template:messageTemplate.value})});
  const data = await response.json();
  if (!response.ok) return alert(data.error || "Could not save notification settings.");
  alert("Notification settings saved.");
}

setInterval(() => {
  if (!dashboard.hidden) loadDashboard();
}, 15000);
setInterval(() => {
  if (!dashboard.hidden) loadEmployees();
}, 30000);

loadAuthState();
