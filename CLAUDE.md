# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo actually is

**Two things in one folder:**

1. **Deliverable-generation workspace** — folders `01_…` through `08_…` each hold a Phase I deliverable for the **AI Issue Router** (Solution 1 of 5 in the Apteker Realty AI plan). The Python files there are *source generators*; running them emits DOCX/PDF/PNG/SQL/CSV/JSONL artefacts. Edit the `.py`, never hand-edit the generated docs.
2. **Working runtime** — `app/`, `scripts/`, `tests/`, `dashboard/`, `.github/`. A FastAPI service that implements L1–L6 of the flow chart, three live WebSocket-driven team-inbox dashboards, and GitHub Actions CI/CD. 30 tests pass in <1 s with no external dependencies. Live demo: `python scripts/verify_e2e.py` after starting `python -m uvicorn app.main:app --port 8000`.

The contracted scope (`../Sales and Legal/Contract_1_AI_Issue_Router_1.docx`) is 39.3h / $3,750 / 2 weeks. The reference implementation plan slide deck is at `../Design/Nathalie_AI_Implementation_Plan_v2.pptx`.

## Commands

All scripts assume the `python` on `PATH` is a Windows Python 3.11+ with the document-generation deps (`python-docx`, `python-pptx`, `matplotlib`, `markitdown`) and the runtime deps from `pyproject.toml` installed. One-time setup: `pip install -e ".[dev]"`.

### Runtime (the live demo)

```powershell
# Start the service (in-memory store; no Postgres or API key needed)
$env:DB_URL = "memory"
python -m uvicorn app.main:app --port 8000

# In another shell — fire the end-to-end P0 trace
python scripts/verify_e2e.py

# Replay all 3K mockup messages across all 3 dashboards
python scripts/feed_mockup.py --rate 10

# Run the 30 tests (in-memory store, stub classifier; no external deps)
python -m pytest -q

# Lint
ruff check .
```

### Deliverable generators

```powershell
# Regenerate ONE deliverable (cd into its folder first)
cd 01_Diagrams           ; python build_diagram.py            # CAD flow chart + DOCX + PDF
cd 02_Workflow_Designs   ; python build_workflow_options.py
cd 03_Discovery          ; python build_discovery.py
cd 04_Rules_and_Routing  ; python build_rules.py              # also emits routing_rules.yaml
cd 05_Workflow_Mapping   ; python build_workflow_mapping.py
cd 06_Taxonomy           ; python build_taxonomy.py           # also emits taxonomy.json
cd 07_Mockup_Data        ; python generate_mockup_data.py     # 3K labelled rows, deterministic
cd 08_Review_Schema_Plan ; python build_review.py ; python build_schema.py ; python build_plan.py

# Regenerate EVERY deliverable
$base = (Get-Location).Path
foreach ($dir in '01_Diagrams','02_Workflow_Designs','03_Discovery','04_Rules_and_Routing','05_Workflow_Mapping','06_Taxonomy','07_Mockup_Data','08_Review_Schema_Plan') {
    Get-ChildItem "$base\$dir\*.py" | ForEach-Object { Push-Location $_.Directory; python $_.Name; Pop-Location }
}

# Sanity-check DOCX accessibility (alt text, heading semantics, table headers)
python audit_check.py

# Extract text from any DOCX/PPTX without opening Word
python -m markitdown "<path>.docx"
```

There is no test suite, lint config, or build system. The `mockup_summary.json` written by the data generator is the closest thing to a regression artefact — diff it to detect unintended changes after editing the generator.

## Architecture & conventions

### Single canonical helpers module
`08_Review_Schema_Plan/_docx_helpers.py` is the **only** DOCX helper module you should reuse for new docs. It produces accessibility-clean output: real `Heading 1/2/3` styles (so the Word outline pane works), `w:tblHeader` flag on first row of every table, and `descr` + `title` alt text on embedded images. Older `build_*.py` files in folders 02–06 predate this helper and reimplement their own inline `h1/h2/h3` functions; do not propagate that pattern.

