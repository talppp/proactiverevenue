# Phase 2 alignment plan — closing the "second brain" gaps

**Date:** 2026-06-10 · **Baseline:** the 4 gaps identified in the
Karpathy-alignment review (no deployed intelligence, no contact memory,
no feedback loop, budget configured-but-not-enforced).

## Status after this increment

| Gap | Before | Now | Remaining for full Phase 2 |
|---|---|---|---|
| 1. Deployed intelligence | `LoggingPipeline` stub — service received but didn't think | **`LitePipeline` live**: rule-based urgency (P0–P3) + topic classification + owner routing, keyword tables lifted from `routing_rules.yaml` | Merge the Drive-project L2–L6 (LLM classifiers with geo + VIP, delivery) |
| 2. Contact memory | Every message classified in isolation | **`recent_by_sender()`** on both stores; every classification carries `thread_depth` and feeds prior bodies to the LLM fallback | Vector/semantic recall across channels; deal-level threading |
| 3. Feedback loop | Nathalie's corrections vanished | **`sms_feedback` table + `POST /admin/sms_inbox_raw/{id}/feedback` + `GET /admin/feedback`** — corrections accumulate as labeled eval data | Auto-eval job that scores the classifier against feedback weekly; keyword-table tuning from corrections |
| 4. Token budget enforcement | `LLM_DAILY_USD_CEILING` was config-only | **`BudgetGuard`** gates every LLM call at runtime; spend persisted in `llm_spend` (survives restarts, shared across workers); fails **closed** when the DB is unreachable | Per-message cost ledger; alerting at 80% of ceiling |

## What shipped (commit-level)

- `app/l2_lite.py` — `classify_urgency`, `classify_topic`, `route_owner`,
  `BudgetGuard`, `LitePipeline`, `make_default_llm_fn` (Anthropic-backed
  fallback, only active when `ANTHROPIC_API_KEY` is set).
- `app/sms_inbox_store.py` — 5 new protocol methods implemented in both
  backends: `recent_by_sender`, `insert_feedback`, `list_feedback`,
  `add_llm_spend`, `llm_spend_today`.
- `app/admin.py` — feedback record + export endpoints (admin-bearer gated).
- `app/main_l1.py` — lifespan now wires `LitePipeline` instead of the
  logging stub; the deployed service classifies for real.
- `migrations/002_feedback_and_spend.sql` — `sms_feedback`, `llm_spend`,
  and a per-sender index for contact-memory lookups. **Applied to the
  live Supabase project.**
- CI: the Postgres job now applies both migrations.
- Tests: 18 new (classifier rules, budget ceiling + accumulation,
  pipeline classify/route/unsure/thread-depth, LLM gating in all four
  states, feedback roundtrip + 404 + 422, store-contract additions for
  all 5 new methods on both backends). **63 total, 0 skipped against
  real Postgres.**

## Design decisions worth recording

1. **Rules first, LLM second.** The keyword tables resolve the common
   80% deterministically at zero cost and zero latency. The LLM is only
   consulted when rules are unsure (`min(conf) < CONFIDENCE_FLOOR`) AND
   budget remains. Confident rule hits never spend a token.
2. **Budget fails closed.** If the spend table is unreachable, the LLM
   call is refused, not allowed. A degraded DB can't cause unbounded
   spend.
3. **Unsure → owner, flagged.** Low-confidence messages route to
   Nathalie with `is_unsure: true` rather than guessing — the human
   stays the backstop, and her corrections feed gap 3.
4. **LLM injected as a callable.** Tests exercise all four gating states
   (skipped-confident, invoked-unsure, refused-over-budget,
   failed-falls-back) without network access.
5. **Memory is enrichment, not a gate.** If the history lookup fails,
   classification proceeds with `thread_depth=0` instead of erroring —
   an enrichment outage never blocks message flow.

## Phase 2 remainder (ordered tickets)

1. **P2-1** Merge Drive L2–L6 into the repo; `LitePipeline` becomes the
   fallback when the full pipeline errors. (1–2 d)
2. **P2-2** Weekly eval job: score classifier output against
   `sms_feedback`; emit accuracy per topic/urgency. (0.5 d)
3. **P2-3** Spend alerting at 80% ceiling → admin notification. (0.25 d)
4. **P2-4** Thread continuity: same-sender messages within N hours share
   a thread id; urgency escalates on repeat unanswered texts. (1 d)
5. **P2-5** Semantic recall: embeddings over message history (pgvector
   is available on Supabase free tier). (1–2 d)
