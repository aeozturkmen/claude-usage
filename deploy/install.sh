#!/bin/bash
# Claude Usage Dashboard: install for THIS macOS profile.
#
# Every profile on this Mac shares:
#   /Users/Shared/ClaudeUsage/app       the code (one version for everyone)
#   /Users/Shared/ClaudeUsage/usage.db  one database with every profile's usage
#   http://localhost:9050               one dashboard address
#
# Run once in each profile, without sudo:   bash deploy/install.sh
# Safe to run again (e.g. after updating the code). Details: KURULUM.md

set -euo pipefail

SHARED="${CLAUDE_USAGE_SHARED:-/Users/Shared/ClaudeUsage}"
PORT="${CLAUDE_USAGE_PORT:-9050}"
LABEL="${CLAUDE_USAGE_LABEL:-com.claudeusage.dashboard}"
APP="$SHARED/app"
DB="$SHARED/usage.db"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/claudeusage-dashboard"
BACKUP_DIR="$HOME/.claude/claude-usage-backup"
LOCAL_DB="$HOME/.claude/usage.db"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
UIDN="$(id -u)"
CODE=(cli.py dashboard.py scanner.py pricing.py)

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\033[31mHATA: %s\033[0m\n' "$*" >&2; exit 1; }
own_chmod() { for p in "${@:2}"; do if [ -O "$p" ]; then chmod "$1" "$p"; fi; done; }

[ "$(uname)" = Darwin ] || die "Bu kurulum macOS içindir."
[ "$UIDN" != 0 ] || die "sudo ile değil, normal kullanıcı olarak çalıştırın."
for f in "${CODE[@]}"; do
  [ -f "$SRC/$f" ] || die "$SRC/$f yok. Script'i paket klasörünün içinden çalıştırın: bash deploy/install.sh"
done

# ── 1. Python ────────────────────────────────────────────────────────────────
PY=""
for cand in /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if [ -x "$cand" ] && "$cand" -c 'import sys, sqlite3; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
    PY="$cand"; break
  fi
done
[ -n "$PY" ] || die "Python 3.8+ bulunamadı. Terminalde 'xcode-select --install' çalıştırıp tekrar deneyin."
say "Profil: $(id -un)   Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"

# ── 2. Shared folder (group staff, writable by every profile) ───────────────
umask 002
mkdir -p "$SHARED" "$APP/deploy" "$SHARED/.usage-sync"
for d in "$SHARED" "$APP" "$APP/deploy" "$SHARED/.usage-sync"; do
  if [ -O "$d" ]; then chgrp staff "$d" && chmod 775 "$d"; fi
  [ -w "$d" ] || die "$d klasörüne yazılamıyor. Klasörü oluşturan profilde çalıştırın: chmod -R g+w \"$SHARED\""
done
say "Ortak klasör: $SHARED"

# ── 3. Code ─────────────────────────────────────────────────────────────────
if [ "$SRC" != "$APP" ]; then
  say "Kod kopyalanıyor: $APP"
  copy() {  # atomic per file, so a running agent never reads half a file
    local tmp
    tmp="$(dirname "$APP/$2")/.$(basename "$2").$$.tmp"
    cp "$1" "$tmp" && chmod "$3" "$tmp" && mv -f "$tmp" "$APP/$2"
  }
  for f in "${CODE[@]}" LICENSE README.md; do
    if [ -f "$SRC/$f" ]; then copy "$SRC/$f" "$f" 664; fi
  done
  for md in KURULUM.md docs/KURULUM.md; do
    if [ -f "$SRC/$md" ]; then copy "$SRC/$md" KURULUM.md 664; break; fi
  done
  for s in install.sh uninstall.sh status.sh; do
    copy "$SRC/deploy/$s" "deploy/$s" 775
  done
  note "Diğer profilin çalışan ajanı yeni kodu en geç 60 sn içinde kendiliğinden yükler."
else
  say "Kod zaten ortak klasörde: $APP"
fi

# ── 4. Stop this profile's agent (old or new) so the DB is quiet ─────────────
if launchctl print "gui/$UIDN/$LABEL" >/dev/null 2>&1; then
  say "Bu profilin mevcut dashboard ajanı durduruluyor"
  launchctl bootout "gui/$UIDN/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
  sleep 2
fi

# ── 5. Database ──────────────────────────────────────────────────────────────
(umask 022; mkdir -p "$BACKUP_DIR")
if [ -f "$LOCAL_DB" ] && [ ! -f "$DB" ]; then
  say "Bu profilin mevcut geçmişi ortak veritabanına taşınıyor"
  "$PY" - "$LOCAL_DB" "$DB" <<'PYEOF'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
PYEOF
  mv "$LOCAL_DB" "$BACKUP_DIR/usage.db.before-shared-$STAMP"
  note "Eski yerel DB yedeği: $BACKUP_DIR/usage.db.before-shared-$STAMP"
elif [ -f "$LOCAL_DB" ]; then
  say "Bu profilin eski yerel geçmişi ortak veritabanına ekleniyor"
  added="$(cd "$APP" && CLAUDE_USAGE_DB="$DB" PYTHONDONTWRITEBYTECODE=1 "$PY" -c \
    'import sys, scanner; print(scanner.merge_db(sys.argv[1], scanner.DB_PATH))' "$LOCAL_DB")"
  mv "$LOCAL_DB" "$BACKUP_DIR/usage.db.before-shared-$STAMP"
  note "$added kayıt eklendi. Eski yerel DB yedeği: $BACKUP_DIR/usage.db.before-shared-$STAMP"
else
  say "Veritabanı: $DB"
fi
own_chmod 664 "$DB"
umask 022  # the rest is private to this profile

# Plain terminal commands (python3 cli.py today) read the shared DB too.
printf '%s\n' "$DB" > "$HOME/.claude/usage-db-path"

# ── 6. LaunchAgent ───────────────────────────────────────────────────────────
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
if [ -f "$PLIST" ]; then
  cp "$PLIST" "$BACKUP_DIR/$LABEL.plist.$STAMP"
  note "Eski ajan ayarı yedeği: $BACKUP_DIR/$LABEL.plist.$STAMP"
fi
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>$APP/cli.py</string>
        <string>dashboard</string>
        <string>--port</string>
        <string>$PORT</string>
        <string>--agent</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$APP</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>Umask</key>
    <integer>2</integer>
    <key>StandardOutPath</key>
    <string>$LOG.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>$HOME</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>CLAUDE_USAGE_DB</key>
        <string>$DB</string>
        <key>PYTHONDONTWRITEBYTECODE</key>
        <string>1</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null || die "Oluşan plist geçersiz: $PLIST"
say "Ajan başlatılıyor ($LABEL)"
launchctl bootstrap "gui/$UIDN" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"

# ── 7. Check ─────────────────────────────────────────────────────────────────
say "Kontrol ediliyor (ilk tarama birkaç saniye sürebilir)"
for _ in $(seq 1 90); do  # until this profile serves the port or stands by
  pid="$(launchctl print "gui/$UIDN/$LABEL" 2>/dev/null | awk '$1 == "pid" {print $3; exit}')"
  [ -f "$SHARED/.usage-sync/standby-$UIDN.pid" ] && break
  [ -n "$pid" ] && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | grep -qx "$pid" && break
  sleep 1
done
echo
export CLAUDE_USAGE_SHARED="$SHARED" CLAUDE_USAGE_PORT="$PORT" CLAUDE_USAGE_LABEL="$LABEL"
bash "$APP/deploy/status.sh"
