"""Shared test isolation.

Every test gets its own empty DBASIM_HOME, so results never depend on the
developer's real ~/.dbasim.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_dbasim_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DBASIM_HOME", str(tmp_path / "dbasim-home"))
