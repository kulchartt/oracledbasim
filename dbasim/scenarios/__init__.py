from ..db import SetupError
from ..i18n import t
from .s01_missing_index import MissingIndex
from .s02_stale_stats import StaleStats
from .s03_temp_full import TempFull
from .s04_lock_contention import LockContention
from .s05_hard_parse import HardParse

ALL = [MissingIndex(), StaleStats(), TempFull(), LockContention(), HardParse()]
_BY_ID = {s.id: s for s in ALL}


def get(scenario_id):
    try:
        return _BY_ID[scenario_id.lower()]
    except KeyError:
        raise SetupError(t(f"ไม่มีโจทย์ '{scenario_id}' ลอง dbasim list",
                           f"No scenario '{scenario_id}'. Try dbasim list")) from None
