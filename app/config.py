"""
Central configuration. Reads env vars (loaded from .env in dev) so the same
code runs locally, in CI, and on Render without changes.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- Storage -------------------------------------------------------------
# "memory" runs entirely in-process (demo, unit tests). Anything starting
# with "postgresql://" hits a real Postgres (CI, Render, local docker).
DB_URL: str = os.environ.get("DB_URL", "memory")

# --- LLM ----------------------------------------------------------------
# When set, the urgency/topic classifiers fall back to Claude for low-
# confidence rule-based hits. Cost ceiling enforced per request.
ANTHROPIC_API_KEY: str | None = os.environ.get("ANTHROPIC_API_KEY")
LLM_ENABLED: bool = bool(ANTHROPIC_API_KEY)
LLM_MODEL: str = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
LLM_MAX_INPUT_TOKENS: int = int(os.environ.get("LLM_MAX_INPUT_TOKENS", "4000"))
LLM_MAX_OUTPUT_TOKENS: int = int(os.environ.get("LLM_MAX_OUTPUT_TOKENS", "400"))
LLM_DAILY_USD_CEILING: float = float(os.environ.get("LLM_DAILY_USD_CEILING", "10.0"))

# --- Routing -----------------------------------------------------------
ROUTING_RULES_PATH = PROJECT_ROOT / "04_Rules_and_Routing" / "routing_rules.yaml"
TAXONOMY_PATH      = PROJECT_ROOT / "06_Taxonomy" / "taxonomy.json"
SCHEMA_SQL_PATH    = PROJECT_ROOT / "08_Review_Schema_Plan" / "02_schema.sql"

CONFIDENCE_FLOOR: float = float(os.environ.get("CONFIDENCE_FLOOR", "0.55"))

# --- Org / team seeding (CI + demo) -----------------------------------
ORG_NAME = "Apteker Realty"
ORG_TIMEZONE = "America/New_York"
TEAM_SEED = [
    # display_name, role,         counties,                                       after_hours_ok, max_open
    ("Nathalie",   "owner",       ["Fulton", "DeKalb"],                            True,           99),
    ("Fabi",       "agent",       ["Cobb", "Gwinnett", "Forsyth", "Cherokee"],     False,          99),
    ("Noa",        "back_office", [],                                              False,          99),
]
