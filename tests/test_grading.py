"""Grading rules and progress logic, tested without a database."""
import random

import pytest

from dbasim import state
from dbasim.cli import build_parser, _password
from dbasim.scenarios import ALL, get

FP = {"count": 1000, "checksum": 500500}


def passed(results):
    return all(r.passed for r in results)


# ---- s01 missing index ----------------------------------------------------

def s01_metrics(**kw):
    m = {"full_scan": False, "gets_per_exec": 12.0, "ms_per_exec": 1.0, "orders": dict(FP)}
    m.update(kw)
    return m


def test_s01_passes_with_index():
    assert passed(get("s01").evaluate(s01_metrics(), {}, {"orders": FP}))


def test_s01_fails_on_full_scan():
    r = get("s01").evaluate(s01_metrics(full_scan=True, gets_per_exec=17000), {}, {"orders": FP})
    assert [c.passed for c in r] == [False, False, True]


def test_s01_rejects_deleting_rows():
    gone = {"count": 10, "checksum": 55}
    r = get("s01").evaluate(s01_metrics(orders=gone), {}, {"orders": FP})
    assert not passed(r)
    assert "ห้าม" in r[-1].detail


# ---- s02 stale stats ------------------------------------------------------

def s02_metrics(**kw):
    m = {"sales": {"count": 2_001_000, "checksum": 1}, "tab_num_rows": 2_000_500,
         "tab_fresh": True, "ix_num_rows": 1_990_000, "ix_fresh": True}
    m.update(kw)
    return m


S02_BASE = {"sales": {"count": 2_001_000, "checksum": 1}}


def test_s02_passes_after_gather():
    assert passed(get("s02").evaluate(s02_metrics(), {}, S02_BASE))


def test_s02_fails_with_old_stats():
    r = get("s02").evaluate(s02_metrics(tab_num_rows=1000, tab_fresh=False), {}, S02_BASE)
    assert not r[0].passed


def test_s02_fails_with_estimate_far_off():
    r = get("s02").evaluate(s02_metrics(tab_num_rows=1_500_000), {}, S02_BASE)
    assert not r[0].passed


def test_s02_fails_when_index_stats_not_refreshed():
    r = get("s02").evaluate(s02_metrics(ix_fresh=False), {}, S02_BASE)
    assert [c.passed for c in r] == [True, False, True]


def test_s02_handles_missing_stats():
    r = get("s02").evaluate(s02_metrics(tab_num_rows=None, ix_num_rows=None), {}, S02_BASE)
    assert not r[0].passed and not r[1].passed


# ---- s03 temp full --------------------------------------------------------

def test_s03_passes_when_batch_runs():
    m = {"batch_error": None, "invoices": FP}
    assert passed(get("s03").evaluate(m, {}, {"invoices": FP}))


def test_s03_shows_oracle_error():
    err = "ORA-01652: unable to extend temp segment by 128 in tablespace TEMP_RPT"
    r = get("s03").evaluate({"batch_error": err, "invoices": FP}, {}, {"invoices": FP})
    assert not r[0].passed and "TEMP_RPT" in r[0].detail


# ---- catalog --------------------------------------------------------------

def test_scenario_catalog_is_complete():
    ids = [s.id for s in ALL]
    assert len(ids) == len(set(ids))
    for s in ALL:
        assert s.title and s.story and s.solution and s.difficulty
        assert len(s.hints) >= 3
        assert s.make_params(random.Random(1), 1.0)


def test_scale_shrinks_data():
    assert get("s01").make_params(random.Random(1), 0.2)["nrows"] == 200_000


def test_unknown_scenario():
    from dbasim.db import SetupError
    with pytest.raises(SetupError):
        get("s99")


def test_s03_never_shrinks_below_temp_size():
    assert get("s03").make_params(random.Random(1), 0.05)["nrows"] >= 200_000


def test_password_is_safe_for_create_user():
    pw = _password(random.Random(3))
    assert pw.isalnum() and pw[0].isalpha() and len(pw) == 14


def test_parser_has_all_commands():
    p = build_parser()
    for cmd in ["doctor", "list", "status", "hint", "check", "stop", "reset"]:
        assert p.parse_args([cmd]).cmd == cmd
    assert p.parse_args(["start", "s01"]).id == "s01"


# ---- progress / scoring ---------------------------------------------------

@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DBASIM_HOME", str(tmp_path))
    return tmp_path


def test_score_drops_per_hint():
    a = state.new_active("s01", {}, {})
    assert state.score(a) == 100
    a["hints_shown"] = 2
    assert state.score(a) == 70
    a["hints_shown"] = 9
    assert state.score(a) == 10
    a["solution_revealed"] = True
    assert state.score(a) == 0


def test_state_roundtrip_and_best(tmp_home):
    st = state.load()
    assert st == {"active": None, "history": []}
    a = state.new_active("s01", {"nrows": 1}, {})
    a["started_at"] -= 600
    st["active"] = a
    e = state.record_solved(st, a)
    assert e["minutes"] == pytest.approx(10, abs=0.2)
    state.save(st)
    again = state.load()
    assert state.best_scores(again) == {"s01": 100}


# ---- s04 lock contention ---------------------------------------------------

