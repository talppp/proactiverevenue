# Channel-pruning audit — 2026-05-28

**Scope confirmed:** keep SMS, sms_phone, WhatsApp, Apple Mail.
**Remove:** Gmail, Instagram.

## What's been changed

### In this GitHub repo (commit `ea3c093`)
| File | Change |
|---|---|
| `docs/integration/normalize_sms_phone.md` | Dropped `gmail` and `instagram` from the example `_NORMALIZERS` dispatch table; added a "Phase 1 scope note" alerting integrators to remove these channels in the live `normalize.py` |
| `docs/L1_SMS_Phone_Ingestion_Design.md` | Updated the rejected-variants list in §2.3 (no more "email bridge using Gmail adapter" — the adapter is gone); updated ADR-001 option 2 with "Deferred / out of scope after Gmail channel was dropped" |

### Still to do — in your local `Phase1_AI_Issue_Router/` folder
Use `scripts/prune_channels.py` (committed to this repo) to do them
automatically.

| Folder / file | Expected change | How |
|---|---|---|
| `app/ingestion/normalize.py` | Remove `"gmail": _normalize_gmail` and `"instagram": _normalize_instagram` from the dispatch dict, delete the two functions, drop module-level imports | script auto-edit |
| `app/ingestion/inbound.py` | Remove `@router.post("/webhook/gmail")` and `/webhook/instagram` handlers | script auto-edit |
| `app/ingestion/gmail.py` (if exists) | Whole-file deletion candidate — review first | script flags |
| `app/ingestion/instagram.py` (if exists) | Whole-file deletion candidate — review first | script flags |
| `app/classifier/*.py` | Drop any channel-list / channel-dispatch lookups that include gmail/instagram | script auto-edit (lists) |
| `tests/test_normalize.py` (and any test_*gmail* / test_*instagram*) | Remove tests for gone channels | manual delete after script flags |
| `01_Diagrams/build_diagram.py` | Source for the CAD flowchart DOCX/PDF/PNG. Remove gmail + instagram boxes from the channel array | script auto-edit (lists) |
| `01_Diagrams/AI_Issue_Router_FlowChart_CAD.docx`/`.pdf`/`.png` | Regenerate from build_diagram.py | `--regen` flag re-runs the script |
| `02_Workflow_Designs/build_workflow_options.py` + DOCX | Same pattern: edit source + regenerate | `--regen` |
| `03_Discovery/build_discovery.py` + `Inbound_Channels_Discovery_Draft.docx` | Drop the Gmail + Instagram sections; regenerate DOCX | script auto-edits where it can, prose mentions get flagged for manual review |
| `04_Rules_and_Routing/build_rules.py` + `Rules_and_Routing_Conditions_Draft.docx` | YAML file unaffected (no channel enum); only the human-readable DOCX may mention the channels | script auto-edits build script |
| `05_Workflow_Mapping/build_workflow_mapping.py` + DOCX | Same pattern | `--regen` |
| `06_Taxonomy/build_taxonomy.py` + `taxonomy.json` + DOCX | Likely few mentions; script flags | script auto-edits |
| `07_Mockup_Data/generate_mockup_data.py` + the gmail mock CSVs/JSONLs | The Gmail mock data folder can be deleted entirely | manual delete after script flags |
| `08_Review_Schema_Plan/build_review.py` (and `build_schema.py`, `build_plan.py`) + their DOCX | Same pattern | `--regen` |
| `09_Advanced_Tests/*.py` | Remove tests referencing gmail/instagram channels | script flags |
| `README.md` | Update the §03_Discovery and §07_Mockup_Data table entries to drop Gmail/Instagram; update the "WhatsApp / Gmail / Messages" line and the "1K SMS + 1K WhatsApp + 1K mixed emails" caption | script auto-edits where safe, prose flagged for manual review |
| `docs/EMULATOR_SETUP.md` | Remove Gmail app from the emulator install steps | script flags prose |

## How to run

In **PowerShell** on your laptop, from anywhere:

```powershell
# 1. dry-run (read-only — shows every change it WOULD make)
python `
  "C:\Users\USER\Documents\AI_Audit_Meetings_FILES\Natalie -Relator\Phase1_AI_Issue_Router\proactiverevenue\scripts\prune_channels.py" `
  --root "C:\Users\USER\Documents\AI_Audit_Meetings_FILES\Natalie -Relator\Phase1_AI_Issue_Router"

# 2. review the output. If happy, apply:
python `
  ".\proactiverevenue\scripts\prune_channels.py" `
  --root "C:\Users\USER\Documents\AI_Audit_Meetings_FILES\Natalie -Relator\Phase1_AI_Issue_Router" `
  --apply

# 3. regenerate the diagrams + DOCX from the now-updated build_*.py sources
python `
  ".\proactiverevenue\scripts\prune_channels.py" `
  --root "C:\Users\USER\Documents\AI_Audit_Meetings_FILES\Natalie -Relator\Phase1_AI_Issue_Router" `
  --apply --regen
```

The script:
- Has **dry-run by default** — never edits without `--apply`.
- **Doesn't touch** `proactiverevenue/` (already cleaned at the repo level),
  `.git/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`.
- **Doesn't touch** your backup folder `Phase1_AI_Issue_Router_OLD_backup_2026-05-28_2010`.
- For binary files (DOCX/PDF/PNG): edits the `build_*.py` source, then
  `--regen` re-runs the build script which overwrites the binary.

## Verifying after the run

```powershell
# Search for any leftover references the script may have missed.
# (Select-String has no -Recurse; recursion belongs on Get-ChildItem.)
$root = "C:\Users\USER\Documents\AI_Audit_Meetings_FILES\Natalie -Relator\Phase1_AI_Issue_Router"

Get-ChildItem -Path $root -Recurse -File -Include "*.py","*.md","*.yaml","*.yml","*.json","*.txt","*.toml","*.cfg","*.ini" |
    Where-Object { $_.FullName -notmatch "\\(?:\.git|__pycache__|\.venv|\.pytest_cache|\.ruff_cache|proactiverevenue|backup|OLD)\\" } |
    Select-String -Pattern "(?i)\bgmail\b|\binstagram\b" |
    Select-Object Path, LineNumber, Line |
    Format-Table -AutoSize -Wrap
```

If the results only show comments like "removed", "deprecated", or
"out of scope", you're done. Otherwise reply with the leftovers and I'll
extend the script.

## Final step — rename backup to `_OLD`

After the verification above is clean:

```powershell
# Confirm the new folder is the canonical state first by running smoke tests.
# Then rename the backup so it's clearly archival:
$bak = "C:\Users\USER\Documents\AI_Audit_Meetings_FILES\Natalie -Relator\Phase1_AI_Issue_Router_OLD_backup_2026-05-28_2010"
$dst = "C:\Users\USER\Documents\AI_Audit_Meetings_FILES\Natalie -Relator\Phase1_AI_Issue_Router_OLD"
Rename-Item -Path $bak -NewName "Phase1_AI_Issue_Router_OLD"
```
