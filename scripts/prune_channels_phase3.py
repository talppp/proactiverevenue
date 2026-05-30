#!/usr/bin/env python3
"""Phase 3: restore broken files from backup, then re-prune with a
line-based filter that doesn't mangle dict-literal syntax.

Phase-1's regex removed `"gmail"` / `"instagram"` wherever they appeared
quoted, including as values inside dict literals like:
    {"channel": "gmail", "provider_msg_id": ...}
That left `{"channel": , "provider_msg_id": ...}` — broken syntax.

Phase-3 restores those files from the backup and applies a safer
WHOLE-LINE filter: if a line contains a quoted "gmail" / "instagram",
the entire line is dropped. For function defs and routes, we strip
them with their bodies. Tuples in build_diagram.py and the prose
mention in README.md are handled with targeted edits.

Usage:
    python prune_channels_phase3.py \\
        --root   "C:\\Users\\USER\\...\\Phase1_AI_Issue_Router" \\
        --backup "C:\\Users\\USER\\...\\Phase1_AI_Issue_Router_OLD_backup_2026-05-28_2010"

    # dry-run prints what it would do; add --apply to write.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

CHANNELS = ("gmail", "instagram")

# Files corrupted by phase-1 — restore from backup, then re-clean.
BROKEN = [
    "app/ingestion/inbound.py",
    "app/ingestion/normalize.py",
    "scripts/feed_mockup.py",
]

# Additional leftover mentions to clean.
ALSO_CLEAN = [
    "01_Diagrams/build_diagram.py",
    "README.md",
    "docs/EMULATOR_SETUP.md",
]


def _strip_function_def(text: str) -> tuple[str, int]:
    """Drop `def *gmail*(...)` / `def *instagram*(...)` and their indented bodies."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    body_indent: int | None = None
    removed = 0
    for line in lines:
        if body_indent is not None:
            if line.strip() == "":
                out.append(line)
                continue
            indent = len(line) - len(line.lstrip())
            if indent > body_indent:
                continue
            body_indent = None
        m = re.match(
            r"^(\s*)(?:async\s+)?def\s+\S*(?:gmail|instagram)\S*\s*\(",
            line,
            re.IGNORECASE,
        )
        if m:
            body_indent = len(m.group(1))
            removed += 1
            continue
        out.append(line)
    return "".join(out), removed


