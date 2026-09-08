#!/usr/bin/env bash
# correlate.sh — run within a minute or two of an actual blackout.
# Checks whether macOS logged an intentional display-off at that moment,
# and pulls the hotplug/EDID sequence around it.
#
#   ./correlate.sh 14:32          # the wall-clock time (HH:MM) you saw it happen

set -uo pipefail
[ "$(uname -s)" = "Darwin" ] || { echo "Run this on the Mac mini."; exit 1; }
T="${1:?usage: $0 HH:MM  (the time you saw the screen go black)}"
OUT="$HOME/thinkvision-logs/correlate-$(date +%H%M%S).txt"
mkdir -p "$(dirname "$OUT")"

{
echo "=== Did macOS itself turn the display off around $T? ==="
pmset -g log | grep -iE "Display is turned" | grep -F "$T"
echo "(blank above = macOS has NO record of turning the display off at $T)"
echo
echo "=== Hotplug / EDID / firmware events near $T (scanning the last 45 min) ==="
sudo log show --last 45m --style compact --info \
  --predicate 'eventMessage CONTAINS[c] "hot plug" OR eventMessage CONTAINS[c] "hotplug" OR eventMessage CONTAINS[c] "edid" OR eventMessage CONTAINS[c] "dcpav" OR eventMessage CONTAINS[c] "PowerStateNotificationType" OR eventMessage CONTAINS[c] "no signal" OR subsystem == "com.apple.iokit.IOMobileGraphicsFamily" OR subsystem == "com.apple.DisplayServices"' 2>/dev/null | grep -F "$T"
} | tee "$OUT"
pbcopy < "$OUT" 2>/dev/null
echo; echo "Saved to $OUT and copied — paste it in."
