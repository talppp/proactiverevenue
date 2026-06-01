"""
Mockup dummy-data generator for the AI Issue Router Phase I tests.

Outputs:
  sms/sms_mock_1k.csv        + sms_mock_1k.jsonl
  whatsapp/whatsapp_mock_1k.csv + whatsapp_mock_1k.jsonl
  email/email_mock_1k.csv    + email_mock_1k.jsonl
        (1/3 non-business, 1/3 buy/sell, 1/3 rent/manage)

Every record has a ground-truth label (expected_urgency, expected_topic,
expected_owner) so the test harness can score classifier accuracy.

All names, phone numbers, emails, and addresses are FAKE. No PII.
"""

import csv, json, os, random, hashlib
from datetime import datetime, timedelta

random.seed(20260517)

ROOT = r"C:\Users\USER\Documents\AI_Audit_Meetings_FILES\Natalie -Relator\Phase1_AI_Issue_Router\07_Mockup_Data"
DIR_SMS = os.path.join(ROOT, "sms")
DIR_WA  = os.path.join(ROOT, "whatsapp")
DIR_EM  = os.path.join(ROOT, "email")
for d in (DIR_SMS, DIR_WA, DIR_EM): os.makedirs(d, exist_ok=True)

# Test user / dummy mobile (the user requested a single test user + mobile)
TEST_USER_NAME  = "Tester Buyer-Smith"
TEST_USER_PHONE = "+14045550199"
TEST_USER_EMAIL = "tester.buyer@example-mail.test"

# Fake universe -------------------------------------------------------------
FIRST_NAMES = ["John","Sara","Mike","Lisa","David","Maria","Tom","Anna","Chris","Rachel",
               "Daniel","Emily","Ryan","Kate","Sam","Olivia","Marco","Priya","Aaron","Nina",
               "Ben","Hannah","Jorge","Linda","Pavel","Aisha","Brian","Tara","Ethan","Sophie"]
LAST_NAMES  = ["Cohen","Levi","Patel","Johnson","Garcia","Nguyen","Brown","Davis","Miller",
               "Williams","Martinez","Rodriguez","Adams","Bell","Khan","Singh","Romano","Park"]
COUNTIES_CORE = ["Fulton","DeKalb"]
COUNTIES_FABI = ["Cobb","Gwinnett","Forsyth","Cherokee"]
STREETS = ["Peachtree St","Oak Ave","Maple Dr","Magnolia Ln","Pine Rd","Birch Ct",
           "Riverbend Way","Willow Pkwy","Cedar Trail","Sycamore Blvd"]
CITIES = {
    "Fulton":   ["Atlanta","Sandy Springs","Roswell"],
    "DeKalb":   ["Decatur","Brookhaven","Dunwoody"],
    "Cobb":     ["Marietta","Smyrna","Kennesaw"],
    "Gwinnett": ["Lawrenceville","Duluth","Sugar Hill"],
    "Forsyth":  ["Cumming"],
    "Cherokee": ["Canton","Woodstock"],
}
DOMAINS = ["gmail.com","yahoo.com","outlook.com","icloud.com","example-mail.test"]
PROMO_DOMAINS = ["mailchimp.com","sendgrid.net","constantcontact.com","marketing.example.com"]

def fake_phone():
    return f"+1404555{random.randint(1000,9999)}"
def fake_email(name):
    handle = name.lower().replace(" ", ".")
    return f"{handle}{random.randint(1,99)}@{random.choice(DOMAINS)}"
def fake_name():
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
def fake_address():
    n = random.randint(100, 9999)
    st = random.choice(STREETS)
    co = random.choice(COUNTIES_CORE + COUNTIES_FABI)
    city = random.choice(CITIES[co])
    return f"{n} {st}, {city}, GA", co
def fake_mls():
    return f"MLS-{random.randint(7000000, 7999999)}"
def ts_within_days(days_back=30):
    return datetime(2026,5,17,12,0,0) - timedelta(
        minutes=random.randint(0, days_back*24*60))

# --------------------------------------------------------------------------
# Message library - templates by (topic, urgency)
# Each template returns body text; we'll fill in {name},{addr},{co},{listing}.
# --------------------------------------------------------------------------

