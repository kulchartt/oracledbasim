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
    assert "is not Oracle Database Free" in capsys.readouterr().out


def test_refuses_to_drop_foreign_shop_user(env, monkeypatch, capsys):
    conn = use(monkeypatch, FakeConn(shop_exists=1, marker=0))
    assert cli.main(["start", "s01"]) == 1
    assert not any(s.startswith("drop user") for s in conn.log)
    assert "was not created by dbasim" in capsys.readouterr().out


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
    assert "Hint 1/3" in out

    from dbasim.scenarios import get
    from dbasim.scenario import Criterion
    monkeypatch.setattr(type(get("s01")), "check", lambda self, *a: [Criterion("ok", True)])
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "Score 85" in out
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
    assert "The simulated app is not running" in capsys.readouterr().out


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


# ---- connection diagnosis ---------------------------------------------------

class _DriverError(Exception):
    """Shaped like oracledb.DatabaseError: args[0] carries .code and .full_code."""
    def __init__(self, code, full_code, message):
        err = type("E", (), {"code": code, "full_code": full_code})()
        super().__init__(err)
        self.message = message

    def __str__(self):
        return self.message


def _unreachable(*a, **k):
    raise _DriverError(0, "DPY-6005", "DPY-6005: cannot connect to database [WinError 10061] refused")


def test_doctor_explains_when_docker_engine_is_down(env, monkeypatch, capsys):
    monkeypatch.setattr(db, "admin_connect", _unreachable)
    monkeypatch.setattr("shutil.which", lambda name: "C:/docker.exe")
    monkeypatch.setattr(cli, "_docker", lambda *args, **kw: (False, "error during connect"))
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "Could not connect to Oracle" in out and "engine is not running" in out
    assert "Reset to factory defaults" in out and "Traceback" not in out


def test_doctor_explains_when_container_is_stopped(env, monkeypatch, capsys):
    monkeypatch.setattr(db, "admin_connect", _unreachable)
    monkeypatch.setattr("shutil.which", lambda name: "C:/docker.exe")
    answers = {"info": (True, ""), "ps": (True, "Exited (255) 2 hours ago")}
    monkeypatch.setattr(cli, "_docker", lambda *args, **kw: answers[args[0]])
    assert cli.main(["doctor"]) == 1
    assert "docker start dbasim-oracle" in capsys.readouterr().out


def test_doctor_explains_when_database_is_still_starting(env, monkeypatch, capsys):
    monkeypatch.setattr(db, "admin_connect", _unreachable)
    monkeypatch.setattr("shutil.which", lambda name: "C:/docker.exe")
    answers = {"info": (True, ""), "ps": (True, "Up 10 seconds"), "logs": (True, "Starting Oracle Net Listener.")}
    monkeypatch.setattr(cli, "_docker", lambda *args, **kw: answers[args[0]])
    assert cli.main(["doctor"]) == 1
    assert "still starting" in capsys.readouterr().out


def test_doctor_explains_missing_docker(env, monkeypatch, capsys):
    monkeypatch.setattr(db, "admin_connect", _unreachable)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert cli.main(["doctor"]) == 1
    assert "Docker is not installed" in capsys.readouterr().out


def test_wrong_password_is_explained(env, monkeypatch, capsys):
    def bad_password(*a, **k):
        raise _DriverError(1017, "ORA-01017", "ORA-01017: invalid credential")
    monkeypatch.setattr(db, "admin_connect", bad_password)
    assert cli.main(["start", "s01"]) == 1
    out = capsys.readouterr().out
    assert "rejected the SYSTEM password" in out and "Traceback" not in out


def test_remote_dsn_gets_a_plain_message(env, monkeypatch, capsys):
    monkeypatch.setenv("DBASIM_DSN", "dbhost.example:1521/FREEPDB1")
    monkeypatch.setattr(db, "admin_connect", _unreachable)
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "Check DBASIM_DSN" in out and "docker" not in out.lower()


def test_stop_prints_shutdown_tip(env, capsys):
    assert cli.main(["stop"]) == 0
    out = capsys.readouterr().out
    assert "docker stop dbasim-oracle" in out and "Quit Docker Desktop" in out


# ---- setup / saved settings ------------------------------------------------

def test_setup_saves_password_and_scale(env, monkeypatch, capsys):
    monkeypatch.delenv("DBASIM_ADMIN_PASSWORD", raising=False)
    checks = []
    monkeypatch.setattr(cli, "cmd_doctor", lambda args: checks.append(1) or 0)
    assert cli.main(["setup", "--password", "Secret123", "--scale", "0.3"]) == 0
    assert state.load_config() == {"admin_password": "Secret123", "scale": 0.3}
    assert db.config()["admin_password"] == "Secret123" and db.scale() == 0.3
    assert "Saved to" in capsys.readouterr().out and checks == [1]


def test_environment_overrides_saved_settings(env, monkeypatch):
    monkeypatch.delenv("DBASIM_ADMIN_PASSWORD", raising=False)
    state.save_config({"admin_password": "saved", "dsn": "saved:1521/X", "scale": 0.5})
    assert db.config()["admin_password"] == "saved" and db.config()["dsn"] == "saved:1521/X"
    monkeypatch.setenv("DBASIM_ADMIN_PASSWORD", "env")
    monkeypatch.setenv("DBASIM_DSN", "env:1521/Y")
    monkeypatch.setenv("DBASIM_SCALE", "0.2")
    assert db.config()["admin_password"] == "env" and db.config()["dsn"] == "env:1521/Y"
    assert db.scale() == 0.2


def test_missing_password_points_to_setup(env, monkeypatch, capsys):
    monkeypatch.delenv("DBASIM_ADMIN_PASSWORD", raising=False)
    assert cli.main(["doctor"]) == 1
    assert "dbasim setup" in capsys.readouterr().out


def test_setup_without_password_and_without_tty_fails_clearly(env, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False})())
    assert cli.main(["setup"]) == 1
    assert "--password" in capsys.readouterr().out


def test_python_dash_m_entry_point():
    import subprocess, sys
    r = subprocess.run([sys.executable, "-m", "dbasim", "--version"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip().count(".") == 2


def test_start_stamps_the_incident_time_and_ticket_command_replays_it(env, monkeypatch, capsys):
    use(monkeypatch, FakeConn())
    assert cli.main(["start", "s01"]) == 0
    out = capsys.readouterr().out
    active = state.load()["active"]
    assert active["params"]["incident_hm"] and active["ticket"] in out
    assert "{incident_hm}" not in active["ticket"]
    assert cli.main(["ticket"]) == 0
    assert capsys.readouterr().out.strip() == active["ticket"].strip()
