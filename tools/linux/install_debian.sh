#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_SOURCE="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PACKAGE_ROOT="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"
[[ -f "$PACKAGE_ROOT/.env.example" && -f "$APP_SOURCE/RELEASE_MANIFEST.txt" ]] || fail "Use the clean company deployment package; do not run the installer from the development source tree."
INSTALL_ROOT="/opt/elite-rf-visitor-kiosk"
APP_ROOT="$INSTALL_ROOT/app"
VENV_ROOT="$INSTALL_ROOT/venv"
STATE_ROOT="/var/lib/elite-rf-visitor-kiosk"
ETC_ROOT="/etc/elite-rf-visitor-kiosk"
SERVICE_NAME="elite-rf-visitor-kiosk"
SERVICE_USER="eliterfkiosk"

[[ "$(id -u)" -eq 0 ]] || fail "Run this installer with sudo: sudo bash tools/linux/install_debian.sh"
[[ -f "$APP_SOURCE/run.py" && -f "$APP_SOURCE/requirements.txt" ]] || fail "Run this from the extracted company package; its app/ folder is missing."
[[ -r /etc/os-release ]] || fail "Cannot identify the Linux distribution."
. /etc/os-release
[[ "${ID:-}" == "debian" || " ${ID_LIKE:-} " == *" debian "* ]] || fail "This installer supports Debian and Debian-based distributions only."
command -v apt-get >/dev/null 2>&1 || fail "The Debian apt package manager is required."
command -v dpkg-query >/dev/null 2>&1 || fail "The Debian package query utility is required."
missing_packages=()
for package in python3 python3-venv apache2 openssl ca-certificates; do
  if ! dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null | grep -qx installed; then
    missing_packages+=("$package")
  fi