# Real estate / business templates
TEMPLATES_BUY_SELL = [
  # (topic, urgency, body)
  ("buyer_lead", "P2", "Hi, I saw the listing at {addr}. Is it still available? I'd love a tour this weekend."),
  ("buyer_lead", "P2", "Looking for a 3BR/2BA in {co} county under $550k. Do you have anything coming up?"),
  ("buyer_lead", "P1", "Need to move ASAP - relocating for work in 3 weeks. Can you call me today?"),
  ("buyer_lead", "P0", "Our offer on {addr} expires at 5pm today, can you confirm acceptance??"),
  ("seller_lead","P2", "Hi Nathalie, I'd like to sell my house at {addr}. What's the market like in {co} right now?"),
  ("seller_lead","P2", "Considering listing the property in {co} - can we schedule a CMA?"),
  ("showing",    "P1", "Running 15 min late to the showing at {addr}, please tell the agent."),
  ("showing",    "P2", "Can we move tomorrow's showing at {addr} from 4pm to 6pm?"),
  ("showing",    "P1", "I'm at {addr} but the door is locked. Are you on the way?"),
  ("deal",       "P0", "Closing tomorrow at 9am and the wire instructions look wrong - help!"),
  ("deal",       "P1", "Got the inspection report back. There are 3 items I'd like to negotiate."),
  ("deal",       "P1", "Counter-offer received on {addr} - they want 5k more. What do you think?"),
  ("deal",       "P2", "Appraisal came in at $620k for {addr}. Numbers look good."),
  ("document",   "P2", "Could you send me the seller's disclosure for {addr} (MLS {listing})?"),
  ("document",   "P1", "Lender needs proof of homeowners insurance by end of day."),
  ("contract",   "P1", "Need your signature on the addendum for {addr} before 5pm."),
  ("contract",   "P2", "Question on clause 7.b of the contract - what does 'as-is' really mean here?"),
]

TEMPLATES_TENANT_RENT_MGMT = [
  ("tenant_issue","P0", "EMERGENCY - water is pouring from the ceiling in unit at {addr}!!"),
  ("tenant_issue","P0", "Gas leak smell in the apartment, I called 911. {addr}"),
  ("tenant_issue","P0", "Locked out of {addr} at 11pm with the kids in the car."),
  ("tenant_issue","P0", "No heat for 2 days now, my baby is freezing. {addr}"),
  ("tenant_issue","P1", "AC stopped cooling at {addr}. Indoor temp is 84F."),
  ("tenant_issue","P2", "Neighbor in unit B has been playing loud music until 2am for a week. {addr}"),
  ("tenant_issue","P2", "Bathroom faucet is dripping non-stop, can a plumber come look?"),
  ("tenant_issue","P2", "Saw a roach in the kitchen - can pest control come this week?"),
  ("maintenance", "P2", "Vendor said they'd be here at 10am, no show. Can you check on them?"),
  ("maintenance", "P3", "Light bulb in the hallway burned out, no rush."),
  ("maintenance", "P2", "The work order #4421 for the dishwasher - any update?"),
  ("billing",     "P2", "I paid rent on the 1st but it doesn't show in the portal yet."),
  ("billing",     "P2", "Got a late fee but I have proof I paid on time. Can we sort this out?"),
  ("billing",     "P3", "Just confirming the security deposit is $1,500 not $2,000 like the email said."),
  ("contract",    "P2", "Need to renew the lease at {addr}. Can you send the renewal terms?"),
  ("contract",    "P1", "Giving 30 days' notice - moving out by end of next month. {addr}"),
  ("scheduling",  "P2", "Can we set up a walkthrough next Tuesday for the move-out at {addr}?"),
  ("vendor",      "P2", "The painter quoted $1,800 for unit B at {addr} - does that look right?"),
  ("vendor",      "P3", "Plumber invoice for {addr} - approving with your sign-off."),
  ("admin",       "P3", "HOA sent a notice about new pool hours, forwarding to you."),
  ("admin",       "P3", "Insurance renewal for {addr} is due next month."),
]

# Non-business / personal / promo / spam
TEMPLATES_NON_BUSINESS = [
  ("personal",       "P3", "Hey, want to grab dinner Friday at that new ramen place?"),
  ("personal",       "P3", "Mom's birthday is next week - did you order the cake?"),
  ("personal",       "P3", "Dentist appointment confirmed for Tuesday 2pm."),
  ("personal",       "P3", "Pickup at school is at 3:15 today, can you grab the kids?"),
  ("personal",       "P3", "FYI we're out of milk. Can you grab some on the way home?"),
  ("personal",       "P3", "Yoga class moved to 7am tomorrow. Want to come?"),
  ("personal",       "P3", "The dog has a vet appointment Thursday at 10am."),
  ("marketing_promo","P3", "FLASH SALE - 50% off everything at our store this weekend only!"),
  ("marketing_promo","P3", "Your monthly newsletter from RealEstateDigest is here."),
  ("marketing_promo","P3", "Are you a real estate agent? Try our new CRM free for 30 days."),
  ("marketing_promo","P3", "Boost your listings with our premium photography package."),
  ("marketing_promo","P3", "Atlanta Realtors Network: invitation to our May mixer."),
  ("spam",           "P3", "CLAIM YOUR PRIZE NOW - you've won $10,000! Click here: http://scam.example/"),
  ("spam",           "P3", "Urgent: your bank account needs verification. Visit https://phish.example/"),
  ("spam",           "P3", "Crypto opportunity - 300% return guaranteed. Reply YES."),
  ("spam",           "P3", "I'm a Nigerian prince and need your help moving $25M..."),
]

