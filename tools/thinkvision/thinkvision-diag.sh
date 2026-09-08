#!/usr/bin/env bash
# thinkvision-diag.sh — find out WHY an external display attached to a Mac
# randomly goes black as if it entered power-save.
#
# This script is READ-ONLY. It changes no setting. It collects evidence and
# tells you which of four suspects is guilty:
#
#   (1) macOS software  — macOS deliberately turned the display off
#   (2) HDMI link       — the cable/port lost sync (Hot-Plug-Detect drop)
#   (3) DCP firmware    — Apple Silicon display co-processor crashed/restarted
#   (4) the monitor     — nothing in any log; the panel blanked on its own
#
# Usage:
#   ./thinkvision-diag.sh snapshot     # start here: identity + settings + last 6h
#   ./thinkvision-diag.sh record       # flight recorder; leave running, use the Mac
#   ./thinkvision-diag.sh capture      # run right AFTER a blackout (grabs last 15m)
#   ./thinkvision-diag.sh verdict      # re-read everything collected so far
#
# All output is also written to ~/thinkvision-logs/ so you can send it on.

set -uo pipefail

OUT="${HOME}/thinkvision-logs"
mkdir -p "$OUT"
STAMP="$(date +%Y%m%d-%H%M%S)"

# ---- tiny formatting helpers -------------------------------------------------
c()   { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
hdr() { echo; c "1;36" "══ $* ══"; }
ok()  { c "32" "  ✔ $*"; }
warn(){ c "33" "  ▲ $*"; }
bad() { c "31" "  ✖ $*"; }
note(){ echo "    $*"; }

need_macos() {
  [ "$(uname -s)" = "Darwin" ] || { bad "This script must run ON the Mac mini, not here."; exit 1; }
}

# Unified-log predicate for everything that touches the display pipeline.
# Kept in one place so snapshot / capture / record all look for the same things.
LOG_PRED='
  eventMessage CONTAINS[c] "hpd"
  OR eventMessage CONTAINS[c] "hot plug"
  OR eventMessage CONTAINS[c] "hotplug"
  OR eventMessage CONTAINS[c] "link training"
  OR eventMessage CONTAINS[c] "linktraining"
  OR eventMessage CONTAINS[c] "link rate"
  OR eventMessage CONTAINS[c] "edid"
  OR eventMessage CONTAINS[c] "displayport"
  OR eventMessage CONTAINS[c] "dp_tx"
  OR eventMessage CONTAINS[c] "dcpav"
  OR eventMessage CONTAINS[c] "display is turned"
  OR eventMessage CONTAINS[c] "displays changed"
  OR eventMessage CONTAINS[c] "no signal"
  OR subsystem == "com.apple.iokit.IOMobileGraphicsFamily"
  OR subsystem == "com.apple.DisplayServices"
  OR (process == "WindowServer" AND (eventMessage CONTAINS[c] "display" OR eventMessage CONTAINS[c] "sleep"))
'

# =============================================================================
# 1. IDENTITY — what hardware is actually in play
# =============================================================================
identity() {
  hdr "HARDWARE"
  local model chip osv
  model=$(system_profiler SPHardwareDataType 2>/dev/null | awk -F': ' '/Model Identifier/{print $2}')
  chip=$(system_profiler SPHardwareDataType 2>/dev/null | awk -F': ' '/Chip|Processor Name/{print $2; exit}')
  osv=$(sw_vers -productVersion 2>/dev/null)
  note "Model Identifier : ${model:-unknown}"
  note "Chip             : ${chip:-unknown}"
  note "macOS            : ${osv:-unknown}"

  case "$model" in
    Macmini9,1|Macmini10,1|Mac14,3|Mac14,12|Mac16,10|Mac16,11)
      warn "Apple Silicon Mac mini: the built-in HDMI port on these has a"
      note "documented history of random black-screen / dropout bugs."
      note "Suspect (3) DCP firmware and the HDMI port itself are live options." ;;
    Macmini8,1)
      note "Intel Mac mini (T2). HDMI dropouts here are usually cable/EDID, not firmware." ;;
    "") bad "Could not read model — is this really macOS?" ;;
    *)  note "Not a recognised Mac mini identifier; the rest still applies." ;;
  esac

  hdr "DISPLAY + LINK MODE"
  system_profiler SPDisplaysDataType 2>/dev/null | sed 's/^/    /'

  local mode
  mode=$(system_profiler SPDisplaysDataType 2>/dev/null | awk -F': ' '/UI Looks like/{print $2; exit}')
  if echo "$mode" | grep -qE '38[0-9][0-9] x 2160.*(59|60)'; then
    warn "Running 4K @ 60Hz. That is ~18 Gbps — the exact point where a"
    note "marginal HDMI cable drops sync intermittently. Prime suspect (2)."
  fi
}

