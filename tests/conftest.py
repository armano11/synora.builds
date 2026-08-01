"""Shared fixtures: rebuild the 4 enterprise DBs + cases ledger once per session."""
import pytest

import enterprise.seed as seed


@pytest.fixture(scope="session", autouse=True)
def rebuilt_dbs():
    seed.rebuild()