done
if ((${#missing_packages[@]})); then
  printf 'Installing approved Debian prerequisites from the configured apt sources: %s\n' "${missing_packages[*]}"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-venv apache2 openssl ca-certificates
fi
command -v python3 >/dev/null 2>&1 || fail "IT must install Python 3.11+ and python3-venv first."
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || fail "Python 3.11 or newer is required."
command -v useradd >/dev/null 2>&1 || fail "The useradd utility is required."
command -v groupadd >/dev/null 2>&1 || fail "The groupadd utility is required."
command -v runuser >/dev/null 2>&1 || fail "The runuser utility is required."
command -v systemctl >/dev/null 2>&1 || fail "systemd is required on the Debian VM."
command -v a2enmod >/dev/null 2>&1 || fail "Apache's a2enmod utility is required."
a2enmod proxy proxy_http headers ssl authz_host >/dev/null

if ! getent passwd "$SERVICE_USER" >/dev/null; then
  useradd --system --user-group --home-dir "$STATE_ROOT" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
if ! getent group "$SERVICE_USER" >/dev/null; then
  groupadd --system "$SERVICE_USER"
fi

install -d -o root -g root -m 0755 "$APP_ROOT" "$INSTALL_ROOT"
cp -a "$APP_SOURCE/." "$APP_ROOT/"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 \
  "$STATE_ROOT" "$STATE_ROOT/data" "$STATE_ROOT/storage" "$STATE_ROOT/storage/photos" "$STATE_ROOT/backups"
install -d -o root -g "$SERVICE_USER" -m 0750 "$ETC_ROOT"
chown root:"$SERVICE_USER" "$ETC_ROOT"

if [[ ! -f "$STATE_ROOT/config.json" ]]; then
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0640 "$APP_ROOT/config.json" "$STATE_ROOT/config.json"
fi
chown "$SERVICE_USER":"$SERVICE_USER" "$STATE_ROOT/config.json"
chmod 0640 "$STATE_ROOT/config.json"

python3 -m venv "$VENV_ROOT" || fail "Could not create the Python virtual environment. IT may need to install Debian's python3-venv package."
"$VENV_ROOT/bin/python" -m pip install --requirement "$APP_ROOT/requirements.txt"

if [[ ! -f "$ETC_ROOT/kiosk.env" ]]; then
  session_secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  umask 027
  {
    printf 'KIOSK_SESSION_SECRET=%s\n' "$session_secret"
    printf 'KIOSK_EXTERNAL_HTTPS=true\n'
    printf 'KIOSK_DISABLE_REMOTE_OWNER_SETUP=true\n'
    printf 'KIOSK_PUBLIC_HOSTNAME=SET-COMPANY-HOSTNAME\n'
    printf 'KIOSK_BACKUP_COPY_PATH=\n'
    printf 'KIOSK_REQUIRE_MOUNTED_BACKUP=true\n'
    printf 'KIOSK_BACKUP_TIMER_MANAGED=true\n'
    printf 'KIOSK_BACKUP_RETENTION=30\n'
    printf 'M365_TENANT_ID=\nM365_CLIENT_ID=\nM365_SENDER_ADDRESS=\n'
    printf 'M365_CERTIFICATE_PATH=\nM365_CERTIFICATE_THUMBPRINT=\nM365_PRIVATE_KEY_PATH=\n'
    printf 'M365_GRAPH_SCOPE=https://graph.microsoft.com/.default\n'
    printf 'M365_REDIRECT_URI=\n'
  } > "$ETC_ROOT/kiosk.env"
  chown root:"$SERVICE_USER" "$ETC_ROOT/kiosk.env"
  chmod 0640 "$ETC_ROOT/kiosk.env"
fi
python3 "$SCRIPT_DIR/configure_kiosk_env.py" "$ETC_ROOT/kiosk.env" --group "$SERVICE_USER"

backup_destination="$("$VENV_ROOT/bin/python" - "$ETC_ROOT/kiosk.env" <<'PY'
from pathlib import Path
import re
import sys

value = ""
for raw_line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    key, separator, item = raw_line.partition("=")
    if separator and key.strip() == "KIOSK_BACKUP_COPY_PATH":
        value = item.strip()
if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
    value = value[1:-1]
if value:
    path = Path(value)
    if (not path.is_absolute() or path == Path("/") or
            re.fullmatch(r"/[A-Za-z0-9_./+-]+", value) is None or
            any(part in {".", ".."} for part in path.parts)):
        raise SystemExit("KIOSK_BACKUP_COPY_PATH must be a simple absolute path without spaces or dot segments.")
    value = str(path)
print(value)
PY
)"

backup_dropin_name="10-elite-rf-backup-storage.conf"
for unit in "$SERVICE_NAME-backup.service"; do
  dropin_directory="/etc/systemd/system/$unit.d"
  dropin_path="$dropin_directory/$backup_dropin_name"
  if [[ -n "$backup_destination" ]]; then
    install -d -o root -g root -m 0755 "$dropin_directory"
    printf '[Unit]\nRequiresMountsFor=%s\n[Service]\nReadWritePaths=-%s\n' \
      "$backup_destination" "$backup_destination" > "$dropin_path"
    chown root:root "$dropin_path"
    chmod 0644 "$dropin_path"
  elif [[ -f "$dropin_path" ]]; then
    rm -f "$dropin_path"
    rmdir "$dropin_directory" 2>/dev/null || true
  fi
done

install -o root -g root -m 0644 "$SCRIPT_DIR/elite-rf-visitor-kiosk.service" \
  "/etc/systemd/system/$SERVICE_NAME.service"
install -o root -g root -m 0644 "$SCRIPT_DIR/elite-rf-visitor-kiosk-backup.service" \
  "/etc/systemd/system/$SERVICE_NAME-backup.service"
install -o root -g root -m 0644 "$SCRIPT_DIR/elite-rf-visitor-kiosk-backup.timer" \
  "/etc/systemd/system/$SERVICE_NAME-backup.timer"
install -d -o root -g root -m 0755 /usr/local/share/elite-rf-visitor-kiosk
install -o root -g root -m 0644 "$SCRIPT_DIR/elite-rf-visitor-kiosk.apache.conf" \
  /usr/local/share/elite-rf-visitor-kiosk/apache-vhost.example.conf

if PYTHONPATH="$APP_ROOT" "$VENV_ROOT/bin/python" -c 'import json,sys; from kiosk_server.security import verify_password; c=json.load(open(sys.argv[1],encoding="utf-8")); raise SystemExit(0 if verify_password("change-me-before-use",c.get("admin_password_hash","")) else 1)' "$STATE_ROOT/config.json"; then
  printf '\nSet the initial administrator password for the company kiosk.\n'
  runuser -u "$SERVICE_USER" -- "$VENV_ROOT/bin/python" \
    "$APP_ROOT/tools/set_admin_password.py" --config "$STATE_ROOT/config.json"
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0600 /dev/null "$STATE_ROOT/.admin-password-configured"
fi

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME.service"
systemctl restart "$SERVICE_NAME.service"

backup_mount_ready=false
if [[ -n "$backup_destination" ]] && \
   "$VENV_ROOT/bin/python" -c 'from pathlib import Path; import sys; raise SystemExit(0 if Path(sys.argv[1]).is_mount() else 1)' "$backup_destination" && \
   runuser -u "$SERVICE_USER" -- test -w "$backup_destination"; then
  backup_mount_ready=true
fi

if [[ "$backup_mount_ready" == true ]]; then
  systemctl disable --now "$SERVICE_NAME-backup.timer" >/dev/null 2>&1 || true
  systemctl start "$SERVICE_NAME-backup.service"
  systemctl enable --now "$SERVICE_NAME-backup.timer"
  printf 'Daily backup timer enabled for the configured mounted backup destination.\n'
else
  systemctl disable --now "$SERVICE_NAME-backup.timer" >/dev/null 2>&1 || true
  if [[ -z "$backup_destination" ]]; then
    systemctl start "$SERVICE_NAME-backup.service"
    printf 'Created an initial local backup; daily backups remain disabled until the secondary mount is configured.\n'
  else
    printf 'WARNING: Daily backups remain disabled. Mount KIOSK_BACKUP_COPY_PATH and grant %s write access, then rerun this installer.\n' "$SERVICE_USER"
  fi
fi

printf '\nDebian application service installed and started.\n'
printf 'Backend: http://127.0.0.1:8080 (local VM only)\n'
printf 'Runtime data: %s\n' "$STATE_ROOT"
printf 'Environment settings: %s/kiosk.env\n' "$ETC_ROOT"
printf 'Apache example: /usr/local/share/elite-rf-visitor-kiosk/apache-vhost.example.conf\n'
printf 'Next: follow the DEBIAN APACHE DEPLOYMENT section in INSTALL_INSTRUCTIONS.txt to configure Apache TLS and the reverse proxy.\n'