# =============================================================================
# 2. SETTINGS — is macOS even configured to blank the screen?
# =============================================================================
settings() {
  hdr "POWER SETTINGS (pmset)"
  pmset -g custom 2>/dev/null | sed 's/^/    /'

  local ds
  ds=$(pmset -g custom 2>/dev/null | awk '/displaysleep/{print $2; exit}')
  echo
  if [ -n "${ds:-}" ] && [ "$ds" -gt 0 ] 2>/dev/null; then
    ok "Display sleep timer = ${ds} min."
    note "IMPORTANT: this timer is reset by every keystroke and mouse move."
    note "It therefore CANNOT be what blanks the screen while you are typing."
  elif [ "${ds:-}" = "0" ]; then
    note "Display sleep disabled entirely — definitely not the cause."
  fi

  hdr "SLEEP ASSERTIONS (what is currently holding the display awake)"
  pmset -g assertions 2>/dev/null | sed -n '1,25p' | sed 's/^/    /'
  note ""
  note "If PreventUserIdleDisplaySleep = 0 while you are WATCHING video,"
  note "the idle timer keeps counting and macOS will legitimately blank the"
  note "screen. That explains blackouts while watching — never while typing."
}

# =============================================================================
# 3. HISTORY — what the logs already remember
# =============================================================================
history_window() {
  local since="${1:-6h}"
  local pm="$OUT/pmset-log-$STAMP.txt"
  local ul="$OUT/unified-log-$STAMP.txt"

  hdr "MACOS-INITIATED DISPLAY OFF/ON EVENTS (last $since)"
  pmset -g log 2>/dev/null > "$pm"
  local sw
  sw=$(grep -iE "Display is turned (off|on)|DisplaySleep|Assertions.*Display" "$pm" | tail -40)
  if [ -n "$sw" ]; then
    echo "$sw" | sed 's/^/    /'
  else
    note "(none found)"
  fi

  hdr "DISPLAY PIPELINE EVENTS (last $since)"
  note "Collecting… this takes 10-60s."
  log show --last "$since" --style compact --info --predicate "$LOG_PRED" 2>/dev/null > "$ul"
  local n; n=$(wc -l < "$ul" | tr -d ' ')
  note "Wrote $n lines to $ul"
  grep -iE "hpd|hotplug|hot plug|link training|edid|dcpav|displays changed|no signal" "$ul" | tail -40 | sed 's/^/    /'

  echo "$pm" > "$OUT/.last-pmset"
  echo "$ul" > "$OUT/.last-unified"
}

