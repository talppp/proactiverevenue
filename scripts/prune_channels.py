#!/usr/bin/env python3
"""Phase 1 channel pruning — remove Gmail + Instagram from the local
Phase1_AI_Issue_Router folder.

Scope confirmed 2026-05-28: KEEP sms, sms_phone, whatsapp, apple_mail.
                            REMOVE gmail, instagram.

USAGE (from your laptop, in PowerShell or bash):

  # 1. dry-run first — shows every change, makes nothing
  python prune_channels.py --root "C:\\Users\\USER\\Documents\\AI_Audit_Meetings_FILES\\Natalie -Relator\\Phase1_AI_Issue_Router"

  # 2. apply the changes
  python prune_channels.py --root "<same path>" --apply

  # 3. regenerate the diagrams + DOCX from the updated build_*.py sources
  python prune_channels.py --root "<same path>" --regen

The script edits source files in place. It does NOT touch your backup at
Phase1_AI_Issue_Router_OLD_backup_2026-05-28_2010, so you can always revert.

What it does:
  * Walks .py, .yaml, .yml, .md, .json, .txt, .csv (text only).
  * Skips .git/, .venv/, __pycache__/, .pytest_cache/, .ruff_cache/,
    proactiverevenue/ (already cleaned at the repo level).
  * Removes Gmail + Instagram mentions using a small set of safe pattern
    edits (channel-list entries, dispatch-table entries, webhook routes,
    prose mentions in docs). Anything ambiguous is reported, not edited.
  * Re-runs build_diagram.py, build_discovery.py, build_rules.py,
    build_workflow_options.py, build_workflow_mapping.py, build_taxonomy.py,
    build_review.py if found, so the DOCX/PDF/PNG regenerate from the
    edited source.

What it does NOT do:
  * Edit DOCX/PDF/PNG directly (those regenerate from the .py sources).
  * Touch your backup folder.
  * Delete files unless the entire file's purpose was a removed channel.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

CHANNELS_OUT = {"gmail", "instagram"}
CHANNELS_KEEP = {"sms", "sms_phone", "whatsapp", "apple_mail", "test"}

TEXT_EXTS = {".py", ".yaml", ".yml", ".md", ".json", ".txt", ".csv", ".cfg",
             ".ini", ".toml", ".env", ".example", ".rst", ".html"}

SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".vscode", ".idea", "node_modules", "dist", "build",
    ".mypy_cache", "proactiverevenue",   # cleaned at the GitHub repo level
}

# Pattern: a Python list of channel strings, e.g.
#   _CHANNELS = ["sms", "whatsapp", "gmail", "apple_mail", "instagram", "test"]
LIST_ITEM_RE = re.compile(
    r"""(['"])(gmail|instagram)\1\s*,?\s*"""
)

# Pattern: dict entry, e.g.   "gmail": _normalize_gmail,
DICT_ENTRY_RE = re.compile(
    r"""^[ \t]*['"](gmail|instagram)['"][ \t]*:[ \t]*[^,\n]*,?\s*\n""",
    re.MULTILINE,
)

# Pattern: function definitions whose name embeds the channel
FUNC_DEF_RE = re.compile(
    r"""^def\s+_normalize_(gmail|instagram)\s*\([^)]*\)[^\n]*:\n
        (?:[ \t]+[^\n]*\n)+                # indented body
    """,
    re.MULTILINE | re.VERBOSE,
)

# Pattern: webhook route decorator, e.g.
#   @router.post("/webhook/gmail")
ROUTE_RE = re.compile(
    r"""^@router\.(?:post|get)\(["']/?webhook[s]?/(gmail|instagram)["']\)\n
        (?:[ \t]*async\s+def|def)[^\n]*\n
        (?:[ \t]+[^\n]*\n)+
    """,
    re.MULTILINE | re.VERBOSE,
)

# Pattern: imports referencing the channel module
IMPORT_RE = re.compile(
    r"""^(?:from\s+\S*(?:gmail|instagram)\S*\s+import\s+[^\n]+
        |import\s+\S*(?:gmail|instagram)\S*[^\n]*)\n""",
    re.MULTILINE | re.VERBOSE,
)

# Prose mentions in markdown / docs — flagged for review, not auto-removed.
PROSE_MENTION_RE = re.compile(
    r"(?i)\b(gmail|instagram)\b"
)

# ---------------------------------------------------------------------------

def should_skip(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts
    return any(p in SKIP_DIRS for p in parts)


def edit_text(path: Path, text: str) -> tuple[str, list[str]]:
    """Apply safe automated edits. Returns (new_text, notes)."""
    notes: list[str] = []
    new = text

    # 1. Remove list items like  "gmail",  or  'instagram',
    def _list_sub(m):
        notes.append(f"  - removed channel list entry: {m.group(2)!r}")
        return ""
    new = LIST_ITEM_RE.sub(_list_sub, new)

    # 2. Remove dict entries like  "gmail": _normalize_gmail,
    def _dict_sub(m):
        notes.append(f"  - removed dispatch dict entry: {m.group(1)!r}")
        return ""
    new = DICT_ENTRY_RE.sub(_dict_sub, new)

    # 3. Remove function bodies _normalize_gmail / _normalize_instagram
    def _func_sub(m):
        notes.append(f"  - removed function: _normalize_{m.group(1)}")
        return ""
    new = FUNC_DEF_RE.sub(_func_sub, new)

    # 4. Remove webhook routes /webhook/gmail and /webhook/instagram
    def _route_sub(m):
        notes.append(f"  - removed webhook route: /webhook/{m.group(1)}")
        return ""
    new = ROUTE_RE.sub(_route_sub, new)

    # 5. Remove channel-module imports
    def _imp_sub(m):
        notes.append(f"  - removed import: {m.group(0).strip()!r}")
        return ""
    new = IMPORT_RE.sub(_imp_sub, new)

    # 6. Collapse double blank lines we may have produced
    new = re.sub(r"\n{3,}", "\n\n", new)

    return new, notes


def find_prose_mentions(path: Path, text: str) -> list[str]:
    """Return human-readable warnings for each prose hit."""
    hits = []
    for i, line in enumerate(text.splitlines(), start=1):
        if PROSE_MENTION_RE.search(line):
            ctx = line.strip()[:120]
            hits.append(f"  - L{i}: {ctx}")
    return hits


def regen_doc_sources(root: Path) -> None:
    """Re-run the build_*.py scripts that produce DOCX/PDF/PNG. They
    sit under 01_Diagrams/, 02_Workflow_Designs/, 03_Discovery/, etc."""
    candidates = [
        "01_Diagrams/build_diagram.py",
        "02_Workflow_Designs/build_workflow_options.py",
        "03_Discovery/build_discovery.py",
        "04_Rules_and_Routing/build_rules.py",
        "05_Workflow_Mapping/build_workflow_mapping.py",
        "06_Taxonomy/build_taxonomy.py",
        "08_Review_Schema_Plan/build_review.py",
        "08_Review_Schema_Plan/build_schema.py",
        "08_Review_Schema_Plan/build_plan.py",
    ]
    for rel in candidates:
        script = root / rel
        if not script.exists():
            print(f"  [skip] {rel}  (not present)")
            continue
        print(f"  [run]  {rel}")
        try:
            subprocess.run(
                [sys.executable, str(script)],
                cwd=str(script.parent),
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"  [FAIL] {rel}  (exit {e.returncode})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True,
                    help="Path to your Phase1_AI_Issue_Router folder.")
    ap.add_argument("--apply", action="store_true",
                    help="Make changes. Without this, dry-run only.")
    ap.add_argument("--regen", action="store_true",
                    help="After --apply, re-run build_*.py to regenerate "
                         "DOCX/PDF/PNG.")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        return 2

    print(f"Scanning {root}")
    print(f"Mode: {'APPLY' if args.apply else 'dry-run'}")
    print()

    total_files = 0
    edited_files = 0
    prose_files = 0
    all_prose: list[tuple[Path, list[str]]] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if should_skip(path, root):
            continue
        if path.suffix.lower() not in TEXT_EXTS:
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        total_files += 1
        new_text, notes = edit_text(path, text)
        prose_hits = find_prose_mentions(path, new_text)

        if notes:
            edited_files += 1
            rel = path.relative_to(root)
            print(f"[edit] {rel}")
            for n in notes:
                print(n)
            if args.apply:
                path.write_text(new_text, encoding="utf-8")

        if prose_hits:
            prose_files += 1
            all_prose.append((path.relative_to(root), prose_hits))

    print()
    print("=" * 60)
    print(f"Files scanned:        {total_files}")
    print(f"Files auto-edited:    {edited_files}")
    print(f"Files w/ prose hits:  {prose_files}  (review manually)")
    print("=" * 60)

    if all_prose:
        print()
        print("--- PROSE MENTIONS (not auto-edited; review and decide) ---")
        for rel, hits in all_prose:
            print(f"\n{rel}")
            for h in hits[:20]:                  # cap to 20 per file
                print(h)
            if len(hits) > 20:
                print(f"  ... +{len(hits) - 20} more")

    if args.regen:
        if not args.apply:
            print("\nNOTE: --regen without --apply is a no-op (nothing was edited).")
        else:
            print("\n--- REGENERATING DOCX/PDF/PNG from build_*.py sources ---")
            regen_doc_sources(root)

    if not args.apply:
        print()
        print("Dry-run only. Re-run with --apply to write changes.")
        print("Add --regen to also re-execute the build_*.py generators.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
