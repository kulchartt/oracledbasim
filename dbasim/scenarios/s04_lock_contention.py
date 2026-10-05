import time

from .. import db
from ..scenario import Criterion, Scenario, integrity_criterion

PAY_LOCK = "select balance from shop.accounts where account_id = :a for update wait 10"
PAY_UPDATE = "update shop.accounts set balance = balance + :amt, updated_at = sysdate where account_id = :a"
PAY_INSERT = (
    "insert into shop.payments (payment_id, account_id, amount, paid_at) "
    "values (shop.payments_seq.nextval, :a, :amt, sysdate)"
)
# FOR UPDATE WAIT n timing out: ORA-30006 up to 23ai, ORA-00054 from 26ai on
ORA_WAIT_TIMEOUT = (30006, 54)
STUCK_MICROS = 5_000_000


class LockContention(Scenario):
    id = "s04"
    title = "Payment entry screen hangs"
    difficulty = "Medium"
    premium = True
    story = ("Ticket #5390 from Finance (urgent):\n"
            "\"When staff save a payment (PAYMENT_ENTRY) it just spins and finally errors out. "
            "Customers who already paid still show as outstanding. It's been like this since this morning.\"\n\n"
            "Your job: get payment entry working normally again without deleting any data, "
            "and be careful not to touch the sessions of staff who aren't the cause.")
    hints = [
        "ORA-30006 or ORA-00054 means the app waited for a lock longer than it allows. "
            "The question is who holds the lock on those rows.",
        "In v$session, look at the event and blocking_session columns of the sessions waiting on "
            "'enq: TX - row lock contention'",
        "The culprit session isn't doing anything (INACTIVE) but has an open transaction. Check module "
            "and last_call_et, then deal with it using alter system kill session 'sid,serial#'",
    ]
    solution = ("Cause: the ADJUST_BATCH job updated 50 accounts and never committed, leaving the transaction open. "
            "Those rows stay locked, so anyone saving a payment to those accounts waits until they time out.\n\n"
            "Finding the culprit:\n"
            "  select sid, serial#, username, module, status, last_call_et, blocking_session, event\n"
            "  from v$session where username = 'SHOP' order by blocking_session nulls first;\n"
            "  -- the session nobody blocks, but whose SID the others' blocking_session points to, is the culprit\n\n"
            "Fix:\n"
            "  alter system kill session '<sid>,<serial#>' immediate;\n\n"
            "What a senior DBA does next: on a real system, check with the batch owner before killing it and see how big "
            "the transaction is, because after a kill Oracle has to roll it back, which can take about as long as the work took. "
            "Then fix the root cause: have the batch commit in chunks, and set a timeout for sessions that sit idle.")
    workload_threads = 4          # worker 0 is the batch that holds the lock, 1-3 are clerks
    workload_pause = 2.0
    pause_workload_during_check = False
    check_needs_workload = True

    def __init__(self):
        self._batch_state = None  # per workload process: None -> holding -> done

    def make_params(self, rng, scale):
        lo = rng.randint(100, 800)
        return {"naccounts": 1000, "hot_lo": lo, "hot_hi": lo + 49}

    def setup(self, admin, params):
        db.create_shop_user(admin, params["shop_password"])
        db.run(
            admin,
            "create table shop.accounts (account_id number primary key, owner_name varchar2(60) not null,"
            " balance number(14,2) not null, updated_at date not null)",
        )
        db.run(
            admin,
            "insert into shop.accounts select n, 'CUSTOMER-' || n, mod(n * 37, 50000), sysdate "
            f"from {db.row_generator()}",
            {"n": params["naccounts"]},
        )
        db.run(
            admin,
            "create table shop.payments (payment_id number primary key, account_id number not null,"
            " amount number(12,2) not null, paid_at date not null)",
        )
        db.run(admin, "create sequence shop.payments_seq")
        admin.commit()
        db.gather(admin, "ACCOUNTS")

    def baseline(self, admin, params):
        return {"accounts": db.table_fingerprint(admin, "shop.accounts", "account_id")}

    def workload_session_init(self, conn, params, worker=0):
        conn.module = "ADJUST_BATCH" if worker == 0 else "PAYMENT_ENTRY"

    def workload_step(self, conn, params, rng, log, worker=0):
        if worker == 0:
            return self._batch_step(conn, params, log)
        acct = rng.randint(params["hot_lo"], params["hot_hi"])
        amt = rng.randint(100, 50000) / 100
        cur = conn.cursor()
        t0 = time.time()
        try:
            cur.execute(PAY_LOCK, a=acct)
            cur.execute(PAY_UPDATE, a=acct, amt=amt)
            cur.execute(PAY_INSERT, a=acct, amt=amt)
            conn.commit()
            log.info("PAYMENT_ENTRY account=%s amount=%.2f saved in %.2fs", acct, amt, time.time() - t0)
        except Exception as exc:
            if db.ora_code(exc) not in ORA_WAIT_TIMEOUT:
                raise
            conn.rollback()
            log.warning("PAYMENT_ENTRY account=%s gave up after %.0fs: %s",
                        acct, time.time() - t0, db.first_line(exc))
        finally:
            cur.close()

    def _batch_step(self, conn, params, log):
        if self._batch_state == "done":
            time.sleep(30)
            return
        if self._batch_state is None:
            db.run(
                conn,
                "update shop.accounts set updated_at = sysdate where account_id between :lo and :hi",
                {"lo": params["hot_lo"], "hi": params["hot_hi"]},
            )
            self._batch_state = "holding"  # deliberately no commit
            log.info("ADJUST_BATCH started month-end adjustment of %s accounts", params["hot_hi"] - params["hot_lo"] + 1)
            return
        # holding: stay idle in transaction and never touch the session, so it shows
        # INACTIVE with a growing last_call_et; once killed its lock is simply gone
        time.sleep(60)

    def reference_fix(self, admin, params):
        for sid, serial in db.rows(
            admin,
            "select sid, serial# from v$session where username = 'SHOP' and module = 'ADJUST_BATCH'",
        ):
            db.run(admin, f"alter system kill session '{int(sid)},{int(serial)}' immediate", ignore=(30, 31))
        time.sleep(15)  # let the clerks' pending waits finish so none count as stuck

    def collect(self, admin, params, baseline):
        stuck = db.scalar(
            admin,
            "select count(*) from v$session where username = 'SHOP'"
            " and blocking_session is not null and state = 'WAITING'"
            " and event = 'enq: TX - row lock contention' and wait_time_micro > :w",
            {"w": STUCK_MICROS},
        )
        lock_error = None
        try:
            db.run(
                admin,
                "select account_id from shop.accounts where account_id between :lo and :hi for update wait 3",
                {"lo": params["hot_lo"], "hi": params["hot_hi"]},
            )
        except Exception as exc:
            lock_error = db.first_line(exc)
        finally:
            admin.rollback()
        # clerks only reconnect after their session was killed, so a clerk that logged
        # on well after the others means the player killed the wrong sessions
        reconnected = db.scalar(
            admin,
            "select count(*) from v$session where username = 'SHOP' and module = 'PAYMENT_ENTRY'"
            " and logon_time > (select min(logon_time) + 30/86400 from v$session"
            " where username = 'SHOP' and module = 'PAYMENT_ENTRY')",
        )
        return {
            "clerks_reconnected": int(reconnected or 0),
            "stuck_sessions": int(stuck or 0),
            "lock_error": lock_error,
            "accounts": db.table_fingerprint(admin, "shop.accounts", "account_id"),
        }

    def evaluate(self, m, params, baseline):
        return [
            Criterion(
                "No app session blocked for more than 5 seconds",
                m["stuck_sessions"] == 0,
                f"{m['stuck_sessions']} session(s) still waiting on a lock" if m["stuck_sessions"] else "",
            ),
            Criterion(
                "Affected accounts can take payments again",
                m["lock_error"] is None,
                m["lock_error"] or "",
            ),
            Criterion(
                "No innocent staff sessions were killed",
                m["clerks_reconnected"] == 0,
                (f"{m['clerks_reconnected']} PAYMENT_ENTRY session(s) were killed and reconnected "
                  "(on a real system, whatever the clerk was entering would be lost)")
                if m["clerks_reconnected"] else "",
            ),
            integrity_criterion(m["accounts"], baseline["accounts"], "SHOP.ACCOUNTS"),
        ]
