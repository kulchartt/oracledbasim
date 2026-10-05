import time

from .. import db
from ..i18n import L, t
from ..scenario import Criterion, Scenario, integrity_criterion

QUERY = (
    "select /* dbasim:s02 month_end */ r.region_name, count(*), sum(s.amount) "
    "from shop.sales s join shop.regions r on r.region_id = s.region_id "
    "where s.region_id = :r group by r.region_name"
)
TOLERANCE = 0.10
# the report logs its speed per 1,000 rows so the threshold holds at any DBASIM_SCALE.
# Measured on Oracle Free: wrong plan ~0.55-0.8ms, after gathering stats ~0.07-0.08ms
SLOW_MS_PER_1K_ROWS = 0.25


def _close_enough(stat_rows, actual):
    if stat_rows is None or actual == 0:
        return False
    return abs(stat_rows - actual) <= TOLERANCE * actual


class StaleStats(Scenario):
    id = "s02"
    title = L("Report ปิดเดือนช้าหลังย้ายข้อมูล", "Month-end report slow after data migration")
    difficulty = L("ง่าย", "Easy")
    story = L(
        (
            "Ticket #5107 จากฝ่ายบัญชี:\n"
            "\"เมื่อวานทีม IT ย้ายข้อมูลยอดขายย้อนหลังเข้าระบบใหม่ ตั้งแต่นั้น report ปิดเดือน "
            "(MONTH_END) ช้าจากไม่กี่วินาทีเป็นหลายนาที พรุ่งนี้ต้องปิดงบแล้ว\"\n\n"
            "งานของคุณ: ทำให้ report กลับมาเร็ว โดยไม่แก้ SQL ของ report และไม่ลบข้อมูล"
        ),
        (
            "Ticket #5107 from Accounting:\n"
            "\"Yesterday IT migrated the historical sales data into the new system. Since then the month-end report "
            "(MONTH_END) has gone from a few seconds to several minutes. We have to close the books tomorrow.\"\n\n"
            "Your job: make the report fast again without changing the report's SQL and without deleting any data."
        ),
    )
    hints = [
        L(
            "ข้อมูลเพิ่งถูกโหลดเข้ามาเยอะ ลองเทียบจำนวนแถวที่ optimizer คิด (num_rows ใน dba_tables) กับ count(*) จริง",
            "A lot of data was just loaded. Compare the row count the optimizer believes (num_rows in dba_tables) "
            "with the real count(*)",
        ),
        L(
            "เก็บ statistics ใหม่ด้วย dbms_stats.gather_table_stats แล้วสังเกต error ที่ได้",
            "Regather statistics with dbms_stats.gather_table_stats and note the error you get",
        ),
        L(
            "ถ้าเจอ ORA-20005 แปลว่า stats ถูก lock อยู่ ลองดู stattype_locked ใน dba_tab_statistics",
            "ORA-20005 means the stats are locked. Check stattype_locked in dba_tab_statistics",
        ),
    ]
    solution = L(
        (
            "สาเหตุ: statistics ของ SALES บอกว่ามีแค่ 1,000 แถวและแต่ละ region มีไม่กี่แถว แล้วมีคน lock stats ไว้ "
            "หลังจากนั้นโหลดข้อมูลเพิ่มเป็นล้านแถว โดย 80% อยู่ใน region 1 optimizer ยังเชื่อตัวเลขเก่า "
            "จึงเลือกวิ่งผ่าน index ทีละแถว ซึ่งเหมาะกับข้อมูลไม่กี่แถว แต่ช้ามากเมื่อต้องอ่านเป็นล้านแถว\n\n"
            "วิธีแก้:\n"
            "  exec dbms_stats.unlock_table_stats('SHOP','SALES');\n"
            "  exec dbms_stats.gather_table_stats('SHOP','SALES', cascade => true);\n\n"
            "เรื่องที่ DBA อาวุโสจะทำต่อ: ถามว่าใคร lock stats และทำไม บางทีมตั้งใจ lock เพื่อให้ plan นิ่ง "
            "ถ้าปลดแล้วต้องตกลงกันว่าหลังโหลดข้อมูลก้อนใหญ่ต้องเก็บ stats ทุกครั้ง"
        ),
        (
            "Cause: the SALES statistics say the table has only 1,000 rows with a handful per region, and someone locked them. "
            "Then millions of rows were loaded, 80% of them in region 1. The optimizer still trusts the old numbers, "
            "so it walks the index row by row - fine for a few rows, but very slow when it has to read millions.\n\n"
            "Fix:\n"
            "  exec dbms_stats.unlock_table_stats('SHOP','SALES');\n"
            "  exec dbms_stats.gather_table_stats('SHOP','SALES', cascade => true);\n\n"
            "What a senior DBA does next: find out who locked the stats and why. Some teams lock them on purpose to keep plans stable. "
            "If you unlock them, agree that stats get regathered after every large data load."
        ),
    )
    workload_threads = 1
    workload_pause = 5.0

    def make_params(self, rng, scale):
        return {"nrows": int(2_000_000 * scale), "seed_rows": 1000}

    def setup(self, admin, params):
        db.create_shop_user(admin, params["shop_password"])
        db.run(admin, "create table shop.regions (region_id number primary key, region_name varchar2(30) not null)")
        db.run(
            admin,
            "insert into shop.regions select level, 'REGION-' || level from dual connect by level <= 10",
        )
        db.run(
            admin,
            "create table shop.sales ("
            " sale_id number primary key, region_id number not null,"
            " sale_date date not null, amount number(10,2) not null, note varchar2(100))",
        )
        db.run(
            admin,
            "insert into shop.sales select n, mod(n, 10) + 1, date '2025-01-01' + mod(n, 365),"
            f" mod(n * 37, 10000) / 10, rpad('s', 60, 's') from {db.row_generator()}",
            {"n": params["seed_rows"]},
        )
        admin.commit()
        # (region_id, sale_date): within one region the index walks rows in date order, so the
        # misplanned index access hops across the whole table instead of reading it once
        db.run(admin, "create index shop.sales_region_ix on shop.sales(region_id, sale_date)")
        db.gather(admin, "REGIONS")
        db.gather(admin, "SALES")
        # Freeze stats that describe a tiny, highly selective table, the way an
        # old test-system export would. Gathered stats from the 1,000 seed rows
        # alone would still favour a full scan, so the misplan has to be planted.
        db.run(
            admin,
            "begin"
            " dbms_stats.delete_column_stats('SHOP', 'SALES', 'REGION_ID', col_stat_type => 'HISTOGRAM');"
            " dbms_stats.set_table_stats('SHOP', 'SALES', numrows => 1000, numblks => 10);"
            " dbms_stats.set_column_stats('SHOP', 'SALES', 'REGION_ID', distcnt => 1000, density => 0.001);"
            " dbms_stats.set_index_stats('SHOP', 'SALES_REGION_IX', numrows => 1000, numlblks => 3,"
            "   numdist => 1000, avglblk => 1, avgdblk => 1, clstfct => 10, indlevel => 1);"
            " dbms_stats.lock_table_stats('SHOP', 'SALES');"
            " end;",
        )
        # the "data migration": skewed, 80% of rows land in region 1
        db.run(
            admin,
            "insert /*+ append */ into shop.sales select n + :seed,"
            " case when mod(n, 10) < 8 then 1 else mod(n, 9) + 2 end,"
            " date '2024-01-01' + mod(n, 730), mod(n * 37, 10000) / 10, rpad('s', 60, 's') "
            f"from {db.row_generator()}",
            {"n": params["nrows"], "seed": params["seed_rows"]},
        )
        admin.commit()

    def baseline(self, admin, params):
        return {
            "sales": db.table_fingerprint(admin, "shop.sales", "sale_id"),
            "ready_at": db.db_now(admin),
        }

    def workload_session_init(self, conn, params, worker=0):
        conn.module = "MONTH_END"

    def workload_step(self, conn, params, rng, log, worker=0):
        t0 = time.perf_counter()
        cur = conn.cursor()
        cur.execute(QUERY, r=1)
        result = cur.fetchall()
        cur.close()
        took_ms = (time.perf_counter() - t0) * 1000
        nrows = sum(r[1] for r in result)
        per_1k = took_ms / max(nrows, 1) * 1000
        if per_1k > SLOW_MS_PER_1K_ROWS:
            log.warning("MONTH_END report region=1: %s rows in %.2fs = %.2fms per 1,000 rows "
                        "(SLOW, normal < %.2fms)", f"{nrows:,}", took_ms / 1000, per_1k, SLOW_MS_PER_1K_ROWS)
        else:
            log.info("MONTH_END report region=1: %s rows in %.2fs = %.2fms per 1,000 rows",
                     f"{nrows:,}", took_ms / 1000, per_1k)

    def reference_fix(self, admin, params):
        db.run(admin, "begin dbms_stats.unlock_table_stats('SHOP', 'SALES');"
                      " dbms_stats.gather_table_stats('SHOP', 'SALES', cascade => true); end;")

    def collect(self, admin, params, baseline):
        fresh_sql = (
            "select num_rows, case when last_analyzed > to_date(:t, 'YYYY-MM-DD HH24:MI:SS')"
            " then 1 else 0 end from {view} where owner = 'SHOP' and {col} = :n"
            " and partition_name is null"
        )
        tab = db.rows(admin, fresh_sql.format(view="dba_tab_statistics", col="table_name"),
                      {"t": baseline["ready_at"], "n": "SALES"})
        ix = db.rows(admin, fresh_sql.format(view="dba_ind_statistics", col="index_name"),
                     {"t": baseline["ready_at"], "n": "SALES_REGION_IX"})
        return {
            "sales": db.table_fingerprint(admin, "shop.sales", "sale_id"),
            "tab_num_rows": tab[0][0] if tab else None,
            "tab_fresh": bool(tab and tab[0][1]),
            "ix_num_rows": ix[0][0] if ix else None,
            "ix_fresh": bool(ix and ix[0][1]),
        }

    def evaluate(self, m, params, baseline):
        actual = m["sales"]["count"]
        tab_ok = m["tab_fresh"] and _close_enough(m["tab_num_rows"], actual)
        ix_ok = m["ix_fresh"] and _close_enough(m["ix_num_rows"], actual)
        return [
            Criterion(
                t("statistics ของตาราง SALES ตรงกับข้อมูลจริง", "SALES table statistics match the real data"),
                tab_ok,
                t(f"optimizer คิดว่ามี {m['tab_num_rows'] or 0:,} แถว แต่จริงมี {actual:,} แถว",
                  f"optimizer thinks there are {m['tab_num_rows'] or 0:,} rows, actual is {actual:,}"),
            ),
            Criterion(
                t("statistics ของ index SALES_REGION_IX ตรงกับข้อมูลจริง",
                  "SALES_REGION_IX index statistics match the real data"),
                ix_ok,
                t(f"index stats บอก {m['ix_num_rows'] or 0:,} แถว",
                  f"index stats say {m['ix_num_rows'] or 0:,} rows"),
            ),
            integrity_criterion(m["sales"], baseline["sales"], "SHOP.SALES"),
        ]
