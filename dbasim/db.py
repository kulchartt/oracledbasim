"""Connection helpers. Settings come from `dbasim setup` (saved in ~/.dbasim/config.json)
or from environment variables, which override the saved file:

  DBASIM_DSN             default localhost:1521/FREEPDB1
  DBASIM_ADMIN_USER      default system
  DBASIM_ADMIN_PASSWORD  the ORACLE_PWD you gave the container
  DBASIM_SCALE           row-count multiplier, default 1.0 (use 0.2 on small laptops)
"""
import os
import time


try:
    import oracledb
except ImportError:  # allows unit tests without the driver
    oracledb = None

DEFAULT_DSN = "localhost:1521/FREEPDB1"
SHOP_USER = "SHOP"
LAB_TEMP_TS = "TEMP_RPT"
FILE_TAG = "dbasim_"


class SetupError(Exception):
    pass


def config():
    from . import state
    saved = state.load_config()
    return {
        "dsn": os.environ.get("DBASIM_DSN") or saved.get("dsn") or DEFAULT_DSN,
        "admin_user": os.environ.get("DBASIM_ADMIN_USER") or saved.get("admin_user") or "system",
        "admin_password": os.environ.get("DBASIM_ADMIN_PASSWORD") or saved.get("admin_password"),
    }


def scale():
    from . import state
    raw = os.environ.get("DBASIM_SCALE") or state.load_config().get("scale") or 1.0
    try:
        return max(0.05, float(raw))
    except (TypeError, ValueError):
        return 1.0


def admin_connect():
    c = config()
    if not c["admin_password"]:
        raise SetupError("No database password saved yet. Run: dbasim setup   (asks once for the SYSTEM password you gave the container)")
    return oracledb.connect(user=c["admin_user"], password=c["admin_password"], dsn=c["dsn"])


def connect(user, password):
    return oracledb.connect(user=user, password=password, dsn=config()["dsn"])


def ora_code(exc):
    """Return the ORA- number of a DatabaseError, or None."""
    if not exc.args:
        return None
    return getattr(exc.args[0], "code", None)


def error_kind(exc):
    """Classify a driver exception: "auth" (wrong password), "connect" (no database
    answering at the DSN), or None for everything else."""
    err = exc.args[0] if exc.args else None
    code = getattr(err, "code", None)
    full = getattr(err, "full_code", "") or ""
    if code == 1017:
        return "auth"
    if full.startswith("DPY-60") or (code and 12150 <= code <= 12699):
        return "connect"
    return None


def first_line(exc):
    return str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)


def run(conn, sql, params=None, ignore=()):
    """Execute one statement; swallow the ORA codes listed in `ignore`."""
    cur = conn.cursor()
    try:
        cur.execute(sql, params or {})
    except Exception as exc:  # oracledb.DatabaseError
        if ora_code(exc) in ignore:
            return
        raise
    finally:
        cur.close()


def scalar(conn, sql, params=None):
    cur = conn.cursor()
    try:
        cur.execute(sql, params or {})
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()


def rows(conn, sql, params=None):
    cur = conn.cursor()
    try:
        cur.execute(sql, params or {})
        return cur.fetchall()
    finally:
        cur.close()


# ---- safety: dbasim drops objects, so it must never touch a real database --

def assert_safe_target(admin):
    banner = scalar(admin, "select banner from v$version where rownum = 1") or ""
    if "Free" not in banner and os.environ.get("DBASIM_ALLOW_NON_FREE") != "1":
        raise SetupError("This database is not Oracle Database Free:\n  " + banner + "\n"
            "dbasim creates and drops objects, so it only runs against Oracle Free on your own computer. "
            "Never point it at a database at work.")
    con = scalar(admin, "select sys_context('USERENV','CON_NAME') from dual")
    if con == "CDB$ROOT":
        raise SetupError("Connected to CDB$ROOT. Point DBASIM_DSN at a PDB, e.g. localhost:1521/FREEPDB1")
    return banner, con


# ---- shared building blocks used by every scenario ----------------------

ORA_USER_NOT_EXIST = 1918
ORA_TS_NOT_EXIST = 959


