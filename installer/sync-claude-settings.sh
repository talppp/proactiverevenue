#!/usr/bin/env bash
# sync-claude-settings.sh — bring Claude Code on this Mac in line with the
# Lenovo by merging the personal config the migration left behind: agents,
# skills, commands, hooks, output-styles, and CLAUDE.md memory.
#
# It does NOT touch credentials, and it BACKS UP ~/.claude before changing
# anything. Safe to re-run.
#
# Usage:
#   ./sync-claude-settings.sh [SOURCE]
#     SOURCE = the Lenovo's ~/.claude snapshot. If omitted, the script
#     auto-discovers it inside ~/mac-migration/payload. You can also point
#     it straight at a copy of the Lenovo's .claude folder (AirDrop/USB).
set -uo pipefail

c() { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
DEST="$HOME/.claude"

# --- 1. Locate the source snapshot ---------------------------------------
SRC="${1:-}"
if [ -z "$SRC" ]; then
  # find a dir that actually contains agents/ or skills/ under the payload
  hit="$(find "$HOME/mac-migration/payload" "$HOME/Downloads" "$HOME/Desktop" \
          -maxdepth 4 -type d \( -name skills -o -name agents \) 2>/dev/null | head -1)"
  [ -n "$hit" ] && SRC="$(dirname "$hit")"
fi

if [ -z "$SRC" ] || [ ! -d "$SRC" ]; then
  c "31" "Could not find a Lenovo .claude snapshot containing agents/ or skills/."
  echo "The migration payload apparently did not capture them."
  echo
  c "1;33" "Fix: on the LENOVO, re-run the exporter from this repo:"
  echo "    installer\\export-lenovo.ps1     (it copies the whole ~/.claude)"
  echo "Then AirDrop the resulting folder here and run:"
  echo "    ./sync-claude-settings.sh /path/to/that/.claude"
  exit 1
fi
c "36" "Source snapshot: $SRC"
echo "   contains: $(ls -1 "$SRC" 2>/dev/null | tr '\n' ' ')"

# --- 2. Back up current ~/.claude ----------------------------------------
mkdir -p "$DEST"
BACKUP="$HOME/.claude-backup-$(date +%Y%m%d-%H%M%S).tgz"
tar czf "$BACKUP" -C "$HOME" .claude 2>/dev/null && c "32" "Backed up current config → $BACKUP"

before_skills=$(ls -1 "$DEST/skills"  2>/dev/null | wc -l | tr -d ' ')
before_agents=$(ls -1 "$DEST/agents" 2>/dev/null | wc -l | tr -d ' ')

# --- 3. Merge the safe, file-based config (never credentials) -------------
# These subtrees are plain files; payload versions win, nothing is deleted.
for d in agents skills commands hooks output-styles; do
  if [ -d "$SRC/$d" ]; then
    mkdir -p "$DEST/$d"
    rsync -a "$SRC/$d"/ "$DEST/$d"/ && c "32" "synced $d/"
  fi
done
# CLAUDE.md memory (personal, global)
[ -f "$SRC/CLAUDE.md" ] && cp "$SRC/CLAUDE.md" "$DEST/CLAUDE.md" && c "32" "synced CLAUDE.md"

# --- 4. Report on plugins (these must be REINSTALLED, not copied) ---------
c "36" "Checking plugins…"
PLUGINS=""
if [ -f "$SRC/settings.json" ]; then
  PLUGINS="$(python3 - "$SRC/settings.json" <<'PY' 2>/dev/null
import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: sys.exit()
ep=d.get("enabledPlugins") or d.get("plugins") or {}
names=list(ep.keys()) if isinstance(ep,dict) else (ep if isinstance(ep,list) else [])
print("\n".join(str(n) for n in names))
PY
)"
fi
if [ -n "$PLUGINS" ]; then
  c "1;33" "Your Lenovo had these PLUGINS — reinstall them inside Claude Code with"
  c "1;33" "'/plugin marketplace add <marketplace>' then '/plugin install <name>':"
  echo "$PLUGINS" | sed 's/^/    - /'
  echo "  (Plugin skills can't be file-copied; they must be reinstalled to work.)"
else
  echo "   No plugin list found in the snapshot (or none were enabled)."
fi

# --- 5. Summary ----------------------------------------------------------
after_skills=$(ls -1 "$DEST/skills"  2>/dev/null | wc -l | tr -d ' ')
after_agents=$(ls -1 "$DEST/agents" 2>/dev/null | wc -l | tr -d ' ')
echo
c "1;32" "Done."
echo "  personal skills:  $before_skills → $after_skills"
echo "  personal agents:  $before_agents → $after_agents"
echo "  backup:           $BACKUP  (restore with: tar xzf \"$BACKUP\" -C \"\$HOME\")"
echo
echo "Open Claude Code (run 'claude' in iTerm) and check /agents and your skill list."
echo "Remember: agents & skills appear in Claude CODE (terminal), not the Claude Desktop app."
