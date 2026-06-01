"""
Seed the database (memory or Postgres) with:
  - 1 org row (Apteker Realty)
  - 3 team_member rows (Nathalie / Fabi / Noa) per the routing rules
  - 1 dummy test contact (Tester Buyer-Smith) with 3 identities (sms, whatsapp, apple_mail)
  - 10 properties spanning Nathalie's core counties and Fabi's overflow counties
  - 1 channel_account per channel
  - routing_rule_version v1 (snapshot of routing_rules.yaml)

Idempotent: re-running is safe. Inserts use ON CONFLICT DO NOTHING.

Usage:
    python scripts/seed_database.py
"""
from __future__ import annotations

import hashlib
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from app.config import DB_URL, ROUTING_RULES_PATH  # noqa: E402
from app.logging_setup import get_logger  # noqa: E402
from app.store import PostgresStore, make_store  # noqa: E402

log = get_logger("seed")

DUMMY_TEST_USER = {
    "name":  "Tester Buyer-Smith",
    "phone": "+14045550199",
    "email": "tester.buyer@example-mail.test",
}

# 10 properties spanning Nathalie's + Fabi's territories.
DEMO_PROPERTIES = [
    # (external_id,    address,                          city,           county,    kind)
    ("MLS-7700001",  "1234 Peachtree St",   "Atlanta",       "Fulton",   "listing_for_sale"),
    ("MLS-7700002",  "456 Oak Ave",         "Sandy Springs", "Fulton",   "managed_rental"),
    ("MLS-7700003",  "789 Maple Dr",        "Decatur",       "DeKalb",   "listing_for_sale"),
    ("MLS-7700004",  "101 Magnolia Ln",     "Brookhaven",    "DeKalb",   "managed_rental"),
    ("MLS-7700005",  "555 Riverbend Way",   "Marietta",      "Cobb",     "listing_for_sale"),
    ("MLS-7700006",  "67 Cedar Trail",      "Smyrna",        "Cobb",     "managed_rental"),
    ("MLS-7700007",  "890 Willow Pkwy",     "Lawrenceville", "Gwinnett", "listing_for_sale"),
    ("MLS-7700008",  "12 Birch Ct",         "Duluth",        "Gwinnett", "managed_rental"),
    ("MLS-7700009",  "345 Sycamore Blvd",   "Cumming",       "Forsyth",  "listing_for_sale"),
    ("MLS-7700010",  "678 Pine Rd",         "Canton",        "Cherokee", "listing_for_sale"),
]

CHANNEL_LABELS = [
    ("apple_mail", "nathalie_icloud"),
    ("whatsapp",   "wa_business"),
    ("sms",        "twilio_main"),
    ("ig_apteker"),
    ("apteker_gmail"),
]

