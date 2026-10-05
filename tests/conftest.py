"""Shared test isolation.

Every test gets its own empty DBASIM_HOME and no DBASIM_LANG, so results never
depend on the developer's real ~/.dbasim (where `dbasim lang` may have been set).
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_dbasim_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DBASIM_HOME", str(tmp_path / "dbasim-home"))
    monkeypatch.delenv("DBASIM_LANG", raising=False)