### CAD-style diagram convention
Both flow-chart and ER diagrams (`01_Diagrams/build_diagram.py`, `08_Review_Schema_Plan/build_schema.py`) use matplotlib with: monospace DejaVu Sans Mono, light-grey 5-unit engineering grid, navy `#1F4E79` titles, black `#0A0A0A` body, white background, title block in bottom-right, numbered callouts in white circles, swim-lane / horizontal-band layouts. Keep this style for any new diagram — it is the visual signature of the client deliverables.

### Schema is the contract for Phases 2–5
`08_Review_Schema_Plan/02_schema.sql` is the **single source of truth** for the DB schema across all five phases (Issue Router → Vendor Concierge → Buyer Qualification → Communication Digest → Memory & Dashboard). It is forward-compatible: later phases only ADD tables that FK into the Phase 1 core, never alter them. When discussing schema in any document, cross-check against this SQL file — do not invent column or table names.

Critical invariants encoded in the schema:
- Every table carries `org_id` (multi-tenant from day one).
- Every AI decision is reproducible: `classification` stores model/version/tokens/cost; `routing_decision` references the exact `routing_rule_version` that fired.
- PII separation: `inbound_message.body` (raw, retained) vs `inbound_message.body_redacted` (the only field ever sent to an LLM).
- `delivery_log.idempotency_key` is UNIQUE — outbound retries are no-ops.

### Routing rules: two-file source of truth
`04_Rules_and_Routing/routing_rules.yaml` is the **machine-readable** truth; the sibling DOCX is the human-readable companion. When a routing rule changes, BOTH must change. The YAML is what the runtime rules engine loads; the DOCX is what Nathalie reads.

### Taxonomy: two-file source of truth
Same pattern. `06_Taxonomy/taxonomy.json` is loaded by the classifier prompt template at runtime; `Urgency_Topic_Taxonomy_and_Team_Tagging.docx` is the team-facing reference. The Python generator `build_taxonomy.py` derives BOTH from one in-source list of tuples — edit the tuples, re-run, both files update consistently.

### Mockup data is deterministic
`07_Mockup_Data/generate_mockup_data.py` seeds `random` with `20260517`, so identical re-runs produce identical output. Change the seed at the top of the file to draw a different sample. Every row carries ground-truth labels (`expected_topic`, `expected_urgency`, `expected_owner`) so a downstream classifier can be scored directly.

### Team-routing facts that drive everything
These three facts are baked into the YAML rules, the taxonomy doc, the schema seed plan, and the mockup-data labels. Cross-check any new routing logic against them:
- **Nathalie** — owner; core counties **Fulton, DeKalb**; VIP + deals + escalations; only person with `after_hours_ok = true`.
- **Fabi** — agent for **Cobb, Gwinnett, Forsyth, Cherokee** (everything *outside* Nathalie's core); buyer/seller/showing topics.
- **Noa** — back-office; **contracts, documents, vendors, scheduling, admin, billing**; county-agnostic.

### File-name conventions
- `build_*.py` → generator scripts (re-runnable).
- Numbered folders (`01_…` through `08_…`) → deliverable categories, in the order Phase I produces them.
- Inside `08_Review_Schema_Plan/`, the `01_`, `02_`, `03_` *file* prefixes correspond to Review / Schema / Plan — independent of folder numbering.

## When asked to extend the work

- **New Phase I deliverable** → new numbered folder + new `build_*.py` using `_docx_helpers.py`. Update `README.md` deliverable table and file-map tree.
- **New schema table** → edit `08_Review_Schema_Plan/build_schema.py` (which writes both the SQL DDL inline string AND the ER diagram boxes), re-run, verify the ER diagram has no overlap.
- **Routing rule change** → edit `04_Rules_and_Routing/build_rules.py` (it owns BOTH the YAML and DOCX as one source). Never edit the YAML or DOCX directly.
- **Taxonomy change** → edit the tuple lists at the top of `06_Taxonomy/build_taxonomy.py`. Both `taxonomy.json` and the DOCX regenerate from those tuples.
