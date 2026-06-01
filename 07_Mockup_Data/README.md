# Phase I Mockup Data — README

Synthetic test corpus for the AI Issue Router classifier. All content is fake
(no real PII). Every record carries a **ground-truth label** so the test
harness can score classification + routing accuracy.

## Files

| File | Records | Purpose |
|------|---------|---------|
| `sms/sms_mock_1k.csv` (+ `.jsonl`) | 1,000 | SMS messages from a single dummy test user/number |
| `whatsapp/whatsapp_mock_1k.csv` (+ `.jsonl`) | 1,000 | WhatsApp messages from the same dummy number |
| `email/email_mock_1k.csv` (+ `.jsonl`) | 1,000 | Mixed email: 1/3 non-business, 1/3 buy/sell, 1/3 rent/manage |
| `mockup_summary.json` | — | Label distribution summary |

## Dummy test user

| Field | Value |
|-------|-------|
| Name  | `Tester Buyer-Smith` |
| Phone | `+1 (404) 555-0199`  |
| Email | `tester.buyer@example-mail.test` |

The single dummy user sends BOTH the 1K SMS and the 1K WhatsApp messages so we
can simulate a realistic cross-channel conversation history attached to one
contact in the CRM.

## Record schema (SMS / WhatsApp)

```
msg_id, channel, received_at, sender_name, sender_phone, body,
address_in_msg, county_in_msg, listing_in_msg,
expected_topic, expected_urgency, expected_owner
```

## Record schema (Email)

```
msg_id, channel, received_at, sender_name, sender_email, subject, body,
address_in_msg, county_in_msg, listing_in_msg, bucket,
expected_topic, expected_urgency, expected_owner
```

`bucket` = `non_business` | `buy_sell` | `rent_manage` (per the user's
requested 1/3-1/3-1/3 split for emails).

## Label taxonomy

`expected_topic` and `expected_urgency` follow the taxonomy defined in
`../06_Taxonomy/taxonomy.json` and `Urgency_Topic_Taxonomy_and_Team_Tagging.docx`.

`expected_owner` is computed by applying the rules in
`../04_Rules_and_Routing/routing_rules.yaml` (topic → primary owner, with the
county-based geo override that sends outlying-county buyer/seller/showing
messages to Fabi).

## Suggested usage

1. **Smoke test** the classifier prompt against ~50 random rows per channel.
2. **Bulk evaluation**: feed all 3K records through the staging pipeline and
   compute confusion matrices for urgency, topic, and owner separately.
3. **Edge-case sweep**: filter to `expected_urgency == "P0"` and verify every
   record fires the SMS+escalation path.
4. **Geo routing**: filter to `county_in_msg in {Cobb, Gwinnett, Forsyth, Cherokee}`
   and confirm `expected_owner == Fabi` for lead/showing topics.
5. **Spam filter**: rows tagged `expected_topic in {spam, marketing_promo, personal}`
   must NOT route to any owner (expected_owner = `ARCHIVE`).

## Regenerating

```powershell
python generate_mockup_data.py
```

The generator is seeded (`random.seed(20260517)`) so output is deterministic.
Change the seed at the top of the file to draw a fresh sample.

## Caveats

- All addresses, listing IDs, names, phone numbers, and emails are synthetic.
- Templates are intentionally short — a real conversation often spans 5–20
  turns. Phase 4 (Communication Digest) will exercise long threads; Phase I
  testing only needs single-message classification accuracy.
- Voice-note transcription is simulated with a `Voice note transcription:`
  prefix on ~15% of WhatsApp rows — the real pipeline will hand the audio to
  Whisper or equivalent before classification.
