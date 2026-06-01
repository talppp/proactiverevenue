"""Shared pytest fixtures."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the repo root importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Force in-memory store and stub LLM for every test - no external deps.
os.environ.setdefault("DB_URL", "memory")
os.environ.setdefault("ANTHROPIC_API_KEY", "")

import pytest

from app.pipeline import Pipeline
from app.store import make_store
from scripts.seed_database import seed_inmemory_store


@pytest.fixture
def store():
    s = make_store()
    seed_inmemory_store(s)
    return s


@pytest.fixture
def pipeline(store):
    return Pipeline(store)