def _strip_route(text: str) -> tuple[str, int]:
    """Drop `@router.post("/gmail")` or `/instagram` decorators with their function."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    drop_next_def = False
    body_indent: int | None = None
    removed = 0
    for line in lines:
        if body_indent is not None:
            if line.strip() == "":
                out.append(line)
                continue
            indent = len(line) - len(line.lstrip())
            if indent > body_indent:
                continue
            body_indent = None
        if drop_next_def:
            m = re.match(r"^(\s*)(?:async\s+)?def\s+", line)
            if m:
                body_indent = len(m.group(1))
                drop_next_def = False
                continue
            if line.strip() == "" or line.strip().startswith("#"):
                continue
            drop_next_def = False
        if re.match(
            r"^\s*@router\.(?:post|get)\(\s*['\"]/?(?:gmail|instagram)\b",
            line,
            re.IGNORECASE,
        ):
            drop_next_def = True
            removed += 1
            continue
        out.append(line)
    return "".join(out), removed


def _strip_quoted_lines(text: str) -> tuple[str, int]:
    """Drop any line containing a quoted 'gmail' or 'instagram' as a full token."""
    pat = re.compile(r"""(["'])(gmail|instagram)\1""", re.IGNORECASE)
    lines = text.splitlines(keepends=True)
    out = [l for l in lines if not pat.search(l)]
    return "".join(out), len(lines) - len(out)


def _strip_url_paths(text: str) -> tuple[str, int]:
    """Drop any line containing /webhook/gmail or /webhook/instagram."""
    pat = re.compile(r"/webhook[s]?/(?:gmail|instagram)\b", re.IGNORECASE)
    lines = text.splitlines(keepends=True)
    out = [l for l in lines if not pat.search(l)]
    return "".join(out), len(lines) - len(out)


def _strip_imports(text: str) -> tuple[str, int]:
    """Drop import statements referencing gmail/instagram modules."""
    pat = re.compile(
        r"^(?:from\s+\S*(?:gmail|instagram)|import\s+\S*(?:gmail|instagram))",
        re.IGNORECASE | re.MULTILINE,
    )
    lines = text.splitlines(keepends=True)
    out = [l for l in lines if not pat.search(l)]
    return "".join(out), len(lines) - len(out)


def _strip_tuple_box(text: str) -> tuple[str, int]:
    """Targeted: build_diagram.py tuples like (120, "INSTAGRAM DM", "..."),"""
    pat = re.compile(
        r'^\s*\(\s*\d+\s*,\s*"(?:INSTAGRAM\s+DM|GMAIL)"\s*,\s*"[^"]*"\s*\)\s*,?\s*\n',
        re.MULTILINE | re.IGNORECASE,
    )
    new, n = pat.subn("", text)
    return new, n


def _readme_fix(text: str) -> tuple[str, int]:
    """README's 'WhatsApp / Gmail / Messages' line."""
    pat = re.compile(r"WhatsApp\s*/\s*Gmail\s*/\s*Messages")
    new, n = pat.subn("WhatsApp / Mail / Messages", text)
    return new, n


def clean(text: str, *, is_readme: bool = False, is_diagram: bool = False) -> tuple[str, dict]:
    counts: dict[str, int] = {}
    text, counts["function_defs"] = _strip_function_def(text)
    text, counts["routes"]        = _strip_route(text)
    if is_diagram:
        text, counts["tuple_boxes"] = _strip_tuple_box(text)
    text, counts["quoted_lines"]  = _strip_quoted_lines(text)
    text, counts["url_paths"]     = _strip_url_paths(text)
    text, counts["imports"]       = _strip_imports(text)
    if is_readme:
        text, counts["readme_fix"] = _readme_fix(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text, counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--backup", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    bak = Path(args.backup).resolve()
    if not root.is_dir() or not bak.is_dir():
        print(f"ERROR: --root or --backup not a directory", file=sys.stderr)
        return 2

    print(f"Live:   {root}")
    print(f"Backup: {bak}")
    print(f"Mode:   {'APPLY' if args.apply else 'dry-run'}")
    print()

    print("=" * 60)
    print("Step 1: restore broken files from backup")
    print("=" * 60)
    for rel in BROKEN:
        src, dst = bak / rel, root / rel
        if not src.exists():
            print(f"  [skip] {rel}: not in backup")
            continue
        if args.apply:
            shutil.copy2(src, dst)
            print(f"  [restored] {rel}  ({src.stat().st_size} bytes)")
        else:
            print(f"  [would restore] {rel}  ({src.stat().st_size} bytes)")
    print()

    print("=" * 60)
    print("Step 2: clean restored + additional files")
    print("=" * 60)
    for rel in BROKEN + ALSO_CLEAN:
        path = root / rel
        if not path.exists():
            print(f"  [skip] {rel}: not found")
            continue
        text = path.read_text(encoding="utf-8")
        is_readme  = rel.endswith("README.md")
        is_diagram = "build_diagram.py" in rel
        new_text, counts = clean(text, is_readme=is_readme, is_diagram=is_diagram)
        total = sum(counts.values())
        if total == 0:
            print(f"  [no changes] {rel}")
            continue
        details = ", ".join(f"{k}={v}" for k, v in counts.items() if v > 0)
        print(f"  [{'applied' if args.apply else 'would apply'}] {rel}: {details}")
        if args.apply:
            path.write_text(new_text, encoding="utf-8")
    print()

    print("=" * 60)
    print("Step 3: syntax-check the restored Python files")
    print("=" * 60)
    import ast
    all_ok = True
    for rel in BROKEN:
        path = root / rel
        if not path.exists():
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
            print(f"  [OK] {rel}")
        except SyntaxError as e:
            all_ok = False
            print(f"  [BROKEN] {rel}: line {e.lineno}: {e.msg}")
    print()

    if not args.apply:
        print("DRY-RUN ONLY. Re-run with --apply to actually restore + clean.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
