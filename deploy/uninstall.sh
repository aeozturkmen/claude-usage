#!/bin/bash
# Claude Usage Dashboard: remove it from THIS macOS profile.
# The shared folder (code + database) stays, because other profiles may still
# use it. Run in each profile; see KURULUM.md for a full removal or rollback.

set -uo pipefail

SHARED="${CLAUDE_USAGE_SHARED:-/Users/Shared/ClaudeUsage}"
LABEL="${CLAUDE_USAGE_LABEL:-com.claudeusage.dashboard}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UIDN="$(id -u)"

echo "==> Ajan durduruluyor ($LABEL)"
launchctl bootout "gui/$UIDN/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST" "$HOME/.claude/usage-db-path" "$SHARED/.usage-sync/standby-$UIDN.pid"

echo "==> Bu profilden kaldırıldı."
echo "    Ortak klasör duruyor: $SHARED (diğer profiller kullanıyor olabilir)."
echo "    Tamamen silmek için, tüm profillerde bu script'i çalıştırdıktan sonra:"
echo "      rm -rf \"$SHARED\""
echo "    Eski yedekler: $HOME/.claude/claude-usage-backup/"
