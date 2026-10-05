import statistics
import time

from .. import db
from ..i18n import L, t
from ..scenario import Criterion, Scenario, integrity_criterion

QUERY = (
    "select /* dbasim:s01 order_search */ order_id, order_date, amount "
    "from shop.orders where customer_id = :c and status = 'OPEN'"
)
MAX_GETS_PER_EXEC = 200
# the app reports its search latency every LOG_EVERY searches. Measured on Oracle Free:
# indexed ~0.3-1ms under app load, full scan ~5ms at 150k rows and ~10ms at 300k, so 3ms splits them
LOG_EVERY = 20
SLOW_MEDIAN_MS = 3.0
SAMPLE_RUNS = 20

LOGICAL_READS = (
    "select m.value from v$mystat m join v$statname s on s.statistic# = m.statistic# "
    "where s.name = 'session logical reads'"
)


class MissingIndex(Scenario):
    id = "s01"
    title = L("หน้าค้นหาออเดอร์ช้า", "Order search page is slow")
    difficulty = L("ง่าย", "Easy")
    story = L(
        "Ticket #4821 จากทีม Call Center:\n"
        "\"หน้าค้นหาออเดอร์ที่ยังไม่ปิดของลูกค้า (ORDER_SEARCH) ช้ามาก กดค้นทีรอหลายวินาที "
        "ลูกค้าในสายรอนาน เมื่อก่อนไม่เป็นแบบนี้\"\n\n"
        "งานของคุณ: หาสาเหตุและทำให้หน้าค้นหาเร็วขึ้น โดยห้ามแก้โค้ดของแอป และห้ามลบข้อมูล",
        "Ticket #4821 from the Call Center team:\n"
        "\"The open-orders search page (ORDER_SEARCH) is really slow. Every search takes several seconds "
        "and customers on the line are left waiting. It never used to be like this.\"\n\n"
        "Your task: find the cause and make the search page fast again, without changing app code or deleting data",
    )
    hints = [
        L("เริ่มจากหาว่า SQL ไหนกินทรัพยากรมากที่สุด ลองดู v$sql เรียงตาม buffer_gets หรือดูจาก module ORDER_SEARCH",
          "Start by finding the most expensive SQL: query v$sql ordered by buffer_gets, or filter on module ORDER_SEARCH"),
        L("เจอ SQL แล้ว ลองดู execution plan จริงด้วย dbms_xplan.display_cursor('<sql_id>')",
          "Once you have the SQL, look at its actual execution plan with dbms_xplan.display_cursor('<sql_id>')"),
        L("plan อ่านทั้งตาราง ORDERS เพื่อหาแถวไม่กี่แถว ลองดูว่าคอลัมน์ใน WHERE มี index หรือยัง (dba_ind_columns)",
          "The plan reads the whole ORDERS table to find a handful of rows. Check whether the WHERE columns are indexed (dba_ind_columns)"),
    ]
    solution = L(
        "สาเหตุ: query กรองด้วย customer_id และ status แต่ไม่มี index บนคอลัมน์เหล่านั้น "
        "Oracle จึงต้องอ่านทั้งตาราง (TABLE ACCESS FULL) ทุกครั้งที่มีคนกดค้นหา\n\n"
        "วิธีแก้ที่ดี:\n"
        "  create index shop.orders_cust_status_ix on shop.orders(customer_id, status);\n\n"
        "ทำไมใส่สองคอลัมน์: ลูกค้าหนึ่งคนมีหลายออเดอร์ แต่ที่ยัง OPEN มีแค่ไม่กี่ตัว "
        "index ที่มีทั้ง customer_id และ status จะพาไปที่แถวที่ต้องการตรงๆ\n\n"
        "เรื่องที่ DBA อาวุโสจะทำต่อ: ในระบบจริงต้องดูด้วยว่า index ใหม่ทำให้ INSERT ช้าลงแค่ไหน "
        "และสร้างด้วย ONLINE ถ้าตารางมีคนใช้งานอยู่",
        "Cause: the query filters on customer_id and status, but neither column is indexed, "
        "so Oracle has to read the whole table (TABLE ACCESS FULL) every time someone searches.\n\n"
        "A good fix:\n"
        "  create index shop.orders_cust_status_ix on shop.orders(customer_id, status);\n\n"
        "Why two columns: a customer has many orders, but only a few are still OPEN. "
        "An index on both customer_id and status goes straight to the rows you need.\n\n"
        "What a senior DBA would also do: on a real system, check how much the new index slows down INSERTs, "
        "and build it ONLINE if the table is in active use",
    )
    workload_threads = 3
    workload_pause = 0.5

    def __init__(self):
        self._recent = {}  # per workload thread: latencies (ms) since the last log line

    def make_params(self, rng, scale):
        # below ~150k rows a cached full scan is too quick to show up as SLOW in the app log
        nrows = max(150_000, int(1_000_000 * scale))
        return {"nrows": nrows, "ncust": max(1000, nrows // 20)}

    def setup(self, admin, params):
        db.create_shop_user(admin, params["shop_password"])
        db.run(
            admin,
            "create table shop.orders ("
            " order_id number primary key, customer_id number not null,"
            " status varchar2(10) not null, order_date date not null,"
            " amount number(10,2) not null, note varchar2(100))",
        )
        db.run(
            admin,
            "insert /*+ append */ into shop.orders "
            "select n, mod(n * 7919, :ncust) + 1,"
            " case when mod(trunc(n / 7), 20) = 0 then 'OPEN' else 'CLOSED' end,"
            " date '2024-01-01' + mod(n, 700), round(mod(n * 37, 100000) / 100, 2),"
            " rpad('x', 100, 'x') "
            f"from {db.row_generator()}",
            {"n": params["nrows"], "ncust": params["ncust"]},
        )
        admin.commit()
        # a decoy index so "is there any index?" is not the whole investigation
        db.run(admin, "create index shop.orders_date_ix on shop.orders(order_date)")
        db.gather(admin, "ORDERS")

    def baseline(self, admin, params):
        return {"orders": db.table_fingerprint(admin, "shop.orders", "order_id")}

    def workload_session_init(self, conn, params, worker=0):
        conn.module = "ORDER_SEARCH"

    def workload_step(self, conn, params, rng, log, worker=0):
        cust = rng.randint(1, params["ncust"])
        t0 = time.perf_counter()
        cur = conn.cursor()
        cur.execute(QUERY, c=cust)
        cur.fetchall()
        cur.close()
        recent = self._recent.setdefault(worker, [])
        recent.append((time.perf_counter() - t0) * 1000)
        if len(recent) < LOG_EVERY:
            return
        median, slowest = statistics.median(recent), max(recent)
        recent.clear()
        if median > SLOW_MEDIAN_MS:
            log.warning("ORDER_SEARCH last %s searches: median %.1fms, slowest %.1fms (SLOW, normal < %.0fms)",
                        LOG_EVERY, median, slowest, SLOW_MEDIAN_MS)
        else:
            log.info("ORDER_SEARCH last %s searches: median %.1fms, slowest %.1fms",
                     LOG_EVERY, median, slowest)

    def reference_fix(self, admin, params):
        db.run(admin, "create index shop.orders_cust_status_ix on shop.orders(customer_id, status)")

    def collect(self, admin, params, baseline):
        db.run(admin, "delete from plan_table where statement_id = 'DBASIM_S01'")
        # never reuse a cached EXPLAIN PLAN cursor: once the player's DDL (create index)
        # invalidates it, re-executing it fails with ORA-00900
        cur = admin.cursor()
        try:
            cur.prepare("explain plan set statement_id = 'DBASIM_S01' for " + QUERY.replace(":c", "1"),
                        cache_statement=False)
            cur.execute(None)
        finally:
            cur.close()
        full = db.scalar(
            admin,
            "select count(*) from plan_table where statement_id = 'DBASIM_S01'"
            " and operation = 'TABLE ACCESS' and options like 'FULL%'"
            " and object_owner = 'SHOP' and object_name = 'ORDERS'",
        )
        db.run(admin, "delete from plan_table where statement_id = 'DBASIM_S01'")
        admin.commit()

        cur = admin.cursor()
        before = db.scalar(admin, LOGICAL_READS)
        t0 = time.time()
        for i in range(SAMPLE_RUNS):
            cur.execute(QUERY, c=(i * 7919) % params["ncust"] + 1)
            cur.fetchall()
        elapsed = time.time() - t0
        after = db.scalar(admin, LOGICAL_READS)
        cur.close()
        return {
            "full_scan": full > 0,
            "gets_per_exec": (after - before) / SAMPLE_RUNS,
            "ms_per_exec": elapsed * 1000 / SAMPLE_RUNS,
            "orders": db.table_fingerprint(admin, "shop.orders", "order_id"),
        }

    def evaluate(self, m, params, baseline):
        return [
            Criterion(
                t("query ค้นหาไม่อ่านทั้งตาราง ORDERS แล้ว",
                  "search query no longer reads the whole ORDERS table"),
                not m["full_scan"],
                t("plan ยังเป็น TABLE ACCESS FULL บน SHOP.ORDERS",
                  "plan is still TABLE ACCESS FULL on SHOP.ORDERS") if m["full_scan"] else "",
            ),
            Criterion(
                t(f"logical reads ต่อครั้งต่ำกว่า {MAX_GETS_PER_EXEC}",
                  f"logical reads per execution below {MAX_GETS_PER_EXEC}"),
                m["gets_per_exec"] < MAX_GETS_PER_EXEC,
                t(f"วัดได้ {m['gets_per_exec']:,.0f} ต่อครั้ง ({m['ms_per_exec']:,.0f} ms)",
                  f"measured {m['gets_per_exec']:,.0f} per execution ({m['ms_per_exec']:,.0f} ms)"),
            ),
            integrity_criterion(m["orders"], baseline["orders"], "SHOP.ORDERS"),
        ]
