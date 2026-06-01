# Phase I — AI Issue Router  ·  Apteker Realty

**Status:** Draft for kickoff review &nbsp;·&nbsp; **Rev:** 1.0 &nbsp;·&nbsp; **Date:** 2026-05-17
**Source SOW:** `../Sales and Legal/Contract_1_AI_Issue_Router_1.docx`
**Implementation plan:** `../Design/Nathalie_AI_Implementation_Plan_v2.pptx` (Phase 1)

This folder is the complete Phase I working pack: every deliverable required
before the build phases can start (channel discovery → workflow mapping →
prompt & routing logic → integration → testing → training).

## Deliverable index

| # | Folder | Deliverable | Format |
|---|--------|-------------|--------|
| 1 | `01_Diagrams/` | CAD-style flow chart of the full router pipeline | `.docx`, `.pdf`, `.png` |
| 2 | `02_Workflow_Designs/` | Three workflow design options (no-code / backend / hybrid) with scored comparison and recommendation | `.docx` |
| 3 | `03_Discovery/` | Inbound channels discovery — Apple Mail, WhatsApp, SMS, sms_phone (iOS Shortcuts) — access models, risks, decisions required | `.docx` |
| 4 | `04_Rules_and_Routing/` | Rules & routing conditions, human-readable + machine-readable | `.docx` + `routing_rules.yaml` |
| 5 | `05_Workflow_Mapping/` | Current vs. future workflow, data contracts between stages | `.docx` |
| 6 | `06_Taxonomy/` | Urgency + topic taxonomy, use-cases (tenant/buyer/seller/property mgmt), team tagging (Nathalie / Fabi / Noa) | `.docx` + `taxonomy.json` |
| 7 | `07_Mockup_Data/` | 1K SMS + 1K WhatsApp + 1K mixed emails, fully labeled for accuracy testing | `.csv` + `.jsonl` per channel |
| 8 | `08_Review_Schema_Plan/` | Review of drafts 1–6 with gap tables, forward-compatible DB schema for Phases 1–5 with ER diagram + DDL, and day-by-day implementation plan with Phase 2–5 roadmap | `.docx`, `.sql`, `.png`, `.pdf` |
| 9 | `app/` + `scripts/` + `tests/` + `dashboard/` + `.github/` | **Working end-to-end runtime** — FastAPI service implementing L1–L6 of the flow chart, live team-inbox dashboards, GitHub Actions CI/CD, 30 passing tests, runnable on Render starter ($14/mo all-in). | `.py`, `.html`, `.yml` |

## Run the live demo (30 seconds)

```powershell
# 0. install deps
pip install -e .

# 1. start the service (in-memory store, no Postgres required)
$env:DB_URL = "memory"
python -m uvicorn app.main:app --port 8000

# 2. open the three team-inbox dashboards in a browser
#    http://localhost:8000/inbox/nathalie
#    http://localhost:8000/inbox/fabi
#    http://localhost:8000/inbox/noa

# 3. fire the end-to-end trace
python scripts/verify_e2e.py
```

A P0 emergency from `Tester Buyer-Smith` (`+1 (404) 555-0199`) lands on
Nathalie's dashboard in red within ~50 ms. To replay the full 3 K mockup
corpus across all three dashboards: `python scripts/feed_mockup.py --rate 10`.

See [`docs/EMULATOR_SETUP.md`](docs/EMULATOR_SETUP.md) for setting up three
Android emulators (one per team member) running real WhatsApp / Mail / Messages with the dashboard pinned as a homescreen tile.

## Team routing recap

- **Nathalie** — owner; buyer/seller leads, deals, escalations, VIP. After-hours P0 OK. Core counties: **Fulton, DeKalb**.
- **Fabi** — agent for properties **outside** Nathalie's core counties (**Cobb, Gwinnett, Forsyth, Cherokee**). Leads & showings in her territory.
- **Noa** — back-office; **contracts**, documents, vendors, scheduling, admin, billing. County-agnostic.

## Recommended path

