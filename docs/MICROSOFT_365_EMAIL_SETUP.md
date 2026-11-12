# Microsoft 365 Email Setup

The dashboard connects one approved Microsoft 365 mailbox as the kiosk's sender. A visitor check-in sends an alert from that mailbox to the selected employee's saved company email. The dashboard also manages the message template and can send a clearly labeled test email.

## One-time approval by company IT

The app cannot create an application registration or grant permissions inside Elite RF's Microsoft tenant. IT must approve these one-time items:

1. Create or select a single-tenant Microsoft Entra application for the kiosk and provide its **Directory (tenant) ID** and **Application (client) ID** to the kiosk administrator.
2. Add Microsoft Graph's delegated **Mail.Send** permission. Have an Entra administrator grant consent if company policy requires it.
3. Provide the exact HTTPS dashboard callback address. It ends in `/oauth/microsoft/callback` and must be registered as a **Web** redirect URI on the Entra app.
4. Add the public certificate to the Entra app's **Certificates & secrets → Certificates** page. Prefer a certificate approved by company IT for production: upload its public certificate and matching unencrypted PEM private key in the dashboard. The dashboard can generate a self-signed pilot certificate if company policy permits, but company IT must decide whether it is acceptable for production. Never upload the kiosk's HTTPS/TLS server certificate as the Microsoft app certificate.
5. Restrict access to the Entra app and sender mailbox according to company policy. This uses delegated permission: mail is sent as the mailbox that signs in, using Microsoft Graph `/me/sendMail`.

No public DNS is required if the company hostname resolves internally, but the sign-in browser must reach the dashboard at that hostname and trust its HTTPS certificate. The redirect address entered in the dashboard and the one registered in Entra must match exactly.

## Connect from the dashboard

1. Run the kiosk behind its trusted HTTPS address and sign in to the admin dashboard.
2. Open **Notifications → Prepare Microsoft 365**.
3. Enter the tenant ID, application ID, and exact HTTPS callback supplied or approved by IT, then select **Save app details**.
4. Upload an IT-approved public certificate and its matching unencrypted PEM private key, or select **Generate pilot certificate** if IT permits it. For a generated certificate, download the public `.cer` file and give it to IT to add to Entra. The matching private key stays encrypted at rest on the host.
5. Once IT has added the public certificate, registered the callback, and approved `Mail.Send`, select **Connect Microsoft 365 account**. Microsoft opens its sign-in page. Sign in to the dedicated sender mailbox and accept any consent prompt allowed by company policy. Microsoft returns to the dashboard, which displays the connected mailbox.
6. Add or confirm employee email addresses in **Employees**, choose an active employee in **Send a test email**, and verify delivery to an approved mailbox.
7. Turn on **Send visitor alerts** and save. Check-ins will then email the selected employee.

If the connection button is unavailable, the dashboard lists missing host-side values. If Microsoft rejects the sign-in, confirm the tenant, app ID, exact callback, uploaded public certificate, delegated permission, and tenant consent with IT.

## Protected host data

The dashboard's Microsoft app settings and private key are encrypted in `data/.microsoft365-dashboard-settings`, using the host session secret. The generated public certificate can be downloaded only after admin sign-in. Microsoft sign-in tokens are separately encrypted in `data/.microsoft365-token-cache`. Neither file is included in visitor backups. Keep `KIOSK_SESSION_SECRET` stable; changing or losing it requires configuring or reconnecting the mailbox again.

The authorization response uses a short-lived, single-use state entry and a secure browser cookie. The callback is HTTPS-only and uses `form_post` so the authorization code does not appear in the URL or Apache access log. Microsoft Graph's `202 Accepted` response means it accepted the request for processing; it does not guarantee final delivery.

Safe test mode intentionally disables Microsoft sign-in, certificate setup, and real email. Automated local tests mock Microsoft and never contact the company tenant. Before a pilot, complete a real sign-in and test only with a company-approved mailbox and recipient.
