"""
Day-by-day implementation plan for Phase I (10 working days) plus a
roadmap for how Phase 2-5 layer on top without rework.
"""
import os
from _docx_helpers import new_doc, title, h1, h2, h3, body, bullet, numbered, table, code

OUT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(OUT, "03_Implementation_Plan.docx")

d = new_doc()
title(d, "Implementation Plan")
body(d, "Apteker Realty / AI Issue Router / Phase I + roadmap to Phase 5 / Rev 1.0 / 2026-05-17")

# ===========================================================================
h1(d, "1. Operating model and tools")
table(d, [
    ["Layer",              "Tool",                                    "Why"],
    ["Orchestration",       "n8n Cloud (Starter $20/mo)",              "Visual surface for triggers + delivery; team can edit"],
    ["Classifier service",  "Python 3.12 + FastAPI on Render ($7/mo)", "Testable core; cheap; built-in TLS + secrets"],
    ["LLM",                 "Anthropic Claude Haiku 4.5",              "Cheapest production-grade model with structured-output JSON; prompt caching cuts 80% of cost"],
    ["Database",            "Postgres 15 on Render ($7/mo)",            "Same provider keeps egress free; pgcrypto + pg_trgm available"],
    ["Voice transcription", "OpenAI Whisper API",                       "~$0.006/min; reliable on WhatsApp voice notes"],
    ["Secrets",             "Render env vars + 1Password Service Account ref", "No keys in n8n or code"],
    ["Logging / errors",    "Render structured logs + Sentry free tier", "Catch 500s and slow LLM calls"],
    ["Code",                "GitHub private repo + GH Actions CI",      "Lint (ruff) + pytest on every push"],
    ["CRM card / queue",    "Notion or Trello (decide with Nathalie)",  "Both work; Notion preferred if she already uses it"],
], widths=[1.6, 2.6, 3.0])

# ===========================================================================
h1(d, "2. Day-by-day Phase I plan (10 working days)")
body(d, "Each day lists the technical work, the human decision required, and the "
        "exit criterion. Days are sized for one engineer plus part-time Nathalie "
        "availability (~1 hour/day on her side).")

