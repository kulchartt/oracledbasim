import time

from .. import db
from ..i18n import L, t
from ..scenario import Criterion, Scenario, integrity_criterion

BATCH = (
    "select /* dbasim:s03 nightly_batch */ count(*) from ("
    " select invoice_id, row_number() over (order by note desc, invoice_id) rn"
    " from shop.invoices) where rn > 0"
)
# the "legacy" batch session forces sorts to spill to TEMP, which makes the
# failure deterministic regardless of how much PGA the container has
BATCH_SESSION = [
    "alter session set workarea_size_policy = manual",
    "alter session set sort_area_size = 1048576",
]


def run_batch(conn):
    cur = conn.cursor()
    try:
        cur.execute(BATCH)
        return cur.fetchone()[0]
    finally:
        cur.close()


class TempFull(Scenario):
    id = "s03"
    title = L("Batch กลางคืนล้มทุกคืน", "The nightly batch fails every night")
    difficulty = L("ง่าย", "Easy")
    story = L(
        (
            "Ticket #5233 จากทีม Operation (ส่งมาตอนตี 2):\n"
            "\"job NIGHTLY_BATCH ล้มอีกแล้ว ดู log ได้ด้วย dbasim logs ช่วยแก้ให้รันผ่านก่อนเช้า "
            "ไม่งั้น report เช้าจะไม่มีข้อมูล\"\n\n"
            "งานของคุณ: ทำให้ batch รันผ่าน โดยไม่แก้ SQL ของ batch และไม่ลบข้อมูล"
        ),
        (
            "Ticket #5233 from the Operations team (filed at 2 AM):\n"
            "\"The NIGHTLY_BATCH job failed again. Logs are in dbasim logs. Please get it running "
            "before morning, or the morning report will have no data.\"\n\n"
            "Your task: get the batch to run successfully without changing the batch SQL and without deleting data"
        ),
    )
    hints = [
        L(
            "อ่าน error ใน log ให้ครบทั้งบรรทัด มันบอกชื่อ tablespace ไว้ด้วย",
            "Read the whole error line in the log - it names the tablespace too",
        ),
        L(
            "ORA-01652 คือพื้นที่ temporary ไม่พอ ลองดูว่า user SHOP ใช้ temporary tablespace ไหน (dba_users)",
            "ORA-01652 means temporary space ran out. Check which temporary tablespace user SHOP uses (dba_users)",
        ),
        L(
            "ดูขนาดและการตั้ง autoextend ของ tempfile ได้จาก dba_temp_files",
            "Tempfile sizes and autoextend settings are in dba_temp_files",
        ),
    ]
    solution = L(
        (
            "สาเหตุ: user SHOP ถูกตั้งให้ใช้ temporary tablespace TEMP_RPT ซึ่งมีแค่ 8MB และปิด autoextend "
            "batch ต้อง sort ข้อมูลหลายสิบ MB จึงล้มด้วย ORA-01652\n\n"
            "วิธีแก้ (เลือกอย่างใดอย่างหนึ่ง):\n"
            "  alter database tempfile '<path>' resize 500M;\n"
            "  alter tablespace temp_rpt add tempfile '<path>' size 500M;\n"
            "  alter user shop temporary tablespace temp;\n\n"
            "เรื่องที่ DBA อาวุโสจะทำต่อ: ระวังการเปิด autoextend แบบไม่จำกัด เพราะ query ที่เขียนพลาด "
            "อาจกิน disk จนเต็ม ควรตั้ง maxsize และตั้ง alert เมื่อ temp ใช้เกิน 80%"
        ),
        (
            "Cause: user SHOP is assigned temporary tablespace TEMP_RPT, which is only 8MB with autoextend off. "
            "The batch needs to sort tens of MB, so it fails with ORA-01652\n\n"
            "Fix (pick one):\n"
            "  alter database tempfile '<path>' resize 500M;\n"
            "  alter tablespace temp_rpt add tempfile '<path>' size 500M;\n"
            "  alter user shop temporary tablespace temp;\n\n"
            "What a senior DBA does next: be careful with unlimited autoextend - one badly written query "
            "can fill the disk. Set a maxsize and alert when temp usage passes 80%"
        ),
    )
    workload_threads = 1
    workload_pause = 30.0

    def make_params(self, rng, scale):
        # the sort needs ~215 bytes/row; never shrink below ~40MB or it fits in 8MB
        return {"nrows": max(200_000, int(400_000 * scale)), "temp_mb": 8}

    def setup(self, admin, params):
        temp_file = db.scalar(
            admin, "select file_name from dba_temp_files where tablespace_name = 'TEMP' and rownum = 1"
        )
        folder = temp_file.rsplit("/", 1)[0]
        db.run(
            admin,
            f"create temporary tablespace {db.LAB_TEMP_TS} tempfile "
            f"'{folder}/{db.FILE_TAG}temp_rpt01.dbf' size {int(params['temp_mb'])}M reuse autoextend off",
        )
        db.create_shop_user(admin, params["shop_password"], temp_ts=db.LAB_TEMP_TS)
        db.run(
            admin,
            "create table shop.invoices ("
            " invoice_id number primary key, customer_id number not null,"
            " invoice_date date not null, amount number(12,2) not null, note varchar2(200))",
        )
        db.run(
            admin,
            "insert /*+ append */ into shop.invoices select n, mod(n, 5000) + 1,"
            " date '2025-01-01' + mod(n, 365), mod(n * 37, 100000) / 100,"
            " rpad(to_char(mod(n * 7919, 1000003)), 200, 'x') "
            f"from {db.row_generator()}",
            {"n": params["nrows"]},
        )
        admin.commit()
        db.gather(admin, "INVOICES")

    def baseline(self, admin, params):
        return {"invoices": db.table_fingerprint(admin, "shop.invoices", "invoice_id")}

    def workload_session_init(self, conn, params, worker=0):
        conn.module = "NIGHTLY_BATCH"
        for sql in BATCH_SESSION:
            db.run(conn, sql)

    def workload_step(self, conn, params, rng, log, worker=0):
        t0 = time.time()
        try:
            n = run_batch(conn)
            log.info("NIGHTLY_BATCH finished OK, %s rows ranked in %.1fs", n, time.time() - t0)
        except Exception as exc:
            log.error("NIGHTLY_BATCH FAILED after %.1fs: %s", time.time() - t0, db.first_line(exc))

    def reference_fix(self, admin, params):
        db.run(admin, "alter user shop temporary tablespace temp")

    def collect(self, admin, params, baseline):
        error = None
        conn = db.connect(db.SHOP_USER, params["shop_password"])
        try:
            self.workload_session_init(conn, params)
            run_batch(conn)
        except Exception as exc:
            error = db.first_line(exc)
        finally:
            conn.close()
        return {
            "batch_error": error,
            "invoices": db.table_fingerprint(admin, "shop.invoices", "invoice_id"),
        }

    def evaluate(self, m, params, baseline):
        return [
            Criterion(
                t("NIGHTLY_BATCH รันจนจบโดยไม่ error", "NIGHTLY_BATCH runs to completion without errors"),
                m["batch_error"] is None,
                m["batch_error"] or "",
            ),
            integrity_criterion(m["invoices"], baseline["invoices"], "SHOP.INVOICES"),
        ]
