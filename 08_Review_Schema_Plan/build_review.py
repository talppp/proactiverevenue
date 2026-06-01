"""
Review of Phase I drafts: workflow design, discovery, rules, taxonomy.
Highlights gaps and proposes resolutions.
"""
import os
from _docx_helpers import new_doc, title, h1, h2, h3, body, bullet, table

OUT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(OUT, "01_Review_of_Phase1_Drafts.docx")

d = new_doc()
title(d, "Review of Phase I Drafts")
body(d, "Apteker Realty / AI Issue Router / 2026-05-17 / Rev 1.0")
body(d, "Scope: critical review of the four Phase I drafts already produced "
        "(workflow design, inbound discovery, rules and routing, taxonomy). "
        "Each section lists what's solid, the gaps I would address before "
        "build starts, and the resolution that is reflected in the schema "
        "(Document 02) and implementation plan (Document 03).")

# ===========================================================================
h1(d, "1. Workflow design  -  Option C Hybrid")
h3(d, "Strong")
bullet(d, "Clear separation of concerns: n8n owns the visual surface (channels + delivery); a Python service owns the testable core (classifier + rules engine).")
bullet(d, "Forward-compatible with Phase 4 (digest reuses the audit log) and Phase 5 (dashboard reads the same DB).")
bullet(d, "Scored comparison gives Nathalie a defensible reason to pick Option C over A or B.")

h3(d, "Gaps to close before build")
table(d, [
    ["#",  "Gap",                                                                                "Resolution"],
    ["W1", "No observability layer specified (no metrics, no error alerting).",                    "Add structured logging + Sentry-style error alerting. Track LLM token usage per request."],
    ["W2", "No secrets management. Credentials for Twilio / Meta / Google were going to live in n8n env vars only.",
                                                                                                  "Use Render/Fly secret store or 1Password Service Account; n8n reads from credentials store, never plain env."],
    ["W3", "LLM cost ceiling not set. At 5k msgs * 3 classifier calls * ~500 input tokens, we are at ~$2/day - safe. Phase 4 long-thread digests could explode this.",
                                                                                                  "Set hard token budget per request (1500 in, 400 out) and per-day org budget; alert on 80%."],
    ["W4", "No idempotency on outbound delivery - n8n retries could double-send a WhatsApp push.", "Each delivery row carries an idempotency_key; provider call only fires on first attempt; retries no-op if key exists."],
    ["W5", "Deployment story missing - where does the Python service live? (Render? Fly.io? AWS Lambda?)",
                                                                                                  "Render (cheapest predictable plan, $7/mo) with Postgres also on Render. Plan B: Fly.io if Render latency is poor from GA."],
    ["W6", "No CI / test plan for the Python service.",                                            "GitHub Actions on push: lint (ruff) + tests (pytest) + a 50-message regression set against the mockup data."],
    ["W7", "n8n is itself a single point of failure. If n8n is down, ingestion stops.",            "n8n Cloud has a 99.9% SLO; for the Python service we add a 'late ingestion' cron that pulls every 5 min from each channel as a safety net."],
], widths=[0.4, 4.0, 2.7])

# ===========================================================================
h1(d, "2. Inbound channels discovery")
h3(d, "Strong")
bullet(d, "All five channels covered with access models, risks, and decisions required (D1-D7).")
bullet(d, "Correctly flags WhatsApp Business API approval as the longest pole.")

h3(d, "Gaps to close before build")
table(d, [
    ["#",   "Gap",                                                                                "Resolution"],
    ["C1",  "Apple Mail IMAP IDLE is brittle on iCloud (sub-30-min heartbeat; frequent reconnects). Building production-grade IMAP is more work than the SOW assumes.",
                                                                                                  "Recommend native IMAP for Apple Mail via Mail.app server-side rule."],
        ["C3",  "Voice notes (WhatsApp): the discovery flagged them but did not specify the transcription path.",
                                                                                                  "Use OpenAI Whisper (cheapest) or Anthropic audio input. Persist transcript to message_attachment.transcript. Classifier reads transcript, not audio."],
    ["C4",  "No DLP / PII redaction policy. Today the full body is sent to the LLM.",              "Redact phone numbers and email addresses with a regex pass BEFORE LLM call. Originals remain in inbound_message.body; redacted version goes to inbound_message.body_redacted."],
    ["C5",  "No data retention policy enforced (mentioned as 180 days default but not in any rule).",
                                                                                                  "Add nightly purge cron that deletes inbound_message older than retention_days; metrics retained indefinitely (aggregated)."],
    ["C6",  "VIP source ambiguity - the rule references 'Google Contacts label:VIP' but we are not sure that integration is in scope.",
                                                                                                  "Two paths: (a) sync Google Contacts labels nightly into contact.is_vip; (b) maintain a simple admin list in Notion that the rules engine reads. Pick (b) for Phase I to avoid an extra Google scope."],
], widths=[0.4, 4.0, 2.7])