# Owner mapping (defaults; rules engine can override)
OWNER_BY_TOPIC = {
  "buyer_lead":"Nathalie","seller_lead":"Nathalie","showing":"Nathalie","deal":"Nathalie",
  "tenant_issue":"Nathalie","maintenance":"Noa","contract":"Noa","document":"Noa",
  "vendor":"Noa","admin":"Noa","scheduling":"Noa","billing":"Noa",
  "vip_client":"Nathalie","personal":"ARCHIVE","marketing_promo":"ARCHIVE","spam":"ARCHIVE",
}

def expected_owner(topic, county):
    base = OWNER_BY_TOPIC.get(topic, "Nathalie")
    # Geo override: outlying counties go to Fabi for lead/showing topics.
    if base == "Nathalie" and topic in {"buyer_lead","seller_lead","showing"} and county in COUNTIES_FABI:
        return "Fabi"
    return base

def render(tmpl_topic_urg_body):
    topic, urg, body = tmpl_topic_urg_body
    addr, county = fake_address()
    listing = fake_mls()
    name = fake_name()
    text = body.format(addr=addr, co=county, listing=listing, name=name)
    return topic, urg, text, addr, county, listing, name

# --------------------------------------------------------------------------
# Generators
# --------------------------------------------------------------------------
def build_sms(n=1000):
    rows = []
    pool = TEMPLATES_BUY_SELL + TEMPLATES_TENANT_RENT_MGMT + TEMPLATES_NON_BUSINESS
    for i in range(n):
        # The user wanted SMS attached to the dummy test user.
        sender_name  = TEST_USER_NAME
        sender_phone = TEST_USER_PHONE
        tmpl = random.choice(pool)
        topic, urg, text, addr, county, listing, _ = render(tmpl)
        owner = expected_owner(topic, county)
        ts = ts_within_days(60)
        rows.append({
            "msg_id": f"SMS-{i+1:04d}",
            "channel": "sms",
            "received_at": ts.isoformat()+"Z",
            "sender_name": sender_name,
            "sender_phone": sender_phone,
            "body": text[:160],   # one SMS segment
            "address_in_msg": addr,
            "county_in_msg": county,
            "listing_in_msg": listing if "{listing}" in tmpl[2] or "MLS" in text else "",
            "expected_topic": topic,
            "expected_urgency": urg,
            "expected_owner": owner,
        })
    return rows

def build_whatsapp(n=1000):
    rows = []
    pool = TEMPLATES_BUY_SELL + TEMPLATES_TENANT_RENT_MGMT + TEMPLATES_NON_BUSINESS
    for i in range(n):
        # Dummy test user's WhatsApp = same mobile number as SMS dummy user.
        sender_name  = TEST_USER_NAME
        sender_phone = TEST_USER_PHONE
        tmpl = random.choice(pool)
        topic, urg, text, addr, county, listing, _ = render(tmpl)
        # WhatsApp messages often have richer formatting & emojis - sprinkle a few
        if random.random() < 0.25:
            text += " 🙏"
        if random.random() < 0.15:
            text = "Voice note transcription:\n" + text
        # Sometimes a multi-line follow-up
        if random.random() < 0.30:
            text += "\n\nAlso - can you let me know by tomorrow?"
        owner = expected_owner(topic, county)
        ts = ts_within_days(60)
        rows.append({
            "msg_id": f"WA-{i+1:04d}",
            "channel": "whatsapp",
            "received_at": ts.isoformat()+"Z",
            "sender_name": sender_name,
            "sender_phone": sender_phone,
            "body": text,
            "address_in_msg": addr,
            "county_in_msg": county,
            "listing_in_msg": listing if "MLS" in text else "",
            "expected_topic": topic,
            "expected_urgency": urg,
            "expected_owner": owner,
        })
    return rows