def drop_shop_user(admin):
    exists = scalar(admin, "select count(*) from dba_users where username = :u", {"u": SHOP_USER})
    if not exists:
        return
    marker = scalar(
        admin,
        "select count(*) from dba_tables where owner = :u and table_name = 'DBASIM_MARKER'",
        {"u": SHOP_USER},
    )
    if not marker:
        raise SetupError(f"User {SHOP_USER} already exists but was not created by dbasim, so it will not be dropped. "
            "If you are sure it is unused, drop it yourself: DROP USER SHOP CASCADE")
    # logon/schema triggers other users created ON the SHOP schema (s05 invites one)
    for owner, name in rows(
        admin,
        "select owner, trigger_name from dba_triggers"
        " where table_owner = :u and base_object_type like 'SCHEMA%' and owner <> :u",
        {"u": SHOP_USER},
    ):
        run(admin, f'drop trigger "{owner}"."{name}"', ignore=(4080,))
    # kill leftover SHOP sessions first; a killed session can linger for a moment,
    # so retry the drop on ORA-01940 for up to ~30 seconds
    for sid, serial in rows(
        admin, "select sid, serial# from v$session where username = :u", {"u": SHOP_USER}
    ):
        run(admin, f"alter system kill session '{int(sid)},{int(serial)}' immediate",
            ignore=(30, 31))
    for attempt in range(15):
        try:
            run(admin, f"drop user {SHOP_USER} cascade", ignore=(ORA_USER_NOT_EXIST,))
            return
        except Exception as exc:
            if ora_code(exc) != ORA_USER_CONNECTED or attempt == 14:
                raise
            time.sleep(2)


ORA_USER_CONNECTED = 1940


def drop_lab_temp_tablespace(admin):
    exists = scalar(
        admin, "select count(*) from dba_tablespaces where tablespace_name = :t", {"t": LAB_TEMP_TS}
    )
    if not exists:
        return
    files = [f[0] for f in rows(
        admin, "select file_name from dba_temp_files where tablespace_name = :t", {"t": LAB_TEMP_TS}
    )]
    # ours if empty or at least one file carries our tag (players may add their own files)
    if files and not any(FILE_TAG in f for f in files):
        raise SetupError(f"Tablespace {LAB_TEMP_TS} was not created by dbasim, so it will not be dropped")
    run(admin, f"drop tablespace {LAB_TEMP_TS} including contents and datafiles",
        ignore=(ORA_TS_NOT_EXIST,))


def reset_lab_settings(admin):
    """Undo instance-level fixes players may leave behind, so a replay starts broken again.

    s04: max_idle_blocker_time / DEFAULT profile IDLE_TIME would auto-kill the blocker.
    s05: cursor_sharing (memory and spfile) and logon triggers that set it.
    """
    run(admin, "alter system set cursor_sharing = EXACT scope = both", ignore=(2065, 2095, 32001))
    run(admin, "alter system reset max_idle_blocker_time scope = both", ignore=(2065, 32010, 32009))
    run(admin, "alter profile default limit idle_time unlimited", ignore=(2380,))
    for owner, name in rows(
        admin,
        "select distinct s.owner, s.name from dba_source s join dba_triggers t"
        " on t.owner = s.owner and t.trigger_name = s.name"
        # SYSTEM counts as Oracle-maintained, yet it is where players create triggers
        " where s.type = 'TRIGGER' and s.owner <> 'SYS'"
        " and t.triggering_event like 'LOGON%' and upper(s.text) like '%CURSOR_SHARING%'",
    ):
        run(admin, f'drop trigger "{owner}"."{name}"', ignore=(4080,))


def create_shop_user(admin, password, temp_ts="TEMP"):
    if not password.isalnum():
        raise ValueError("password must be alphanumeric")
    run(
        admin,
        f'create user {SHOP_USER} identified by "{password}" '
        f"default tablespace users temporary tablespace {temp_ts} quota unlimited on users",
    )
    run(admin, f"grant create session, create table, create view, alter session to {SHOP_USER}")
    run(admin, f"create table {SHOP_USER}.dbasim_marker (created_by varchar2(30))")
    run(admin, f"insert into {SHOP_USER}.dbasim_marker values ('dbasim')")
    admin.commit()


def row_generator(n_param="n"):
    """SQL fragment yielding up to 10,000,000 numbered rows as column `n`."""
    return (
        "(select rownum n from "
        "(select level l from dual connect by level <= 1000) a, "
        "(select level l from dual connect by level <= 10000) b "
        f"where rownum <= :{n_param})"
    )


def gather(admin, table):
    run(admin, f"begin dbms_stats.gather_table_stats('{SHOP_USER}', '{table}'); end;")


def db_now(admin):
    return scalar(admin, "select to_char(sysdate, 'YYYY-MM-DD HH24:MI:SS') from dual")


def table_fingerprint(admin, table, key):
    """Row count + key checksum, used to catch 'fixes' that delete data."""
    cnt, total = rows(admin, f"select count(*), nvl(sum({key}), 0) from {table}")[0]
    return {"count": int(cnt), "checksum": int(total)}
