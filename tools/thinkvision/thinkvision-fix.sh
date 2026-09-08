#!/usr/bin/env bash
# thinkvision-fix.sh — apply the macOS-side mitigations for random display
# blackouts, WITHOUT giving up energy saving.
#
#   ./thinkvision-fix.sh show      # what is set now (default, changes nothing)
#   ./thinkvision-fix.sh apply     # sane display-sleep + video-playback fix
#   ./thinkvision-fix.sh revert    # restore whatever was saved by 'apply'
#   ./thinkvision-fix.sh autorecover-install   # opt-in: auto re-sync on dropout
#   ./thinkvision-fix.sh autorecover-remove
#
# Design note: the display-sleep timer is reset by every keystroke, so raising
# it is NOT what stops blackouts-while-typing. It only removes the one
# legitimate case (blanking during video). Everything else is physical and is
# handled by the elimination protocol in README.md.

set -uo pipefail
BACKUP="${HOME}/thinkvision-logs/pmset-backup.txt"
AGENT="${HOME}/Library/LaunchAgents/com.local.thinkvision.autorecover.plist"
HELPER="${HOME}/.local/bin/thinkvision-autorecover.sh"

c(){ printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
hdr(){ echo; c "1;36" "══ $* ══"; }
ok(){ c "32" "  ✔ $*"; }
warn(){ c "33" "  ▲ $*"; }
note(){ echo "    $*"; }
[ "$(uname -s)" = "Darwin" ] || { c 31 "Run this ON the Mac mini."; exit 1; }

show() {
  hdr "CURRENT POWER SETTINGS"
  pmset -g custom | sed 's/^/    /'
  hdr "CURRENT SLEEP ASSERTIONS"
  pmset -g assertions | sed -n '1,20p' | sed 's/^/    /'
}

apply() {
  mkdir -p "$(dirname "$BACKUP")"
  pmset -g custom > "$BACKUP"
  ok "Saved current settings to $BACKUP"

  hdr "APPLYING (needs sudo)"
  note "displaysleep 15   → screen still sleeps after 15 idle min (energy saving KEPT)"
  note "sleep 0           → the Mac itself stays up; only the SCREEN sleeps."
  note "                    This removes system-sleep/wake HDMI renegotiation,"
  note "                    which is a separate source of failed re-sync."
  echo
  read -r -p "    Proceed? [y/N] " a; [ "$a" = "y" ] || { warn "Aborted."; return 1; }

  sudo pmset -a displaysleep 15
  sudo pmset -a sleep 0
  ok "Applied. 'thinkvision-fix.sh revert' undoes this."

  hdr "STILL TO DO BY HAND (not scriptable)"
  note "1. ThinkVision OSD → Settings/System → turn OFF 'Auto Standby' /"
  note "   'Deep Sleep' / 'Energy Saving'. The monitor blanks itself on a"
  note "   brief signal dip; this stops it doing so."
  note "2. ThinkVision OSD → turn OFF DDC/CI if a toggle exists."
  note "3. If you watch video in a browser window: keep it fullscreen, or the"
  note "   idle timer keeps counting and macOS blanks the screen legitimately."
}

revert() {
  [ -r "$BACKUP" ] || { warn "No backup at $BACKUP"; return 1; }
  hdr "REVERTING"
  local ds sl
  ds=$(awk '/displaysleep/{print $2; exit}' "$BACKUP")
  sl=$(awk '/[^y] sleep/{print $2; exit}' "$BACKUP")
  [ -n "${ds:-}" ] && sudo pmset -a displaysleep "$ds" && ok "displaysleep → $ds"
  [ -n "${sl:-}" ] && sudo pmset -a sleep "$sl" && ok "sleep → $sl"
}

# --- opt-in band-aid: recover the picture without reaching behind the Mac ----
autorecover_install() {
  hdr "AUTO-RECOVER (opt-in band-aid, NOT a fix)"
  note "Watches how many displays the kernel sees. If the ThinkVision"
  note "disappears for ~9s, it forces the framebuffer to re-initialise"
  note "(displaysleepnow + wake) — the software equivalent of replugging."
  note ""
  warn "This treats the symptom so you can keep working while you run the"
  warn "elimination protocol. It does not repair the underlying link."
  echo
  read -r -p "    Install? [y/N] " a; [ "$a" = "y" ] || return 1

  mkdir -p "$(dirname "$HELPER")"
  cat > "$HELPER" <<'HELP'
#!/usr/bin/env bash
# Re-initialise the display pipeline when the external panel vanishes.
count() {
  local n
  n=$(ioreg -r -c AppleCLCD2 -d 1 2>/dev/null | grep -c '^+-o')
  [ "${n:-0}" -gt 0 ] 2>/dev/null && { echo "$n"; return; }
  ioreg -r -c IODisplayConnect -d 1 2>/dev/null | grep -c '^+-o'
}
BASE=$(count); MISS=0
while :; do
  N=$(count)
  if [ "${N:-0}" -lt "${BASE:-1}" ]; then
    MISS=$((MISS+1))
    if [ "$MISS" -ge 3 ]; then
      logger -t thinkvision-autorecover "display lost (saw $N, expected $BASE) — forcing re-sync"
      pmset displaysleepnow; sleep 2; caffeinate -u -t 2
      MISS=0
    fi
  else
    MISS=0; BASE=$N
  fi
  sleep 3
done
HELP
  chmod +x "$HELPER"

  mkdir -p "$(dirname "$AGENT")"
  cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.local.thinkvision.autorecover</string>
  <key>ProgramArguments</key><array><string>${HELPER}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>${HOME}/thinkvision-logs/autorecover.err</string>
</dict></plist>
PLIST
  launchctl unload "$AGENT" 2>/dev/null
  launchctl load "$AGENT" && ok "Installed and running."
  note "Its actions appear in Console.app under 'thinkvision-autorecover'."
}

autorecover_remove() {
  launchctl unload "$AGENT" 2>/dev/null
  rm -f "$AGENT" "$HELPER"
  ok "Auto-recover removed."
}

case "${1:-show}" in
  show) show ;;
  apply) apply ;;
  revert) revert ;;
  autorecover-install) autorecover_install ;;
  autorecover-remove) autorecover_remove ;;
  *) echo "usage: $0 {show|apply|revert|autorecover-install|autorecover-remove}"; exit 2 ;;
esac
