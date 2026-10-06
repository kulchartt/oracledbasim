import argparse
import os
import random
import signal
import string
import subprocess
import sys
import time

from . import __version__, db, license, state
from .scenarios import ALL, get

OK, BAD = "✓", "✗"


def _say(text=""):
    print(text, flush=True)


def _password(rng):
    alphabet = string.ascii_letters + string.digits
    return "Sh" + "".join(rng.choice(alphabet) for _ in range(12))


# ---- workload process -------------------------------------------------------

def _launch_workload():
    log = open(state.app_log_path(), "a", encoding="utf-8")
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": log}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen([sys.executable, "-m", "dbasim.workload"], **kwargs).pid


def _stop_workload(active):
    pid = (active or {}).get("workload_pid")
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    active["workload_pid"] = None


def _alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) would *terminate* the process on Windows
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---- commands ---------------------------------------------------------------

# ---- connection diagnosis -------------------------------------------------

DOCKER_RUN = ("docker run -d --name dbasim-oracle -p 1521:1521 -e ORACLE_PWD=<password> "
              "container-registry.oracle.com/database/free:latest")


def _docker(*args, timeout=15):
    """Run a docker CLI command; returns (ok, output) and never raises."""
    try:
        r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _diagnose_connection():
    """Lines that tell the player why nothing answers at the DSN, from the outside in:
    Docker installed? engine running? container there? started? database ready?"""
    import shutil
    dsn = db.config()["dsn"]
    host = dsn.split(":")[0].split("/")[0].lower()
    if host not in ("localhost", "127.0.0.1", "::1", "host.docker.internal"):
        return [f"Nothing answered at {dsn}. Check DBASIM_DSN and that the database is running."]
    if not shutil.which("docker"):
        return ["Docker is not installed (or not on PATH). dbasim needs Oracle Database Free in Docker:",
                "  https://www.docker.com/products/docker-desktop/  then: " + DOCKER_RUN]
    ok, _ = _docker("info")
    if not ok:
        return ["Docker is installed but the Docker engine is not running.",
                "  Open Docker Desktop and wait until it says 'Engine running', then try again.",
                "  If Docker Desktop shows 'An unexpected error occurred' mentioning engine.sock: click Quit,",
                "  open Docker Desktop again, and it normally starts on the second try.",
                "  Do NOT choose 'Reset to factory defaults' - that deletes the Oracle container."]
    ok, status = _docker("ps", "-a", "--filter", "name=^dbasim-oracle$", "--format", "{{.Status}}")
    if not ok or not status:
        return ["No Oracle container named dbasim-oracle exists yet. Create it with:", "  " + DOCKER_RUN,
                "  then wait for 'DATABASE IS READY TO USE' in: docker logs -f dbasim-oracle"]
    if not status.lower().startswith("up"):
        return [f"The Oracle container exists but is stopped ({status}). Start it with:",
                "  docker start dbasim-oracle", "  and try again after about 30 seconds."]
    ok, logs = _docker("logs", "--tail", "200", "dbasim-oracle", timeout=30)
    if ok and "DATABASE IS READY TO USE" not in logs:
        return ["The Oracle container is running but the database is still starting.",
                "  The first start downloads and configures Oracle and can take several minutes.",
                "  Watch it with: docker logs -f dbasim-oracle  and wait for 'DATABASE IS READY TO USE'."]
    return [f"The Oracle container is up, but nothing answered at {dsn}.",
            "  Check that the container publishes port 1521 (docker port dbasim-oracle) and that",
            "  DBASIM_DSN matches it. Restarting the container often helps: docker restart dbasim-oracle"]


def _explain_failure(kind):
    if kind == "auth":
        return ["Oracle rejected the SYSTEM password.",
                "  DBASIM_ADMIN_PASSWORD must be the ORACLE_PWD you gave the container when you created it."]
    return _diagnose_connection()



def cmd_doctor(args):
    c = db.config()
    _say(f"dbasim {__version__}  python {sys.version.split()[0]}")
    _say(f"DSN: {c['dsn']}  user: {c['admin_user']}")
    admin = db.admin_connect()
    banner, con = db.assert_safe_target(admin)
    _say(f"{OK} connected: {banner}")
    _say(f"{OK} container: {con}")
    users_ts = db.scalar(admin, "select count(*) from dba_tablespaces where tablespace_name = 'USERS'")
    _say(f"{OK if users_ts else BAD} tablespace USERS exists")
    _say("\nReady to play. Try: dbasim list")
    return 0


