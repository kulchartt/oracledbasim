"""Background process that plays the application's users.

Started by `dbasim start`, stopped by `dbasim check` (when solved), `stop` or `reset`.
Everything it sees goes to ~/.dbasim/logs/app.log, which is what the player reads
with `dbasim logs` - the same way a DBA reads application logs at 2am.
"""
import logging
import random
import threading
import time

from . import db, state
from .scenarios import get

MAX_RUNTIME_SECONDS = 3 * 3600


def main():
    active = state.load().get("active")
    if not active:
        return
    scenario = get(active["scenario"])
    params = active["params"]

    logging.basicConfig(
        filename=state.app_log_path(),
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    log = logging.getLogger("app")
    log.info("application started (%s users)", scenario.workload_threads)
    stop_at = time.time() + MAX_RUNTIME_SECONDS

    def worker(n):
        rng = random.Random(n)
        conn = None
        while time.time() < stop_at:
            try:
                if conn is None:
                    conn = db.connect(db.SHOP_USER, params["shop_password"])
                    scenario.workload_session_init(conn, params, worker=n)
                scenario.workload_step(conn, params, rng, log, worker=n)
                time.sleep(scenario.workload_pause)
            except Exception as exc:
                log.error("APP ERROR: %s", db.first_line(exc))
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
                time.sleep(5)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True)
               for i in range(scenario.workload_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