# ===========================================================================
h1(d, "3. Rules and routing conditions")
h3(d, "Strong")
bullet(d, "Evaluation order is explicit (10 stages).")
bullet(d, "VIP and confidence-floor escape hatches are present.")
bullet(d, "YAML format makes the rules diffable, versionable, and easy to read.")

h3(d, "Gaps to close before build")
table(d, [
    ["#",   "Gap",                                                                                "Resolution"],
    ["R1",  "No load-balancing between Nathalie and Fabi for showings or leads in her county.",   "Add 'capacity' counter per team_member; if Nathalie has >3 open P1+ threads, leads route to Fabi even in core county."],
    ["R2",  "No PTO / availability model.",                                                       "Add team_availability table; routing skips any owner currently in 'pto' or 'sick' window and falls through to the next available."],
    ["R3",  "Geo vs topic conflict: which wins? Document said 'geo overrides topic for buyer/seller/showing' but spam_obvious filter runs first - subtle precedence not enumerated.",
                                                                                                  "Schema captures the matched rule_id and rule_version_id - the eval order in the rules engine is the source of truth, and the audit log records what fired."],
    ["R4",  "Confidence floor 0.55 may be too low for production - false routes will frustrate Fabi and Noa.",
                                                                                                  "Pilot at 0.65 in shadow mode; tune from confusion matrix after first week."],
    ["R5",  "Hebrew-language rule has no concrete implementation path.",                          "Use n8n's `lang` field from the normalizer (langdetect lib); rule fires when lang=='he' OR body matches a small Hebrew-character regex."],
    ["R6",  "After-hours rule applies to delivery, not to owner assignment - implicit.",          "Document explicitly. The team_member.after_hours_ok column controls owner selection during the 19:00-08:00 window."],
    ["R7",  "Escalation 'ring owner phone via Twilio voice prompt' lacks cost cap.",              "Cap to 3 voice calls per P0 incident; cost is ~$0.013/min so negligible, but limit prevents pager storm."],
], widths=[0.4, 4.0, 2.7])

# ===========================================================================
h1(d, "4. Urgency / topic taxonomy and team tagging")
h3(d, "Strong")
bullet(d, "Four urgency tiers + 16 topics covers the realistic spread of inbound traffic.")
bullet(d, "Use cases for tenant / buyer / seller / property management give the classifier concrete few-shot examples.")
bullet(d, "Team tagging maps cleanly to the routing YAML (Nathalie core counties, Fabi outlying, Noa back-office).")

h3(d, "Gaps to close before build")
table(d, [
    ["#",   "Gap",                                                                                "Resolution"],
    ["T1",  "Single-label classification - a message can be both 'buyer_lead' and 'scheduling' (e.g. 'I'm a buyer, let's book Friday').",
                                                                                                  "Allow secondary_topic in classification output; routing uses primary, dashboards count both."],
    ["T2",  "Spam / marketing / personal overlap will confuse classifier on edge cases.",         "Run an inexpensive pre-filter (Claude Haiku) that returns business_or_personal BEFORE the full topic classifier; saves tokens AND improves precision."],
    ["T3",  "tenant_issue vs maintenance overlap (relationship vs work).",                        "Clarify in classifier prompt: tenant_issue is the TENANT's voice describing a problem; maintenance is internal coordination (Noa to vendor). Add 2 contrastive examples to the prompt."],
    ["T4",  "No 'follow-up' classification (is this a continuation or a new conversation?).",     "Add is_follow_up: bool, computed by thread lookup (same contact + same property + within 14 days = follow-up)."],
    ["T5",  "No multi-language handling beyond Hebrew flag.",                                     "Classifier prompt is English-only; for he/es/etc., translate body to English (Anthropic translate) before classification; preserve original on the record."],
], widths=[0.4, 4.0, 2.7])

# ===========================================================================
h1(d, "5. Cross-cutting observations")
bullet(d, "The four drafts are internally consistent and align to the SOW. The gaps above are operational - "
          "what to do when things break - rather than design errors. None requires re-architecture.")
bullet(d, "The forward-compatible DB schema (Document 02) directly addresses W2 (audit), W3 (token tracking), "
          "R1/R2 (capacity, availability), T1/T4 (multi-label, follow-up).")
bullet(d, "The implementation plan (Document 03) treats W5, W6, C1, and C2 as Day-1 / Day-2 decision points so "
          "they cannot derail the 10-working-day Phase I budget.")

d.save(PATH)
print("Wrote:", PATH)