def cmd_list(args):
    st = state.load()
    best = state.best_scores(st)
    active = (st.get("active") or {}).get("scenario")
    _say(f"{'ID':<5}{'Level':<8}{'Plan':<6}{'Best':<6}Scenario")
    for s in ALL:
        mark = " <- playing" if s.id == active else ""
        score = f"{best[s.id]}" if s.id in best else "-"
        plan = "Pro" if s.premium else "Free"
        _say(f"{s.id:<5}{s.difficulty:<8}{plan:<6}{score:<6}{s.title}{mark}")
    if not license.on_sale() and any(s.premium for s in ALL):
        _say("Pro scenarios are not on sale yet (coming soon). Every Free scenario is playable now")
    return 0


def cmd_start(args):
    scenario = get(args.id)
    st = state.load()
    if st.get("active") and not st["active"].get("solved") and not args.force:
        _say(f"Scenario {st['active']['scenario']} is still in progress. Run dbasim reset first, or add --force")
        return 1
    if scenario.premium and not getattr(args, "skip_license", False):
        try:
            license.require_pro()
        except license.LicenseError as exc:
            _say(f"{BAD} {exc}")
            return 1
    _stop_workload(st.get("active"))

    rng = random.Random()
    params = scenario.make_params(rng, db.scale())
    params["shop_password"] = _password(rng)

    admin = db.admin_connect()
    db.assert_safe_target(admin)
    _say(f"Preparing scenario {scenario.id}: {scenario.title} (may take 1-3 minutes) ...")
    scenario.teardown(admin, params)
    scenario.setup(admin, params)
    baseline = scenario.baseline(admin, params)
    admin.close()

    st["active"] = state.new_active(scenario.id, params, baseline)
    state.app_log_path().write_text("", encoding="utf-8")
    state.save(st)  # the workload process reads this file, so save before launching it
    st["active"]["workload_pid"] = _launch_workload()
    state.save(st)

    _say("\n" + "=" * 64)
    _say(str(scenario.story))
    _say("=" * 64)
    _say("\nThe simulated app is running.  App log: dbasim logs  |  Hint: dbasim hint")
    _say("When you have fixed it, run: dbasim check")
    return 0


def _require_active(st):
    if not st.get("active"):
        _say("No scenario in progress. Use dbasim start <id>")
        return None
    return st["active"]


def cmd_status(args):
    st = state.load()
    active = _require_active(st)
    if not active:
        return 1
    s = get(active["scenario"])
    minutes = (time.time() - active["started_at"]) / 60
    running = bool(active.get("workload_pid"))
    _say(f"Scenario: {s.id} {s.title}")
    _say(f"Time: {minutes:.0f} min  Hints used: {active['hints_shown']}/{len(s.hints)}")
    _say(f"Score if solved now: {state.score(active)}")
    _say(f"App user: SHOP / {active['params']['shop_password']}")
    _say(f"Simulated app: {'running' if running else 'stopped'}")
    return 0


def cmd_logs(args):
    path = state.app_log_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for line in lines[-args.n:]:
        _say(line)
    if not lines:
        _say("(no log yet - give the app a moment)")
    return 0


def cmd_hint(args):
    st = state.load()
    active = _require_active(st)
    if not active:
        return 1
    s = get(active["scenario"])
    i = active["hints_shown"]
    if i >= len(s.hints):
        _say("No hints left. To give up, use dbasim solution")
        return 0
    active["hints_shown"] = i + 1
    state.save(st)
    _say(f"Hint {i + 1}/{len(s.hints)} (-{state.HINT_PENALTY} points): {s.hints[i]}")
    return 0


