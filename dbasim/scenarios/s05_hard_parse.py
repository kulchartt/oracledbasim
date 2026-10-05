import random
import time

from .. import db
from ..i18n import L, t
from ..scenario import Criterion, Scenario, integrity_criterion

# The "application" glues the id into the SQL text instead of using a bind variable
LOOKUP = "select /* dbasim:s05 product_lookup */ name, price from shop.products where product_id = {pid}"
BATCH = 50
SAMPLE_RUNS = 200
MAX_HARD_RATIO = 0.10

STATS = (
    "select n.name, s.value from v$sesstat s join v$statname n on n.statistic# = s.statistic#"
    " where s.sid = :sid and n.name = 'parse count (hard)'"
)


class HardParse(Scenario):
    id = "s05"
    title = L("CPU พุ่งหลังปล่อยแอปเวอร์ชันใหม่", "CPU spikes after a new app release")
    difficulty = L("กลาง", "Medium")
    premium = True
    story = L(
        "Ticket #5412 จากทีม Infra:\n"
        "\"หลังทีม dev ปล่อยแอปร้านค้าเวอร์ชันใหม่ CPU ของ DB สูงตลอดวัน หน้าค้นหาสินค้า "
        "(PRODUCT_LOOKUP) ช้าลงทั้งระบบ แต่ไล่ดูแล้วไม่เจอ SQL ตัวไหนช้าเป็นพิเศษ "
        "ทีม dev แก้โค้ดได้อีกทีใน sprint หน้า คือ 2 สัปดาห์\"\n\n"
        "งานของคุณ: ลดภาระที่ทำให้ CPU สูง โดยไม่แก้โค้ดแอปและไม่ลบข้อมูล",
        "Ticket #5412 from the Infra team:\n"
        "\"Since dev shipped the new version of the shop app, DB CPU has been pegged all day. "
        "Product search (PRODUCT_LOOKUP) is slow across the board, but we can't find any single "
        "slow SQL. Dev can't change the code until next sprint, which is 2 weeks out.\"\n\n"
        "Your task: cut the load that is driving CPU up, without changing app code or deleting data",
    )
    hints = [
        L("SQL แต่ละตัวเร็ว แต่จำนวนครั้งที่ Oracle ต้อง parse ใหม่อาจสูงผิดปกติ "
          "ลองเทียบ 'parse count (hard)' กับ 'execute count' ใน v$sysstat",
          "Each SQL is fast, but the number of times Oracle has to parse from scratch may be abnormal. "
          "Compare 'parse count (hard)' with 'execute count' in v$sysstat"),
        L("ใน v$sql ลองหา SQL ที่หน้าตาเหมือนกันแต่ต่างแค่ตัวเลข "
          "(group by force_matching_signature having count(*) > 100)",
          "In v$sql, look for statements that are identical except for a literal number "
          "(group by force_matching_signature having count(*) > 100)"),
        L("แก้โค้ดให้ใช้ bind variable ไม่ได้ แต่มี parameter ที่ให้ Oracle แปลงค่าคงที่ใน SQL "
          "เป็น bind ให้อัตโนมัติ",
          "You can't change the code to use bind variables, but there is a parameter that makes Oracle "
          "turn literals in SQL into binds automatically"),
    ]
    solution = L(
        "สาเหตุ: แอปต่อ product_id เข้าไปในข้อความ SQL ตรงๆ ทุกครั้งที่ค้นหาจึงเป็น SQL \"ใหม่\" "
        "Oracle ต้องวิเคราะห์และสร้าง plan ใหม่ทุกครั้ง (hard parse) ซึ่งกิน CPU และ shared pool "
        "แม้ตัว SQL จะเร็ว ทำเป็นพันครั้งต่อนาทีก็หนัก\n\n"
        "วิธีแก้ชั่วคราว (เฉพาะ user ของแอป ไม่กระทบระบบอื่น):\n"
        "  create or replace trigger system.shop_cursor_sharing\n"
        "  after logon on shop.schema\n"
        "  begin execute immediate 'alter session set cursor_sharing = force'; end;\n"
        "  /\n"
        "หรือแบบทั้งระบบ: alter system set cursor_sharing = force;\n"
        "(แบบ trigger มีผลกับ session ที่ต่อใหม่เท่านั้น ส่วนแบบ alter system มีผลทันที)\n\n"
        "เรื่องที่ DBA อาวุโสจะทำต่อ: cursor_sharing = force เป็นแค่ยาแก้ปวด "
        "มันทำให้ SQL ที่ควรได้ plan ต่างกันตามค่า มาใช้ plan เดียวกันได้ "
        "ต้องให้ dev แก้ไปใช้ bind variable แล้วถอดออกทันที",
        "Cause: the app concatenates product_id straight into the SQL text, so every search is a \"new\" SQL. "
        "Oracle has to analyze it and build a fresh plan every time (hard parse), which burns CPU and shared pool. "
        "Each SQL is fast, but thousands per minute add up\n\n"
        "Temporary fix (app user only, no impact on anything else):\n"
        "  create or replace trigger system.shop_cursor_sharing\n"
        "  after logon on shop.schema\n"
        "  begin execute immediate 'alter session set cursor_sharing = force'; end;\n"
        "  /\n"
        "Or system-wide: alter system set cursor_sharing = force;\n"
        "(The trigger only affects new sessions; alter system takes effect immediately)\n\n"
        "What a senior DBA does next: cursor_sharing = force is just a painkiller. "
        "It can force SQL that should get different plans for different values onto a single plan. "
        "Get dev to switch to bind variables, then remove it right away",
    )
    workload_threads = 4
    workload_pause = 0.0

    def __init__(self):
        self._steps = {}

    def make_params(self, rng, scale):
        return {"nprod": max(10_000, int(100_000 * scale))}

    def setup(self, admin, params):
        db.create_shop_user(admin, params["shop_password"])
        db.run(
            admin,
            "create table shop.products (product_id number primary key, name varchar2(80) not null,"
            " category varchar2(30) not null, price number(10,2) not null)",
        )
        db.run(
            admin,
            "insert into shop.products select n, 'PRODUCT-' || n, 'CAT-' || mod(n, 40),"
            f" mod(n * 37, 99900) / 100 + 1 from {db.row_generator()}",
            {"n": params["nprod"]},
        )
        admin.commit()
        db.gather(admin, "PRODUCTS")

    def baseline(self, admin, params):
        return {"products": db.table_fingerprint(admin, "shop.products", "product_id")}

    def workload_session_init(self, conn, params, worker=0):
        conn.module = "PRODUCT_LOOKUP"

    def workload_step(self, conn, params, rng, log, worker=0):
        cur = conn.cursor()
        t0 = time.time()
        for _ in range(BATCH):
            cur.execute(LOOKUP.format(pid=rng.randint(1, params["nprod"])))
            cur.fetchall()
        cur.close()
        n = self._steps.get(worker, 0) + 1
        self._steps[worker] = n
        if n % 20 == 1:
            log.info("PRODUCT_LOOKUP worker=%s %s lookups in %.2fs", worker, BATCH, time.time() - t0)

    def reference_fix(self, admin, params):
        db.run(admin, "alter system set cursor_sharing = force")

    def collect(self, admin, params, baseline):
        fp = db.table_fingerprint(admin, "shop.products", "product_id")
        try:
            conn = db.connect(db.SHOP_USER, params["shop_password"])
        except Exception as exc:
            if db.ora_code(exc) in (4088, 4098):  # a broken logon trigger blocks the app itself
                return {"connect_error": db.first_line(exc), "hard_parses": 0,
                        "runs": SAMPLE_RUNS, "products": fp}
            raise
        try:
            sid = int(db.scalar(conn, "select sys_context('USERENV', 'SID') from dual"))
            before = int(db.rows(admin, STATS, {"sid": sid})[0][1])
            # ids the workload never uses, so nothing is already in the shared pool
            base = random.randint(10**9, 2 * 10**9)
            cur = conn.cursor()
            for i in range(SAMPLE_RUNS):
                cur.execute(LOOKUP.format(pid=base + i))
                cur.fetchall()
            cur.close()
            after = int(db.rows(admin, STATS, {"sid": sid})[0][1])
        finally:
            conn.close()
        return {"connect_error": None, "hard_parses": after - before,
                "runs": SAMPLE_RUNS, "products": fp}

    def evaluate(self, m, params, baseline):
        if m.get("connect_error"):
            return [
                Criterion(t("แอปยัง login เข้า DB ได้", "App can still log in to the DB"), False,
                          t(f"logon trigger ทำให้ login ไม่ได้: {m['connect_error']} "
                            "(ดู error ด้วย select * from dba_errors where type = 'TRIGGER')",
                            f"The logon trigger blocks login: {m['connect_error']} "
                            "(see the error with select * from dba_errors where type = 'TRIGGER')")),
                integrity_criterion(m["products"], baseline["products"], "SHOP.PRODUCTS"),
            ]
        ratio = m["hard_parses"] / m["runs"]
        return [
            Criterion(
                t(f"session ใหม่ของแอป hard parse ไม่เกิน {MAX_HARD_RATIO:.0%} ของ SQL ที่รัน",
                  f"A new app session hard parses at most {MAX_HARD_RATIO:.0%} of the SQL it runs"),
                ratio <= MAX_HARD_RATIO,
                t(f"วัดได้ {m['hard_parses']} จาก {m['runs']} ครั้ง ({ratio:.0%})",
                  f"Measured {m['hard_parses']} of {m['runs']} runs ({ratio:.0%})"),
            ),
            integrity_criterion(m["products"], baseline["products"], "SHOP.PRODUCTS"),
        ]