# =============================================================================
# 4. VERDICT — turn the evidence into an accusation
# =============================================================================
verdict() {
  local pm ul
  pm=$(cat "$OUT/.last-pmset" 2>/dev/null); ul=$(cat "$OUT/.last-unified" 2>/dev/null)
  [ -r "${pm:-}" ] || { bad "No collected data. Run 'snapshot' first."; return 1; }

  local n_sw n_link n_dcp
  n_sw=$(grep -ciE "Display is turned off" "$pm" 2>/dev/null || echo 0)
  n_link=$(grep -ciE "hpd|hotplug|hot plug|link training|edid|no signal" "${ul:-/dev/null}" 2>/dev/null || echo 0)
  n_dcp=$(grep -ciE "dcpav|dcp.*(crash|restart|timeout|panic)" "${ul:-/dev/null}" 2>/dev/null || echo 0)

  hdr "VERDICT"
  note "macOS-initiated display-off events : $n_sw"
  note "HDMI link / HPD / EDID events      : $n_link"
  note "DCP firmware events                : $n_dcp"
  echo

  if [ "$n_dcp" -gt 0 ]; then
    bad "SUSPECT (3): Apple Silicon DCP firmware faults are present."
    note "→ Fix path: update macOS fully, then bypass the built-in HDMI port"
    note "  with a USB-C → DisplayPort cable. That routes around the DCP path"
    note "  that is faulting."
  fi
  if [ "$n_link" -gt 0 ] && [ "$n_sw" -eq 0 ]; then
    bad "SUSPECT (2): the HDMI link is dropping. macOS never asked for this —"
    note "the signal simply stopped and the monitor fell back to power-save."
    note "→ Fix path, in order: (a) certified Ultra-High-Speed HDMI cable,"
    note "  (b) drop refresh to 4K@30 or disable HDR to halve link bandwidth,"
    note "  (c) USB-C → DisplayPort to bypass the HDMI port entirely."
  fi
  if [ "$n_sw" -gt 0 ] && [ "$n_link" -eq 0 ]; then
    warn "SUSPECT (1): macOS is deliberately turning the display off."
    note "→ Check 'pmset -g assertions' during the app you were using, and"
    note "  raise displaysleep. Energy saving still works — see thinkvision-fix.sh."
  fi
  if [ "$n_sw" -eq 0 ] && [ "$n_link" -eq 0 ] && [ "$n_dcp" -eq 0 ]; then
    warn "SUSPECT (4): no software and no link event recorded."
    note "If a blackout happened inside this window, macOS kept streaming a"
    note "valid picture and the MONITOR blanked itself. That is the ThinkVision"
    note "OSD: turn off its auto-standby / 'Deep Sleep' / DDC-CI setting."
    note "If no blackout happened in the window, just run 'record' and wait."
  fi
}

# =============================================================================
# 5. FLIGHT RECORDER — the blackout is intermittent, so sit and wait for it
# =============================================================================
record() {
  local stream="$OUT/recorder-$STAMP.log"
  local marks="$OUT/marks-$STAMP.log"
  hdr "FLIGHT RECORDER"
  note "Streaming display events → $stream"
  note "Polling display presence  → $marks"
  note ""
  ok  "Leave this running and use the Mac normally."
  ok  "When the screen blacks out, do NOT unplug yet — wait 10 seconds,"
  ok  "then reconnect. Come back here and press Ctrl-C, then run:"
  ok  "    ./thinkvision-diag.sh verdict"
  echo

  log stream --style compact --level info --predicate "$LOG_PRED" > "$stream" 2>/dev/null &
  local pid=$!
  trap 'kill '"$pid"' 2>/dev/null; echo "$stream" > "'"$OUT"'/.last-unified"; echo; ok "Recorder stopped."; exit 0' INT TERM

  local prev="" now
  while :; do
    now=$(display_count)
    if [ "$now" != "$prev" ]; then
      echo "$(date '+%F %T')  DISPLAY-COUNT ${prev:-?} -> ${now}" | tee -a "$marks"
      prev="$now"
    fi
    sleep 3
  done
}

# Cheap probe for "how many displays does the kernel currently see".
display_count() {
  local n
  n=$(ioreg -r -c AppleCLCD2 -d 1 2>/dev/null | grep -c '^+-o')
  [ "${n:-0}" -gt 0 ] 2>/dev/null && { echo "$n"; return; }
  n=$(ioreg -r -c IODisplayConnect -d 1 2>/dev/null | grep -c '^+-o')
  echo "${n:-0}"
}

# =============================================================================
main() {
  need_macos
  case "${1:-snapshot}" in
    snapshot) identity; settings; history_window "6h"; verdict ;;
    capture)  history_window "15m"; verdict ;;
    record)   record ;;
    verdict)  verdict ;;
    *) echo "usage: $0 {snapshot|record|capture|verdict}"; exit 2 ;;
  esac
  echo; ok "Evidence saved under $OUT"
}
main "$@"