1. Walk through `01_Diagrams/AI_Issue_Router_FlowChart_CAD.pdf` with Nathalie/Fabi/Noa in a 60-minute kickoff.
2. Lock the open decisions in `03_Discovery/` (D1–D7) and `04_Rules_and_Routing/` (R1–R7) and `06_Taxonomy/` (T1–T5).
3. Approve **Option C — Hybrid** (or override) from `02_Workflow_Designs/`.
4. Kick off Meta/WhatsApp BSP approval **on day 1** — it is the longest pole.
5. Run the staging classifier against the 3K mockup messages in `07_Mockup_Data/` and tune to ≥ 90% routing accuracy before go-live.

## File map

```
Phase1_AI_Issue_Router/
├── README.md                                  ← you are here
├── 01_Diagrams/
│   ├── build_diagram.py
│   ├── AI_Issue_Router_FlowChart_CAD.png
│   ├── AI_Issue_Router_FlowChart_CAD.pdf
│   └── AI_Issue_Router_FlowChart_CAD.docx
├── 02_Workflow_Designs/
│   ├── build_workflow_options.py
│   └── AI_Issue_Router_Workflow_Design_Options.docx
├── 03_Discovery/
│   ├── build_discovery.py
│   └── Inbound_Channels_Discovery_Draft.docx
├── 04_Rules_and_Routing/
│   ├── build_rules.py
│   ├── Rules_and_Routing_Conditions_Draft.docx
│   └── routing_rules.yaml
├── 05_Workflow_Mapping/
│   ├── build_workflow_mapping.py
│   └── Workflow_Mapping_Document.docx
├── 06_Taxonomy/
│   ├── build_taxonomy.py
│   ├── Urgency_Topic_Taxonomy_and_Team_Tagging.docx
│   └── taxonomy.json
├── 07_Mockup_Data/
│   ├── README.md
│   ├── generate_mockup_data.py
│   ├── mockup_summary.json
│   ├── sms/      sms_mock_1k.csv     sms_mock_1k.jsonl
│   ├── whatsapp/ whatsapp_mock_1k.csv whatsapp_mock_1k.jsonl
│   └── email/    email_mock_1k.csv   email_mock_1k.jsonl
├── 08_Review_Schema_Plan/
│   ├── _docx_helpers.py
│   ├── build_review.py
│   ├── build_schema.py
│   ├── build_plan.py
│   ├── 01_Review_of_Phase1_Drafts.docx        ← drafts 1–6 reviewed, gap tables
│   ├── 02_DB_Schema_Forward_Compatible.docx   ← Phase 1–5 schema with ER diagram
│   ├── 02_schema.sql                          ← drop-in Postgres DDL
│   ├── 02_ER_diagram.png / .pdf               ← CAD-style ER diagram
│   └── 03_Implementation_Plan.docx            ← day-by-day Phase I + Phase 2–5 roadmap
├── app/                                        ← runtime: L1–L6 of the flow chart
│   ├── config.py · logging_setup.py · store.py · main.py · pipeline.py · ws.py
│   ├── ingestion/   inbound.py + normalize.py
│   ├── classifier/  urgency.py + topic.py + geo.py + _llm.py
│   ├── router/      rules.py + assign.py
│   └── delivery/    deliver.py
├── dashboard/inbox.html                        ← single-page team inbox UI
├── scripts/
│   ├── seed_database.py    ← idempotent seed for org/team/contact/properties/rules
│   ├── feed_mockup.py      ← bulk-replay the 3K mockup messages
│   └── verify_e2e.py       ← single P0 live trace (Tester Buyer-Smith)
├── tests/                                      ← 30 tests; in-memory store; no Anthropic key needed
├── .github/workflows/      ci.yml + deploy.yml + cost-guard.yml
├── docs/EMULATOR_SETUP.md  ← AVD setup for Nathalie/Fabi/Noa
├── pyproject.toml · render.yaml · .env.example · .gitignore
└── CLAUDE.md                                   ← guidance for future Claude Code sessions
```

Each `build_*.py` is the source for its DOCX output — edit + re-run to regenerate.
