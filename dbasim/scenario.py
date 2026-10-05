"""Base class every scenario implements.

A scenario has three parts that never mix:
  setup()     plant the problem (runs as SYSTEM)
  workload    simulated app users that keep hitting the problem (runs as SHOP)
  collect() + evaluate()   measure the DB, then judge the numbers.
evaluate() is pure Python so the grading rules are unit-testable without Oracle.
"""
from dataclasses import dataclass



@dataclass
class Criterion:
    name: str
    passed: bool
    detail: str = ""


class Scenario:
    id = ""
    title = ""
    difficulty = ""          # Easy / Medium / Hard
    story = ""               # the "ticket" the player receives
    hints = []               # ordered, vaguest first
    solution = ""            # senior-DBA write-up shown after solving
    premium = False          # True: needs an active dbasim Pro licence to start
    workload_threads = 2
    workload_pause = 1.0     # seconds between workload iterations per thread
    # most checks pause the simulated app so its load does not skew measurements;
    # a scenario whose problem *is* a live app session keeps it running and
    # refuses to grade when the app has been stopped
    pause_workload_during_check = True
    check_needs_workload = False

    def make_params(self, rng, scale):
        return {}

    def setup(self, admin, params):
        raise NotImplementedError

    def baseline(self, admin, params):
        return {}

    def workload_session_init(self, conn, params, worker=0):
        """Session settings the simulated app applies after connecting."""

    def workload_step(self, conn, params, rng, log, worker=0):
        raise NotImplementedError

    def collect(self, admin, params, baseline):
        raise NotImplementedError

    def evaluate(self, metrics, params, baseline):
        raise NotImplementedError

    def reference_fix(self, admin, params):
        """The textbook fix, applied by `dbasim selftest` to prove the scenario is solvable."""
        raise NotImplementedError

    def check(self, admin, params, baseline):
        return self.evaluate(self.collect(admin, params, baseline), params, baseline)

    def teardown(self, admin, params):
        """Remove everything any scenario may have created."""
        from . import db
        db.drop_shop_user(admin)
        db.drop_lab_temp_tablespace(admin)
        db.reset_lab_settings(admin)


def integrity_criterion(metrics_fp, baseline_fp, label):
    ok = metrics_fp == baseline_fp
    detail = "" if ok else (f"Data changed: {baseline_fp['count']:,} rows before, {metrics_fp['count']:,} now "
        "(do not fix the problem by deleting or changing the users' data)")
    return Criterion(f"Data in {label} is unchanged", ok, detail)