S04_BASE = {"accounts": FP}


def test_s04_passes_when_blocker_gone():
    m = {"clerks_reconnected": 0, "stuck_sessions": 0, "lock_error": None, "accounts": FP}
    assert passed(get("s04").evaluate(m, {}, S04_BASE))


def test_s04_fails_while_rows_locked():
    m = {"clerks_reconnected": 0, "stuck_sessions": 3,
         "lock_error": "ORA-30006: resource busy; acquire with WAIT timeout expired", "accounts": FP}
    r = get("s04").evaluate(m, {}, S04_BASE)
    assert [c.passed for c in r] == [False, False, True, True]


def test_s04_fails_when_clerks_were_killed():
    m = {"clerks_reconnected": 3, "stuck_sessions": 0, "lock_error": None, "accounts": FP}
    r = get("s04").evaluate(m, {}, S04_BASE)
    assert [c.passed for c in r] == [True, True, False, True]


def test_s04_hot_range_is_50_accounts():
    p = get("s04").make_params(random.Random(5), 1.0)
    assert p["hot_hi"] - p["hot_lo"] == 49 and p["hot_hi"] <= p["naccounts"]


def test_s04_needs_live_app_for_check():
    s = get("s04")
    assert s.check_needs_workload and not s.pause_workload_during_check


# ---- s05 hard parse ----------------------------------------------------------

def test_s05_passes_with_cursor_sharing():
    m = {"connect_error": None, "hard_parses": 2, "runs": 200, "products": FP}
    assert passed(get("s05").evaluate(m, {}, {"products": FP}))


def test_s05_fails_with_literals():
    m = {"hard_parses": 200, "runs": 200, "products": FP}
    r = get("s05").evaluate(m, {}, {"products": FP})
    assert not r[0].passed and "100%" in r[0].detail


def test_s05_boundary_is_ten_percent():
    ok = get("s05").evaluate({"hard_parses": 20, "runs": 200, "products": FP}, {}, {"products": FP})
    bad = get("s05").evaluate({"hard_parses": 21, "runs": 200, "products": FP}, {}, {"products": FP})
    assert ok[0].passed and not bad[0].passed


def test_s05_reports_broken_logon_trigger():
    m = {"connect_error": "ORA-04098: trigger 'SYSTEM.T' is invalid", "hard_parses": 0, "runs": 200, "products": FP}
    r = get("s05").evaluate(m, {}, {"products": FP})
    assert not r[0].passed and "dba_errors" in r[0].detail


class _OraError(Exception):
    def __init__(self, code):
        super().__init__(type("Err", (), {"code": code})())


@pytest.mark.parametrize("code", [30006, 54])  # 23ai and older / 26ai
def test_s04_clerk_treats_wait_timeout_as_give_up(code):
    import logging, random

    class Cur:
        def execute(self, *a, **k):
            raise _OraError(code)

        def close(self):
            pass

    class Conn:
        rolled_back = False

        def cursor(self):
            return Cur()

        def rollback(self):
            self.rolled_back = True

    s, conn = get("s04"), Conn()
    s.workload_step(conn, {"hot_lo": 100, "hot_hi": 149}, random.Random(0), logging.getLogger("t"), worker=1)
    assert conn.rolled_back  # gave up politely instead of crashing and reconnecting


class _FakeClock:
    """perf_counter that advances by `step` seconds on every call pair (start, end)."""
    def __init__(self, step):
        self.t, self.step, self.calls = 0.0, step, 0

    def __call__(self):
        self.calls += 1
        if self.calls % 2 == 0:
            self.t += self.step
        return self.t


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        rows = self.rows

        class Cur:
            def execute(self, *a, **k):
                pass

            def fetchall(self):
                return rows

            def close(self):
                pass
        return Cur()


@pytest.mark.parametrize("ms,slow", [(8.0, True), (0.4, False)])
def test_s01_app_log_flags_slow_search(monkeypatch, caplog, ms, slow):
    import logging
    from dbasim.scenarios import s01_missing_index as m
    monkeypatch.setattr(m.time, "perf_counter", _FakeClock(ms / 1000))
    s = m.MissingIndex()
    log = logging.getLogger("t01")
    with caplog.at_level(logging.INFO, logger="t01"):
        for _ in range(m.LOG_EVERY):
            s.workload_step(_Rows([]), {"ncust": 1000}, random.Random(0), log)
    assert len(caplog.records) == 1
    assert ("SLOW" in caplog.text) is slow


@pytest.mark.parametrize("secs,slow", [(0.28, True), (0.04, False)])  # 480k rows: wrong plan vs fixed
def test_s02_app_log_flags_slow_report(monkeypatch, caplog, secs, slow):
    import logging
    from dbasim.scenarios import s02_stale_stats as m
    monkeypatch.setattr(m.time, "perf_counter", _FakeClock(secs))
    log = logging.getLogger("t02")
    with caplog.at_level(logging.INFO, logger="t02"):
        m.StaleStats().workload_step(_Rows([("REGION-1", 480_100, 1.0)]), {}, random.Random(0), log)
    assert "480,100 rows" in caplog.text
    assert ("SLOW" in caplog.text) is slow