def build_email(n=1000):
    rows = []
    # Bucket split: 1/3 non-business, 1/3 buy/sell, 1/3 rent/manage
    third = n // 3
    buckets = (
        [("non_business", TEMPLATES_NON_BUSINESS)] * third +
        [("buy_sell",     TEMPLATES_BUY_SELL)]     * third +
        [("rent_manage",  TEMPLATES_TENANT_RENT_MGMT)] * (n - 2*third)
    )
    random.shuffle(buckets)
    for i, (bucket, pool) in enumerate(buckets):
        tmpl = random.choice(pool)
        topic, urg, text, addr, county, listing, name = render(tmpl)
        # Senders vary across email
        if bucket == "non_business" and topic == "marketing_promo":
            sender_name = "Marketing Promo Co"
            sender_email = f"newsletter@{random.choice(PROMO_DOMAINS)}"
        elif bucket == "non_business" and topic == "spam":
            sender_name = "Definitely Not A Scam"
            sender_email = f"win{random.randint(100,999)}@spam.example"
        elif bucket == "non_business":   # personal
            sender_name = fake_name()
            sender_email = fake_email(sender_name)
        else:
            sender_name = fake_name()
            sender_email = fake_email(sender_name)
        # Subject heuristic
        subj_map = {
            "buyer_lead":"Question about your listing",
            "seller_lead":"Selling our home",
            "showing":"Showing time change",
            "deal":"Offer / deal update",
            "tenant_issue":"Urgent - apartment issue",
            "maintenance":"Maintenance follow-up",
            "contract":"Contract / signature needed",
            "document":"Document request",
            "vendor":"Vendor quote",
            "admin":"Admin / paperwork",
            "scheduling":"Calendar question",
            "billing":"Payment / billing",
            "personal":"Hey :)",
            "marketing_promo":"You've gotta see this offer",
            "spam":"YOU WON",
        }
        ts = ts_within_days(60)
        owner = expected_owner(topic, county)
        rows.append({
            "msg_id": f"EM-{i+1:04d}",
            "channel": "email",
            "received_at": ts.isoformat()+"Z",
            "sender_name": sender_name,
            "sender_email": sender_email,
            "subject": subj_map.get(topic,"(no subject)"),
            "body": text,
            "address_in_msg": addr if bucket != "non_business" else "",
            "county_in_msg": county if bucket != "non_business" else "",
            "listing_in_msg": listing if "MLS" in text else "",
            "bucket": bucket,
            "expected_topic": topic,
            "expected_urgency": urg,
            "expected_owner": owner,
        })
    return rows

def write_csv(rows, path):
    if not rows: return
    with open(path,"w",newline="",encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows: w.writerow(r)

def write_jsonl(rows, path):
    with open(path,"w",encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False)+"\n")

# Build everything
sms_rows = build_sms(1000)
wa_rows  = build_whatsapp(1000)
em_rows  = build_email(1000)

write_csv(sms_rows, os.path.join(DIR_SMS, "sms_mock_1k.csv"))
write_jsonl(sms_rows, os.path.join(DIR_SMS, "sms_mock_1k.jsonl"))

write_csv(wa_rows,  os.path.join(DIR_WA, "whatsapp_mock_1k.csv"))
write_jsonl(wa_rows, os.path.join(DIR_WA, "whatsapp_mock_1k.jsonl"))

write_csv(em_rows,  os.path.join(DIR_EM, "email_mock_1k.csv"))
write_jsonl(em_rows, os.path.join(DIR_EM, "email_mock_1k.jsonl"))

# Summary stats
def stats(rows, key):
    from collections import Counter
    c = Counter(r[key] for r in rows)
    return dict(c.most_common())

summary = {
    "test_user_name": TEST_USER_NAME,
    "test_user_phone": TEST_USER_PHONE,
    "test_user_email": TEST_USER_EMAIL,
    "sms": {
        "count": len(sms_rows),
        "by_topic": stats(sms_rows, "expected_topic"),
        "by_urgency": stats(sms_rows, "expected_urgency"),
        "by_owner": stats(sms_rows, "expected_owner"),
    },
    "whatsapp": {
        "count": len(wa_rows),
        "by_topic": stats(wa_rows, "expected_topic"),
        "by_urgency": stats(wa_rows, "expected_urgency"),
        "by_owner": stats(wa_rows, "expected_owner"),
    },
    "email": {
        "count": len(em_rows),
        "by_bucket": stats(em_rows, "bucket"),
        "by_topic": stats(em_rows, "expected_topic"),
        "by_urgency": stats(em_rows, "expected_urgency"),
        "by_owner": stats(em_rows, "expected_owner"),
    },
}
with open(os.path.join(ROOT, "mockup_summary.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)

print("Generated:")
print(f"  SMS:      {len(sms_rows)}  ->  sms/sms_mock_1k.[csv|jsonl]")
print(f"  WhatsApp: {len(wa_rows)}  ->  whatsapp/whatsapp_mock_1k.[csv|jsonl]")
print(f"  Email:    {len(em_rows)}  ->  email/email_mock_1k.[csv|jsonl]")
print(f"\nEmail bucket split: {summary['email']['by_bucket']}")
print(f"Test user: {TEST_USER_NAME} / {TEST_USER_PHONE}")