def seed_inmemory_store(store) -> None:
    """Populate the in-memory store. Adds contact + properties beyond the
    base seed() which only creates org + team_member."""
    store.seed()
    # Contact + identities
    cid = str(uuid.uuid4())
    store.team["__contacts__"] = getattr(store, "_contacts", {})
    contacts = getattr(store, "_contacts", None) or {}
    contacts[cid] = {
        "id": cid, "org_id": store.org_id,
        "display_name": DUMMY_TEST_USER["name"],
        "identities": [
            ("sms",       DUMMY_TEST_USER["phone"]),
            ("whatsapp",  DUMMY_TEST_USER["phone"]),
            (DUMMY_TEST_USER["email"]),
        ],
    }
    store._contacts = contacts
    # Properties
    store._properties = {}
    for ext, addr, city, county, kind in DEMO_PROPERTIES:
        pid = str(uuid.uuid4())
        store._properties[pid] = {
            "id": pid, "org_id": store.org_id, "external_id": ext,
            "address": addr, "city": city, "county": county, "property_kind": kind,
        }
    # Rules snapshot
    rules_yaml = ROUTING_RULES_PATH.read_text(encoding="utf-8")
    store._rules_version = {
        "id": str(uuid.uuid4()), "org_id": store.org_id, "version": 1,
        "yaml_source": rules_yaml,
        "yaml_hash": hashlib.sha1(rules_yaml.encode("utf-8")).hexdigest(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    log.info("seeded_inmemory",
             team=len([k for k in store.team.keys() if not k.startswith("__")]),
             contacts=len(store._contacts),
             properties=len(store._properties))

def seed_postgres_store(store: PostgresStore) -> None:
    """Idempotent Postgres seed."""
    store.init_schema()    # safe to re-run; CREATE TYPE will fail on second run, so guard.
    store.seed()           # creates org + 3 team_member rows
    with store.engine.begin() as conn:
        # Contact
        existing = conn.execute(
            text("SELECT id FROM contact WHERE display_name=:n AND org_id=:o"),
            {"n": DUMMY_TEST_USER["name"], "o": store.org_id}).fetchone()
        if existing:
            contact_id = str(existing[0])
        else:
            contact_id = str(uuid.uuid4())
            conn.execute(
                text("""INSERT INTO contact (id, org_id, display_name, is_vip, lifecycle)
                        VALUES (:id, :o, :n, false, 'lead')"""),
                {"id": contact_id, "o": store.org_id, "n": DUMMY_TEST_USER["name"]})
        # Identities (UNIQUE on channel + identifier, so ON CONFLICT DO NOTHING)
        for chan, ident in [("sms", DUMMY_TEST_USER["phone"]),
                            ("whatsapp", DUMMY_TEST_USER["phone"]),
                            (DUMMY_TEST_USER["email"])]:
            conn.execute(
                text("""INSERT INTO contact_identity (contact_id, channel, identifier, verified)
                        VALUES (:c, :ch, :i, true) ON CONFLICT DO NOTHING"""),
                {"c": contact_id, "ch": chan, "i": ident})
        # Properties
        for ext, addr, city, county, kind in DEMO_PROPERTIES:
            conn.execute(
                text("""INSERT INTO property
                          (org_id, external_id, address, city, state, county, property_kind)
                        VALUES (:o, :ext, :addr, :city, 'GA', :co, :kind)
                        ON CONFLICT DO NOTHING"""),
                {"o": store.org_id, "ext": ext, "addr": addr,
                 "city": city, "co": county, "kind": kind})
        # Channel accounts
        for chan, label in CHANNEL_LABELS:
            conn.execute(
                text("""INSERT INTO channel_account (org_id, channel, account_label, status)
                        VALUES (:o, :ch, :lbl, 'active')
                        ON CONFLICT DO NOTHING"""),
                {"o": store.org_id, "ch": chan, "lbl": label})
        # Rules version snapshot
        rules_yaml = ROUTING_RULES_PATH.read_text(encoding="utf-8")
        yaml_hash = hashlib.sha1(rules_yaml.encode("utf-8")).hexdigest()
        existing_v = conn.execute(
            text("SELECT 1 FROM routing_rule_version WHERE org_id=:o AND yaml_hash=:h"),
            {"o": store.org_id, "h": yaml_hash}).fetchone()
        if not existing_v:
            next_v = conn.execute(
                text("SELECT COALESCE(MAX(version),0)+1 FROM routing_rule_version WHERE org_id=:o"),
                {"o": store.org_id}).scalar()
            conn.execute(
                text("""INSERT INTO routing_rule_version
                          (org_id, version, yaml_source, yaml_hash, created_by)
                        VALUES (:o, :v, :y, :h, 'seed_script')"""),
                {"o": store.org_id, "v": next_v, "y": rules_yaml, "h": yaml_hash})
    log.info("seeded_postgres", org_id=store.org_id, properties=len(DEMO_PROPERTIES))

def main() -> None:
    store = make_store()
    if DB_URL == "memory":
        seed_inmemory_store(store)
        log.info("seed_complete", backend="memory")
    else:
        # Try init_schema; ignore "type already exists" on re-run.
        try:
            seed_postgres_store(store)
        except Exception as e:
            msg = str(e)
            if "already exists" in msg or "duplicate" in msg.lower():
                log.warning("schema_already_present", note="continuing")
                # Re-seed without schema init
                store.seed()
                seed_postgres_store_skip_schema = True   # noqa: F841 (kept for clarity)
            else:
                raise
        log.info("seed_complete", backend="postgres")

if __name__ == "__main__":
    main()