DAYS = [
    ("Day 1 - Mon, Week 1", "Kickoff + provisioning", [
        ("Kickoff meeting (60 min)", "Nathalie + Fabi + Noa", "Walk through diagram; lock all decisions D1-D7, R1-R7, T1-T5."),
        ("Provision accounts",        "ProActive Revenue",    "Render project, Postgres, Anthropic API key, Twilio account, GitHub repo, Notion workspace."),
        ("File Meta App Review for WhatsApp", "ProActive Revenue", "LONGEST POLE - file Day 1, do not wait."),
        ("Twilio 10DLC brand registration", "ProActive Revenue", "1-7 day approval. File same day as Meta."),
        ("Decision: Apple Mail strategy (IMAP vs forward-via-bridge)", "Nathalie", "Default: native IMAP via Mail.app server-side rule."),
        ("Decision: Notion vs Trello vs CRM card", "Nathalie", "Pick the tool she already uses."),
    ], "Sign-off on rules YAML v1 and the four open decisions."),

    ("Day 2 - Tue, Week 1", "Database + ingestion skeleton", [
        ("Deploy Phase 1 schema",             "Engineer", "Apply 02_schema.sql to Render Postgres; seed org + team_member rows for Nathalie/Fabi/Noa."),
        ("Set up n8n cloud workspace",         "Engineer", "Create workflow stubs for each channel."),
        ("Wire Apple Mail IMAP", "Engineer", "Native IMAP via Mail.app server-side rule on her iCloud account."),
        ("Smoke test",                          "Engineer", "Confirm both channels write rows."),
    ], "5+ test messages from Apple Mail and sms_phone land in inbound_message."),

    ("Day 3 - Wed, Week 1", "Remaining channels + normalizer", [
        ("Wire Twilio SMS webhook",            "Engineer", "POST /webhook/sms; write inbound_message rows; idempotency on provider_msg_id."),
        ("Wire WhatsApp Business webhook (if approved) or stub",
                                                "Engineer", "If Meta approval still pending, use a sandbox number on Twilio for testing."),
        ("Build normalizer node in n8n",        "Engineer", "Converts every channel payload into canonical {channel, sender, body, attachments, ts, lang}."),
        ("Persist attachments to S3 / Render Disk", "Engineer", "Voice notes downloaded; transcript stub created (filled by Whisper later)."),
    ], "All available channels write canonical inbound_message rows; attachments persist."),

    ("Day 4 - Thu, Week 1", "Classifier service scaffolding", [
        ("Create FastAPI project + repo",      "Engineer", "POST /route endpoint; health check; structured logging."),
        ("Anthropic SDK + prompt caching",     "Engineer", "Cache the taxonomy + few-shot examples; only the message body varies."),
        ("Per-request token budget",            "Engineer", "Reject (and log) any prompt > 4k input tokens."),
        ("Cost tracker",                        "Engineer", "Compute cost_usd from prompt_tokens * model price; write to classification.cost_usd."),
        ("Daily cost ceiling alert",            "Engineer", "Sentry alert at $5/day."),
    ], "Service deployed to Render; smoke test returns a classification JSON for a single test message."),

    ("Day 5 - Fri, Week 1", "Classifier passes (urgency + topic + geo + summary)", [
        ("Urgency classifier prompt",          "Engineer", "Few-shot from taxonomy.json; structured output schema enforced."),
        ("Topic classifier prompt",             "Engineer", "Two-stage: business-or-personal pre-filter, then topic. Saves tokens AND improves precision."),
        ("Geo + property tagger",               "Engineer", "Regex first (MLS-#######), LLM fallback for address-only."),
        ("Multi-language: translate to English before classify if lang != en", "Engineer", "Preserve original on the record."),
        ("Test against 100 sampled mockup rows","Engineer", "Goal: topic accuracy >= 85%, urgency >= 80% before tuning."),
    ], "Classifier produces correct outputs on 100 sampled mockup messages."),

    ("Day 6 - Mon, Week 2", "Rules engine + routing decision", [
        ("YAML loader + validator",            "Engineer", "Reads routing_rules.yaml at startup; snapshots a new routing_rule_version row."),
        ("Eval pipeline (10 stages from rules doc)", "Engineer", "Filters -> urgency -> topic+geo -> owner -> overflow -> delivery; writes routing_decision."),
        ("Capacity-aware owner selection",     "Engineer", "Honors team_member.max_open_threads and skips members in current PTO window."),
        ("VIP override",                        "Engineer", "Reads contact.is_vip; if true and topic in lead-set -> Nathalie."),
        ("Confidence floor",                    "Engineer", "If classifier confidence < 0.65 -> route to Nathalie with is_unsure=true."),
        ("Human override endpoint",             "Engineer", "POST /override - re-routes a message and writes human_override + audit_event."),
    ], "Feed 200 mockup messages through the full pipe end-to-end; routing audit rows appear in DB."),

    ("Day 7 - Tue, Week 2", "Delivery + idempotency", [
        ("WhatsApp push delivery (n8n node)",  "Engineer", "Uses idempotency_key = sha256(msg_id + delivery_kind + owner_id)."),
        ("Slack / SMS owner notification",      "Engineer", "Per-owner preference."),
        ("Notion/Trello card create",           "Engineer", "Card body includes summary + suggested_reply + thread URL."),
        ("AI draft reply (in original channel)","Engineer", "Drafted only - NOT auto-sent in Phase I."),
        ("Escalation timer (P0)",                "Engineer", "Cron checks delivery_log.acknowledged_at; second push at +5 min, voice call at +10 min."),
    ], "P0 message in a test thread fires push + escalation timer correctly."),

    ("Day 8 - Wed, Week 2", "End-to-end testing with the 3K mockup", [
        ("Bulk replay 3K mockup messages",     "Engineer", "Run all sms/whatsapp/email mockup files through the pipe."),
        ("Confusion matrix vs ground truth",   "Engineer", "expected_topic vs actual; expected_urgency vs actual; expected_owner vs actual."),
        ("Tune prompts",                        "Engineer", "Adjust few-shot examples; rerun until >= 90% topic, >= 85% urgency, >= 92% owner."),
        ("Cost report",                         "Engineer", "Total $ spent on the test; project to 5k msg/mo."),
    ], "Three confusion matrices saved as PNG; cost report under $10 for the full 3K run."),

    ("Day 9 - Thu, Week 2", "Shadow run on production", [
        ("Enable production webhooks",         "Engineer", "Webhooks live but delivery layer set to LOG-ONLY (no outbound to owners yet)."),
        ("24-hour shadow run",                  "Engineer", "Collect real-world classification + routing decisions."),
        ("Nathalie reviews shadow log",         "Nathalie", "30-minute walk-through. Flag mistakes; we capture human_override rows."),
        ("Final prompt + rules adjustments",   "Engineer", "Based on shadow review."),
    ], "Nathalie approves go-live."),

    ("Day 10 - Fri, Week 2", "Go-live + training + handoff", [
        ("Flip delivery to ACTIVE",            "Engineer", "Production outbound enabled."),
        ("Monitor for 2 hours",                 "Engineer", "Watch dashboards; rollback if error rate > 2%."),
        ("Team training session (60 min)",     "ProActive + team", "Walk through Notion cards, override flow, escalation behavior, edge cases."),
        ("Maintenance docs handoff",           "ProActive", "How to add a new vendor, edit rules YAML, view logs."),
        ("Post-go-live ticketing list opened", "ProActive", "Captures the first week of tuning needs."),
    ], "System live on all 5 channels; team trained; 5-business-day acceptance window starts."),
]