def cmd_check(args):
    st = state.load()
    active = _require_active(st)
    if not active:
        return 1
    s = get(active["scenario"])
    _say("Checking ...")
    if s.check_needs_workload and not active.get("solved") and not _alive(active.get("workload_pid")):
        _say(f"{BAD} The simulated app is not running and this scenario must be checked while it runs. "
               f"Restart with dbasim start {s.id} --force")
        return 1
    # usually pause the simulated app so its load does not skew the measurement
    was_running = bool(active.get("workload_pid")) and not active.get("solved")
    paused = was_running and s.pause_workload_during_check
    if paused:
        _stop_workload(active)
        state.save(st)

    def resume():
        if paused:
            active["workload_pid"] = _launch_workload()
            state.save(st)

    admin = db.admin_connect()
    try:
        results = s.check(admin, active["params"], active["baseline"])
    except Exception as exc:
        _say(f"{BAD} Could not check: {db.first_line(exc)}")
        _say("(if the scenario's objects are gone, restart with dbasim start --force)")
        resume()
        return 1
    finally:
        admin.close()
    for r in results:
        _say(f" {OK if r.passed else BAD} {r.name}" + (f"  — {r.detail}" if r.detail else ""))
    if not all(r.passed for r in results):
        _say("\nNot solved yet. Keep going, or ask for a hint: dbasim hint")
        resume()
        return 2
    if not active.get("solved"):
        entry = state.record_solved(st, active)
        active["solved"] = True
        _stop_workload(active)
        state.save(st)
        _say(f"\nSolved! Score {entry['score']} in {entry['minutes']} min")
    _say("\n--- How a senior DBA would explain it ---\n" + str(s.solution))
    return 0


def cmd_solution(args):
    st = state.load()
    active = _require_active(st)
    if not active:
        return 1
    if not args.yes:
        _say("Viewing the solution scores 0 for this scenario. If you are sure: dbasim solution --yes")
        return 0
    active["solution_revealed"] = True
    state.save(st)
    _say(str(get(active["scenario"]).solution))
    return 0


def cmd_stop(args):
    st = state.load()
    _stop_workload(st.get("active"))
    state.save(st)
    _say("Simulated app stopped")
    return 0


def cmd_reset(args):
    st = state.load()
    _stop_workload(st.get("active"))
    admin = db.admin_connect()
    db.assert_safe_target(admin)
    ALL[0].teardown(admin, {})
    admin.close()
    st["active"] = None
    state.save(st)
    _say("Scenario cleaned up")
    return 0


def _print_criteria(label, results):
    _say(f"  [{label}]")
    for r in results:
        _say(f"   {OK if r.passed else BAD} {r.name}" + (f"  — {r.detail}" if r.detail else ""))


def cmd_selftest(args):
    """Prove each scenario on this real database: broken before the fix, passing after it."""
    import platform
    from argparse import Namespace
    import oracledb

    admin = db.admin_connect()
    banner, con = db.assert_safe_target(admin)
    admin.close()
    _say(f"dbasim {__version__} | python {platform.python_version()} | oracledb {oracledb.__version__}"
         f" | {platform.system()} {platform.release()}")
    _say(f"{banner} | {con} | DBASIM_SCALE={db.scale()}")

    ids = args.ids or [s.id for s in ALL]
    summary = []
    for sid in ids:
        s = get(sid)
        _say(f"\n===== {s.id} {s.title} =====")
        t0 = time.time()
        verdict, note = "ERROR", ""
        try:
            if cmd_start(Namespace(id=s.id, force=True, skip_license=True)) != 0:
                raise RuntimeError("start failed")
            _say(f"  setup {time.time() - t0:.0f}s, waiting {args.warmup}s for the app to hit the problem ...")
            time.sleep(args.warmup)
            active = state.load()["active"]
            log_lines = state.app_log_path().read_text(encoding="utf-8").splitlines()
            _say("  [app log, last 6 lines]")
            for line in log_lines[-6:]:
                _say("   " + line)
            admin = db.admin_connect()
            try:
                before = s.check(admin, active["params"], active["baseline"])
                _print_criteria("before fix — should NOT pass", before)
                s.reference_fix(admin, active["params"])
                time.sleep(3)
                after = s.check(admin, active["params"], active["baseline"])
                _print_criteria("after reference fix — should pass", after)
            finally:
                admin.close()
            reproduced = not all(r.passed for r in before)
            solvable = all(r.passed for r in after)
            verdict = "OK" if reproduced and solvable else "FAIL"
            note = ("" if reproduced else "problem did not reproduce; ") + ("" if solvable else "fix did not pass")
        except Exception as exc:
            note = db.first_line(exc)
            _say(f"  {BAD} {note}")
        finally:
            try:
                cmd_reset(Namespace())
            except Exception as exc:
                _say(f"  {BAD} reset failed: {db.first_line(exc)}")
        summary.append((s.id, verdict, round(time.time() - t0), note))

    _say("\n===== SUMMARY =====")
    for sid, verdict, secs, note in summary:
        _say(f"{sid}  {verdict:<5} {secs:>4}s  {note}")
    return 0 if all(v == "OK" for _, v, _, _ in summary) else 2


