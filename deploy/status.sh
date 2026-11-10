#!/bin/bash
# Claude Usage Dashboard: is this profile connected? Run in any profile:
#   bash /Users/Shared/ClaudeUsage/app/deploy/status.sh

SHARED="${CLAUDE_USAGE_SHARED:-/Users/Shared/ClaudeUsage}"
PORT="${CLAUDE_USAGE_PORT:-9050}"
LABEL="${CLAUDE_USAGE_LABEL:-com.claudeusage.dashboard}"
DB="$SHARED/usage.db"
SYNC="$SHARED/.usage-sync"
LOG="$HOME/Library/Logs/claudeusage-dashboard.log"
UIDN="$(id -u)"

ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✘\033[0m %s\n' "$*"; }
info() { printf '  • %s\n' "$*"; }

echo "Claude Usage Dashboard: $(id -un) profili"

pid="$(launchctl print "gui/$UIDN/$LABEL" 2>/dev/null | awk '$1 == "pid" {print $3; exit}')"
if [ -n "$pid" ]; then ok "Ajan çalışıyor (pid $pid)"; else bad "Ajan çalışmıyor. Kurmak için: bash $SHARED/app/deploy/install.sh"; fi

if curl -fsS -o /dev/null "http://localhost:$PORT/" 2>/dev/null; then
  ok "Dashboard açık: http://localhost:$PORT"
  listener="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
  if [ -n "$pid" ] && [ "$listener" = "$pid" ]; then
    info "Rol: SUNUCU (sayfayı bu profil sunuyor)"
  elif [ -f "$SYNC/standby-$UIDN.pid" ]; then
    info "Rol: YEDEK (sayfayı diğer profil sunuyor; bu profil kendi verisini ortak DB'ye yazıyor)"
  else
    info "Rol: sayfayı başka bir süreç sunuyor"
  fi
else
  bad "http://localhost:$PORT yanıt vermiyor (ilk tarama sürüyor olabilir, 30 sn sonra tekrar deneyin)"
fi

if [ -f "$DB" ]; then
  ok "Ortak DB: $DB ($(du -h "$DB" | cut -f1 | tr -d ' '))"
  # Which profiles feed the DB: transcript paths look like /Users/<name>/...
  sqlite3 -readonly "$DB" "
    SELECT substr(path, 8, instr(substr(path, 8), '/') - 1) AS profile,
           COUNT(*), datetime(MAX(mtime), 'unixepoch', 'localtime')
    FROM processed_files WHERE path LIKE '/Users/%'
    GROUP BY profile ORDER BY profile;" 2>/dev/null |
  while IFS='|' read -r who files last; do
    info "Veri gelen profil: $who ($files dosya, son değişiklik $last)"
  done
else
  bad "Ortak DB yok: $DB"
fi

if [ "$(cat "$HOME/.claude/usage-db-path" 2>/dev/null)" = "$DB" ]; then
  ok "Terminal komutları (cli.py today/week) ortak DB'yi kullanıyor"
else
  bad "~/.claude/usage-db-path ortak DB'yi göstermiyor (install.sh'i tekrar çalıştırın)"
fi

if [ -f "$LOG" ]; then
  echo "  Son log satırları ($LOG):"
  tail -n 4 "$LOG" | sed 's/^/    /'
fi
