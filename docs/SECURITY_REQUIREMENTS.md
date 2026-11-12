# Security Requirements

This kiosk is for real company use, so security matters.

## Secrets

- Never hardcode Microsoft 365 application credentials or private keys.
- Never hardcode session secrets.
- Store secrets in environment variables or a `.env` file that is not committed.
- Do not print secrets to logs.

## Admin dashboard

- Admin routes must be protected.
- Admin login/session behavior must not be weakened.
- Session cookies should be secure for the deployment environment.
- Avoid overly long sessions unless intentionally configured.

## Visitor data

- Do not log visitor personal information unnecessarily.
- Do not expose visitor logs publicly.
- Treat phone numbers and emails as private.
- Only collect fields that are actually needed.

## Microsoft 365 email

- Validate recipient email addresses.
- Handle Microsoft Graph errors without crashing the kiosk.
- Do not expose Microsoft 365 credentials or tokens to frontend code.
- Encrypt the persisted delegated-token cache with a stable, high-entropy host secret and restrict the cache file to the kiosk service account.
- Bind the HTTPS authorization callback to a single-use state and browser cookie; never put the authorization code in the callback URL or access logs.
- Avoid sending duplicate email messages if the visitor resubmits.

## Database

- Validate user input before saving.
- Use parameterized queries or safe ORM methods.
- Keep SQLite foreign-key enforcement, WAL mode, a write timeout, and full synchronous durability enabled for application connections.
- Serialize visitor/photo writes, deletions, and backup snapshots so a recovery archive cannot pair a database row with a missing photo.
- Permit visit deletion only through the authenticated admin operation; keep a non-personal audit event and retry photo cleanup after interruption.
- Treat backup archives as sensitive: they contain visitor records and photos and are not application-encrypted. Require encrypted, access-controlled host and backup storage.
- Test every schema migration against a disposable copy and document a recovery checkpoint before release.

## Deployment

- Do not run debug mode in production.
- Do not expose local development servers to the public internet without review.
- Document how to start, stop, and update the app.
