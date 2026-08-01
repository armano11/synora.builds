"""Shared fixtures: rebuild the 4 enterprise DBs + cases ledger once per session."""
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import enterprise.seed as seed  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def rebuilt_dbs():
    seed.rebuild()