for day, theme, items, exit_crit in DAYS:
    h2(d, f"{day}  -  {theme}")
    rows = [["Task", "Owner", "Detail"]]
    for task, owner, det in items:
        rows.append([task, owner, det])
    table(d, rows, widths=[2.1, 1.5, 3.6])
    body(d, f"Exit criterion: {exit_crit}")

# ===========================================================================
h1(d, "3. Risks and mitigations")
table(d, [
    ["Risk",                                                                              "Likelihood","Mitigation"],
    ["WhatsApp Business / Meta App Review takes > 10 days, derailing go-live.",           "MEDIUM",    "File Day 1; provision Twilio WhatsApp sandbox number for testing; if real number not approved by Day 9, go live on the other 4 channels and add WhatsApp on Day 11-12."],
    ["Twilio 10DLC brand rejection.",                                                     "LOW",       "Submit clean business documents; can resubmit within 24h if rejected."],
    ["Apple Mail IMAP flakiness.",                                                        "MEDIUM",    "Default to native IMAP via Mail.app server-side rule."],
    ["LLM cost overrun (long voice-note transcripts).",                                    "LOW",       "Hard token budget per request; alert at $5/day; circuit breaker at $10/day."],
    ["Routing accuracy below 85% at Day 8.",                                               "MEDIUM",    "Day 8 buffer: tune Day 8 PM + Day 9 AM if needed. Rules YAML changes are non-engineering; can adjust live."],
    ["Nathalie unavailable on Day 9 for shadow review.",                                   "LOW",       "Have a default 'no objection in 24h = approval' fallback in the SOW; shadow run continues longer if needed."],
    ["VIP list source change requires Google Contacts scope.",                              "LOW",       "Phase I uses a Notion-maintained list (no extra Google scope); migrate to Google Contacts in Phase 2 if needed."],
], widths=[3.9, 1.1, 2.5])

# ===========================================================================
h1(d, "4. Phase 2 to 5 roadmap (how it layers on Phase 1)")