# ---- dbasim Pro licence ---------------------------------------------------

def _show_license(lic):
    _say(f"{OK} dbasim Pro is active")
    _say(f"  key: {license.masked(lic['key'])}")
    if lic.get("plan"):
        _say(f"  plan: {lic['plan']}")
    if lic.get("email"):
        _say(f"  email: {lic['email']}")


def cmd_activate(args):
    try:
        lic = license.activate(args.key)
    except license.LicenseError as exc:
        _say(f"{BAD} {exc}")
        return 1
    except license.Offline as exc:
        _say(f"{BAD} Could not reach Lemon Squeezy: {exc}")
        return 1
    _show_license(lic)
    _say("Every scenario is unlocked. Try: dbasim list")
    return 0


def cmd_license(args):
    lic = license.load()
    if not lic:
        _say("dbasim Pro is not activated (the first 3 scenarios are free)")
        if not license.on_sale():
            _say(f"dbasim Pro is not on sale yet (coming soon). Follow {license.PROJECT_URL}")
            return 0
        _say(f"Subscribe: {license.pricing_url()}  then run dbasim activate <licence-key>")
        return 0
    try:
        lic = license.refresh() if args.refresh else license.require_pro()
    except license.LicenseError as exc:
        _say(f"{BAD} {exc}")
        return 1
    except license.Offline as exc:
        _say(f"{BAD} Could not reach Lemon Squeezy: {exc}")
        return 1
    _show_license(lic)
    return 0


def cmd_deactivate(args):
    try:
        removed = license.deactivate()
    except license.LicenseError as exc:
        _say(f"{BAD} {exc}")
        return 1
    _say("Licence removed from this computer; you can use the key on another one"
         if removed else "dbasim Pro is not activated on this computer")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="dbasim", description="Oracle DBA break-fix simulator")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check the connection to Oracle Free").set_defaults(fn=cmd_doctor)
    sub.add_parser("list", help="list scenarios").set_defaults(fn=cmd_list)
    sp = sub.add_parser("start", help="start a scenario")
    sp.add_argument("id")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_start)
    sub.add_parser("status", help="current scenario status").set_defaults(fn=cmd_status)
    lp = sub.add_parser("logs", help="show the simulated app's log")
    lp.add_argument("-n", type=int, default=30)
    lp.set_defaults(fn=cmd_logs)
    sub.add_parser("hint", help="get a hint (costs points)").set_defaults(fn=cmd_hint)
    sub.add_parser("check", help="check your fix").set_defaults(fn=cmd_check)
    so = sub.add_parser("solution", help="show the solution (scores 0)")
    so.add_argument("--yes", action="store_true")
    so.set_defaults(fn=cmd_solution)
    sub.add_parser("stop", help="stop the simulated app").set_defaults(fn=cmd_stop)
    sub.add_parser("reset", help="remove everything dbasim created").set_defaults(fn=cmd_reset)
    ap = sub.add_parser("activate", help="activate dbasim Pro with a licence key")
    ap.add_argument("key")
    ap.set_defaults(fn=cmd_activate)
    lc = sub.add_parser("license", help="show dbasim Pro status")
    lc.add_argument("--refresh", action="store_true")
    lc.set_defaults(fn=cmd_license)
    sub.add_parser("deactivate", help="remove the licence from this computer").set_defaults(fn=cmd_deactivate)
    tp = sub.add_parser("selftest", help="(developers) prove every scenario on a real DB: broken before, passing after")
    tp.add_argument("ids", nargs="*")
    tp.add_argument("--warmup", type=int, default=45)
    tp.set_defaults(fn=cmd_selftest)
    return p


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # check marks and box characters on Windows consoles
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except db.SetupError as exc:
        _say(f"{BAD} {exc.args[0] if exc.args else exc}")
        return 1
    except Exception as exc:
        kind = db.error_kind(exc)
        if kind:
            _say(f"{BAD} Could not connect to Oracle: {db.first_line(exc)}")
            for line in _explain_failure(kind):
                _say(line)
            return 1
        if db.ora_code(exc):
            _say(f"{BAD} Oracle error: {db.first_line(exc)}")
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
