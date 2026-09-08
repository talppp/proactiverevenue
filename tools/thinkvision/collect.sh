#!/usr/bin/env bash
# collect.sh — one-shot evidence bundle for the ThinkVision blackout issue.
# READ-ONLY. Changes nothing. Writes a single text file you can paste back.
#
#   bash collect.sh            # last 12h of history
#   bash collect.sh 2h         # narrower window (use right after a blackout)

set -uo pipefail
WIN="${1:-12h}"
OUT="${HOME}/thinkvision-logs"; mkdir -p "$OUT"
R="$OUT/report-$(date +%Y%m%d-%H%M%S).txt"

say(){ printf '\033[36m%s\033[0m\n' "$*"; }
sec(){ { echo; echo "################ $* ################"; } >> "$R"; }
run(){ { echo "\$ $*"; eval "$@" 2>&1 | head -"${CAP:-400}"; echo; } >> "$R"; }

[ "$(uname -s)" = "Darwin" ] || { echo "Run this on the Mac mini."; exit 1; }

say "Collecting into $R  (window: $WIN)"
say "Some kernel-level display events need sudo; you'll be asked once."
sudo -v 2>/dev/null || say "(no sudo — continuing without kernel-level entries)"

{ echo "ThinkVision blackout evidence bundle"; echo "generated: $(date)"; echo "window: $WIN"; } > "$R"

# ── 1. What hardware is actually involved ────────────────────────────────────
sec "IDENTITY"
run "sw_vers"
run "system_profiler SPHardwareDataType"

sec "DISPLAYS (model, mode, refresh, connection type)"
CAP=200 run "system_profiler SPDisplaysDataType"

sec "THUNDERBOLT / USB-C (any adapter or dock in the path?)"
CAP=120 run "system_profiler SPThunderboltDataType"

sec "KERNEL VIEW OF ATTACHED PANELS"
CAP=80 run "ioreg -r -c AppleCLCD2 -d 1 | grep -Ei 'AppleCLCD2|IOMFB|DisplayAttributes|ProductName|Timing' | head -60"
CAP=60 run "ioreg -r -c IODisplayConnect -d 1 | head -40"

# ── 2. Is macOS even configured to blank the screen? ─────────────────────────
sec "POWER SETTINGS"
run "pmset -g custom"
run "pmset -g"
CAP=40 run "pmset -g ps"

sec "SLEEP ASSERTIONS (what is holding the display awake right now)"
CAP=60 run "pmset -g assertions"

# ── 3. Did macOS *choose* to turn the display off? ───────────────────────────
sec "MACOS-INITIATED DISPLAY OFF/ON  <-- suspect 1"
CAP=150 run "pmset -g log | grep -iE 'Display is turned (off|on)' | tail -120"

sec "SLEEP / WAKE REASONS"
CAP=120 run "pmset -g log | grep -iE 'Wake from|Sleep +\\(|DarkWake|Wake Reason' | tail -80"

sec "PMSET LOG TAIL (raw, for timeline correlation)"
CAP=250 run "pmset -g log | tail -220"

# ── 4. Did the physical link drop? ───────────────────────────────────────────
PRED='eventMessage CONTAINS[c] "hpd" OR eventMessage CONTAINS[c] "hot plug" OR eventMessage CONTAINS[c] "hotplug" OR eventMessage CONTAINS[c] "link training" OR eventMessage CONTAINS[c] "linktraining" OR eventMessage CONTAINS[c] "link rate" OR eventMessage CONTAINS[c] "edid" OR eventMessage CONTAINS[c] "displayport" OR eventMessage CONTAINS[c] "dcpav" OR eventMessage CONTAINS[c] "displays changed" OR eventMessage CONTAINS[c] "no signal" OR subsystem == "com.apple.iokit.IOMobileGraphicsFamily" OR subsystem == "com.apple.DisplayServices"'

sec "DISPLAY PIPELINE EVENTS, last $WIN  <-- suspects 2 and 3"
say "Reading the unified log — this takes 30-90 seconds…"
CAP=700 run "sudo log show --last $WIN --style compact --info --predicate '$PRED' 2>/dev/null | tail -650"

sec "DCP FIRMWARE FAULTS (Apple Silicon)  <-- suspect 3"
CAP=120 run "sudo log show --last $WIN --style compact --info --debug --predicate 'eventMessage CONTAINS[c] \"dcp\"' 2>/dev/null | grep -iE 'crash|restart|timeout|panic|fail|recover' | tail -80"

sec "WINDOWSERVER DISPLAY ACTIVITY"
CAP=200 run "sudo log show --last $WIN --style compact --info --predicate 'process == \"WindowServer\"' 2>/dev/null | grep -iE 'display|sleep|wake|blank' | tail -150"

# ── 5. Crashes ───────────────────────────────────────────────────────────────
sec "RECENT CRASH REPORTS (WindowServer / graphics / kernel)"
CAP=60 run "ls -lt /Library/Logs/DiagnosticReports ~/Library/Logs/DiagnosticReports 2>/dev/null | grep -iE 'WindowServer|kernel|panic|dcp|graphics' | head -40"

# ── 6. Automated read ────────────────────────────────────────────────────────
n_sw=$(grep -ciE 'Display is turned off' "$R" 2>/dev/null || echo 0)
n_link=$(grep -ciE 'hpd|hotplug|hot plug|link training|edid|no signal' "$R" 2>/dev/null || echo 0)
n_dcp=$(grep -ciE 'dcpav|dcp.*(crash|restart|timeout|panic)' "$R" 2>/dev/null || echo 0)
model=$(awk -F': ' '/Model Identifier/{print $2; exit}' "$R")
mode=$(awk -F': ' '/UI Looks like/{print $2; exit}' "$R")

sec "AUTOMATED READ"
{
  echo "model                        : ${model:-?}"
  echo "current mode                 : ${mode:-?}"
  echo "macOS-initiated display-off  : $n_sw"
  echo "HDMI link / HPD / EDID       : $n_link"
  echo "DCP firmware faults          : $n_dcp"
} >> "$R"

echo
say "════════════════════════════════════════════"
say " Model        : ${model:-unknown}"
say " Mode         : ${mode:-unknown}"
say " display-off  : $n_sw   link: $n_link   dcp: $n_dcp"
say "════════════════════════════════════════════"
echo
say "Report: $R   ($(wc -l < "$R" | tr -d ' ') lines)"
echo
echo "Send it back with:      cat \"$R\" | pbcopy      (then paste into the chat)"
echo "Note: it contains your hostname and app names. Skim it if that matters."
