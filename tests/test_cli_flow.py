"""End-to-end CLI flow against a fake Oracle connection.

The fake answers just enough of the dictionary queries for the safety checks and
records every SQL statement, so we can assert what dbasim would run.
"""
import pytest

from dbasim import cli, db, state


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._row = None
        self._rows = []

    def execute(self, sql, params=None, **kw):
        self.conn.log.append(" ".join(sql.split()))
        res = self.conn.answer(sql)
        self._rows = res if isinstance(res, list) else [(res,)]
        self._row = self._rows[0] if self._rows else None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, banner="Oracle Database 23ai Free Release 23.0.0.0.0", shop_exists=0, marker=0):
        self.log = []
        self.banner = banner
        self.shop_exists = shop_exists
        self.marker = marker
        self.module = None

    def answer(self, sql):
        s = " ".join(sql.split()).lower()
        if "from v$version" in s:
            return self.banner
        if "con_name" in s:
            return "FREEPDB1"
        if "from dba_users" in s:
            return self.shop_exists
        if "dbasim_marker" in s and "dba_tables" in s:
            return self.marker
        if "from dba_triggers" in s or "from dba_source" in s:
            return []
        if "from v$session" in s or "from dba_temp_files where tablespace_name = :t" in s:
            return []
        if "from dba_temp_files" in s:
            return "/opt/oracle/oradata/FREE/FREEPDB1/temp01.dbf"
        if "count(*), nvl(sum(" in s:
            return [(1000, 500500)]
        if "to_char(sysdate" in s:
            return "2026-10-04 12:00:00"
        return 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def close(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DBASIM_HOME", str(tmp_path))
    monkeypatch.setenv("DBASIM_ADMIN_PASSWORD", "x")
    monkeypatch.delenv("DBASIM_LANG", raising=False)
    state.save({**state.load(), "lang": "th"})  # these tests assert on the Thai copy; English is the default
    monkeypatch.setattr(cli, "_launch_workload", lambda: 4242)
    return tmp_path


@pytest.fixture
def pro(monkeypatch):
    """An active dbasim Pro licence, without talking to Lemon Squeezy."""
    from dbasim import license
    monkeypatch.setattr(license, "require_pro", lambda: {"key": "TEST-KEY-0000", "status": "active"})


def use(monkeypatch, conn):
    monkeypatch.setattr(db, "admin_connect", lambda: conn)
    return conn


def test_start_s03_runs_setup_and_records_state(env, monkeypatch, capsys):
    conn = use(monkeypatch, FakeConn())
    assert cli.main(["start", "s03"]) == 0
    sql = "\n".join(conn.log)
    assert "create temporary tablespace TEMP_RPT tempfile '/opt/oracle/oradata/FREE/FREEPDB1/dbasim_temp_rpt01.dbf' size 8M" in sql
    assert "temporary tablespace TEMP_RPT" in sql
    st = state.load()
    assert st["active"]["scenario"] == "s03"
    assert st["active"]["workload_pid"] == 4242
    assert st["active"]["baseline"]["invoices"] == {"count": 1000, "checksum": 500500}
    assert "NIGHTLY_BATCH" in capsys.readouterr().out


def test_refuses_non_free_database(env, monkeypatch, capsys):
    use(monkeypatch, FakeConn(banner="Oracle Database 19c Enterprise Edition Release 19.0.0.0.0"))
    assert cli.main(["start", "s01"]) == 1
    assert "ไม่ใช่ Oracle Database Free" in capsys.readouterr().out


def test_refuses_to_drop_foreign_shop_user(env, monkeypatch, capsys):
    conn = use(monkeypatch, FakeConn(shop_exists=1, marker=0))
    assert cli.main(["start", "s01"]) == 1
    assert not any(s.startswith("drop user") for s in conn.log)
    assert "ไม่ได้สร้างโดย dbasim" in capsys.readouterr().out


def test_drops_own_shop_user(env, monkeypatch):
    conn = use(monkeypatch, FakeConn(shop_exists=1, marker=1))
    assert cli.main(["start", "s01"]) == 0
    assert "drop user SHOP cascade" in conn.log


def test_second_start_needs_force(env, monkeypatch, capsys):
    use(monkeypatch, FakeConn())
    assert cli.main(["start", "s01"]) == 0
    assert cli.main(["start", "s02"]) == 1
    assert cli.main(["start", "s02", "--force"]) == 0
    assert state.load()["active"]["scenario"] == "s02"


def test_hints_then_check_pass(env, monkeypatch, capsys):
    use(monkeypatch, FakeConn())
    cli.main(["start", "s01"])
    cli.main(["hint"])
    out = capsys.readouterr().out
    assert "คำใบ้ 1/3" in out

    from dbasim.scenarios import get
    from dbasim.scenario import Criterion
    monkeypatch.setattr(type(get("s01")), "check", lambda self, *a: [Criterion("ok", True)])
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "คะแนน 85" in out
    st = state.load()
    assert st["history"][0]["score"] == 85
    assert st["active"]["workload_pid"] is None


def test_check_failure_keeps_playing(env, monkeypatch, capsys):
    use(monkeypatch, FakeConn())
    cli.main(["start", "s01"])
    from dbasim.scenarios import get
    from dbasim.scenario import Criterion
    monkeypatch.setattr(type(get("s01")), "check", lambda self, *a: [Criterion("plan", False, "full scan")])
    assert cli.main(["check"]) == 2
    assert state.load()["history"] == []


def test_list_shows_scenarios(env, capsys):
    cli.main(["list"])
    out = capsys.readouterr().out
    assert "s01" in out and "s02" in out and "s03" in out


class TempTsConn(FakeConn):
    def __init__(self, files):
        super().__init__()
        self.files = files

    def answer(self, sql):
        s = " ".join(sql.split()).lower()
        if "from dba_tablespaces where tablespace_name" in s:
            return 1
        if "from dba_temp_files where tablespace_name = :t" in s:
            return [(f,) for f in self.files]
        return super().answer(sql)


def test_teardown_accepts_player_added_tempfile():
    conn = TempTsConn(["/o/dbasim_temp_rpt01.dbf", "/o/temp_rpt02.dbf"])
    db.drop_lab_temp_tablespace(conn)
    assert "drop tablespace TEMP_RPT including contents and datafiles" in conn.log


def test_teardown_refuses_foreign_temp_tablespace():
    conn = TempTsConn(["/o/temp_rpt01.dbf"])
    with pytest.raises(db.SetupError):
        db.drop_lab_temp_tablespace(conn)
    assert not any(s.startswith("drop tablespace") for s in conn.log)


def test_s04_check_refuses_when_app_stopped(env, pro, monkeypatch, capsys):
    use(monkeypatch, FakeConn())
    monkeypatch.setattr(cli, "_alive", lambda pid: False)
    cli.main(["start", "s04"])
    capsys.readouterr()
    assert cli.main(["check"]) == 1
    assert "แอปจำลองไม่ได้ทำงาน" in capsys.readouterr().out


def test_s04_check_keeps_app_running(env, pro, monkeypatch):
    use(monkeypatch, FakeConn())
    monkeypatch.setattr(cli, "_alive", lambda pid: True)
    stopped = []
    monkeypatch.setattr(cli, "_stop_workload", lambda a: stopped.append(a and a.get("workload_pid")))
    cli.main(["start", "s04"])
    stopped.clear()
    from dbasim.scenarios import get
    from dbasim.scenario import Criterion
    monkeypatch.setattr(type(get("s04")), "check", lambda self, *a: [Criterion("x", False)])
    assert cli.main(["check"]) == 2
    assert stopped == []  # the blocker must stay alive while grading


def test_reset_drops_foreign_schema_triggers():
    class C(FakeConn):
        def answer(self, sql):
            if "from dba_triggers" in sql:
                return [("SYSTEM", "SHOP_CURSOR_SHARING")]
            return super().answer(sql)
    conn = C(shop_exists=1, marker=1)
    db.drop_shop_user(conn)
    assert 'drop trigger "SYSTEM"."SHOP_CURSOR_SHARING"' in conn.log


def test_reset_lab_settings_puts_instance_back():
    conn = FakeConn()
    db.reset_lab_settings(conn)
    log = "\n".join(conn.log)
    assert "alter system set cursor_sharing = EXACT scope = both" in log
    assert "alter system reset max_idle_blocker_time scope = both" in log
    assert "alter profile default limit idle_time unlimited" in log


def test_selftest_reports_ok_and_fail(env, monkeypatch, capsys):
    use(monkeypatch, FakeConn())
    from dbasim.scenarios import get
    from dbasim.scenario import Criterion
    calls = {"n": 0}

    def fake_check(self, *a):
        calls["n"] += 1
        return [Criterion("x", calls["n"] % 2 == 0)]  # fail, then pass

    for sid in ("s01", "s02"):
        monkeypatch.setattr(type(get(sid)), "check", fake_check)
        monkeypatch.setattr(type(get(sid)), "reference_fix", lambda self, *a: None)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    assert cli.main(["selftest", "s01", "s02", "--warmup", "0"]) == 0
    out = capsys.readouterr().out
    assert "s01  OK" in out and "s02  OK" in out

    monkeypatch.setattr(type(get("s01")), "check", lambda self, *a: [Criterion("x", True)])
    assert cli.main(["selftest", "s01", "--warmup", "0"]) == 2
    assert "problem did not reproduce" in capsys.readouterr().out