h2(d, "Phase 2 - Vendor & Warranty Concierge (Weeks 2-4)")
bullet(d, "DB: add vendor, warranty, emergency_playbook, concierge_query, concierge_response (already in schema; no migration).")
bullet(d, "Code: new POST /concierge endpoint on the same Python service; reuses LLM client and rules-loader.")
bullet(d, "Hook: Router classifications with topic IN (maintenance, tenant_issue, vendor) optionally POST to /concierge BEFORE generating the AI draft reply.")
bullet(d, "Effort: 23h (per SOW); fits in 5 working days.")

h2(d, "Phase 3 - Buyer Qualification Workflow (Weeks 4-5)")
bullet(d, "DB: add buyer_profile, qualification_session, qualification_response.")
bullet(d, "Code: new POST /qualify endpoint + a simple form UI (Next.js or just a Notion form pointing at a webhook).")
bullet(d, "Hook: Router-driven trigger - when topic='buyer_lead' AND classification.is_follow_up = true, automatically open a qualification_session and send Nathalie the link to her pre-filled form.")
bullet(d, "Effort: 17h (per SOW); 3-4 days.")

h2(d, "Phase 4 - Communication Digest (Weeks 5-7)")
bullet(d, "DB: add thread, thread_message, digest, action_item.")
bullet(d, "Code: a nightly job that groups inbound_message rows into threads (by contact + property within 14 days); a digest builder that calls the LLM to summarize the thread and extract action items.")
bullet(d, "Hook: emits digest cards to the same delivery layer used in Phase 1.")
bullet(d, "Effort: 26.5h (per SOW); ~5 days.")

h2(d, "Phase 5 - Memory & Analytics Dashboard (Weeks 7-10)")
bullet(d, "DB: add metric_daily, anomaly_alert, manual_time_entry; nightly aggregation cron from raw Phase 1 tables.")
bullet(d, "Code: a small dashboard (Next.js + Recharts) reading from the views and metric_daily.")
bullet(d, "KPIs surfaced: issue heatmap (topic x channel), volume trends, top-clients, unresolved items, manual-time tracker.")
bullet(d, "Effort: 35h (per SOW); 7-10 days.")

# ===========================================================================
h1(d, "5. Acceptance and handover")
bullet(d, "Day 10 PM: Service-Provider written notice of delivery completion (per SOW section 4).")
bullet(d, "Days 11-15: Nathalie has 5 business days to raise written objections.")
bullet(d, "Day 16+: deliverable deemed accepted; monthly support engagement begins.")
bullet(d, "One post-go-live support session (up to 2h) is included within 30 days for tuning, additional rules, or new vendor entries.")

# ===========================================================================
h1(d, "6. Cost summary (one-time + recurring)")
table(d, [
    ["Line item",                                  "One-time", "Recurring"],
    ["AI Issue Router build (SOW)",                  "$3,750",   "-"],
    ["Twilio 10DLC brand fee",                        "$4",       "$2/mo"],
    ["Twilio phone number",                           "-",        "$1/mo"],
    ["Twilio SMS (5k/mo @ $0.0079)",                  "-",        "~$40/mo"],
    ["WhatsApp BSP minimums (Twilio)",                "-",        "~$15-30/mo"],
    ["Render: web service + Postgres",                "-",        "$14/mo"],
    ["n8n Cloud Starter",                              "-",        "$20/mo"],
    ["Anthropic Claude Haiku (5k msgs/mo, cached)",   "-",        "~$10-30/mo"],
    ["Whisper transcription (300 voice notes/mo)",    "-",        "~$5/mo"],
    ["Sentry free tier",                               "-",        "$0"],
    ["Total estimated monthly run",                    "-",        "~$110-150/mo"],
], widths=[3.5, 1.4, 1.8])
body(d, "This sits inside the $150-$350/mo monthly support band from the SOW. "
        "Margin in that band covers ProActive Revenue's monitoring + ~30 min/wk "
        "of tuning + on-demand rule edits.")

d.save(PATH)
print("Wrote:", PATH)
