#!/usr/bin/env bash
# correlate.sh — run within a minute or two of an actual blackout.
# Answers the one question that decides everything: did macOS decide to turn
# the display off at that moment, or did it just lose the picture?
#
#   ./correlate.sh 14:32          # the wall-clock time (HH:MM) you saw it happen
#   ./correlate.sh 14:32:15       # or with seconds, if you caught it precisely

set -uo pipefail
[ "$(uname -s)" = "Darwin" ] || { echo "Run this on the Mac mini."; exit 1; }
T="${1:?usage: $0 HH:MM[:SS]  (the time you saw the screen go black)}"
TODAY=$(date +%Y-%m-%d)
OUT="$HOME/thinkvision-logs/correlate-$(date +%H%M%S).txt"
mkdir -p "$(dirname "$OUT")"

echo "Checking whether macOS logged an intentional display-off near $T ..."
{
echo "=== pmset: did macOS itself turn the display off around $T? ==="
pmset -g log | grep -iE "Display is turned" | grep "$TODAY" | grep -E "$(echo "$T" | cut -c1-4)" 
echo
echo "(if nothing printed above: macOS's own power log has NO record of turning"
echo " the display off at that time — meaning macOS did not do this on purpose.)"
} | tee "$OUT"

echo
echo "=== link / firmware events in the 4 minutes around $T ==="
sudo log show --start "${TODAY} $(date -j -f "%H:%M:%S" "${T}:00" "+%H:%M:%S" 2>/dev/null || echo "$T:00")" \
  --last 1m --style compact --info \
  --predicate 'eventMessage CONTAINS[c] "hpd" OR eventMessage CONTAINS[c] "hotplug" OR eventMessage CONTAINS[c] "link training" OR eventMessage CONTAINS[c] "edid" OR eventMessage CONTAINS[c] "dcpav" OR eventMessage CONTAINS[c] "no signal" OR subsystem == "com.apple.iokit.IOMobileGraphicsFamily"' \
  2>/dev/null | tee -a "$OUT"

echo
echo "Saved to $OUT — paste both sections back into the chat."
