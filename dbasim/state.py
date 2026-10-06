"""Local progress file (~/.dbasim/state.json). Will move server-side later."""
import json
import os
import time
from pathlib import Path


def home():
    p = Path(os.environ.get("DBASIM_HOME", Path.home() / ".dbasim"))
    (p / "logs").mkdir(parents=True, exist_ok=True)
    return p


def app_log_path():
    return home() / "logs" / "app.log"


def _path():
    return home() / "state.json"


def load():
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"active": None, "history": []}


def save(state):
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_path())


def config_path():
    return home() / "config.json"


def load_config():
    """Settings saved by `dbasim setup` (password, dsn, scale). Environment variables override them."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(cfg):
    tmp = config_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(config_path())
    try:
        os.chmod(config_path(), 0o600)  # the file holds the SYSTEM password of the local lab database
    except OSError:
        pass


def new_active(scenario_id, params, baseline):
    return {
        "scenario": scenario_id,
        "started_at": time.time(),
        "params": params,
        "baseline": baseline,
        "hints_shown": 0,
        "solution_revealed": False,
        "workload_pid": None,
    }


HINT_PENALTY = 15


def score(active):
    if active.get("solution_revealed"):
        return 0
    return max(10, 100 - HINT_PENALTY * active.get("hints_shown", 0))


def record_solved(state, active, finished_at=None):
    finished_at = finished_at or time.time()
    entry = {
        "scenario": active["scenario"],
        "score": score(active),
        "minutes": round((finished_at - active["started_at"]) / 60, 1),
        "hints": active.get("hints_shown", 0),
        "finished_at": finished_at,
    }
    state.setdefault("history", []).append(entry)
    return entry


def best_scores(state):
    best = {}
    for h in state.get("history", []):
        best[h["scenario"]] = max(best.get(h["scenario"], 0), h["score"])
    return best
