"""dbasim Pro licence checks against a fake Lemon Squeezy License API."""
import pytest

from dbasim import cli, license, state

STORE, PRODUCT = "111", "222"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DBASIM_HOME", str(tmp_path))
    monkeypatch.setenv("DBASIM_LS_STORE_ID", STORE)
    monkeypatch.setenv("DBASIM_LS_PRODUCT_ID", PRODUCT)
    monkeypatch.delenv("DBASIM_LANG", raising=False)
    state.save({**state.load(), "lang": "th"})  # these tests assert on the Thai copy; English is the default
    return tmp_path


def meta(store=STORE, product=PRODUCT):
    return {"store_id": int(store), "product_id": int(product), "variant_name": "Monthly",
            "customer_email": "a@example.com"}


class FakeAPI:
    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def __call__(self, endpoint, fields):
        self.calls.append((endpoint, fields))
        answer = self.answers[endpoint]
        if isinstance(answer, Exception):
            raise answer
        return answer


def activated(status="active", m=None):
    return {"activated": True, "error": None, "license_key": {"status": status},
            "instance": {"id": "inst-1"}, "meta": m or meta()}


def validated(valid=True, status="active"):
    return {"valid": valid, "error": None, "license_key": {"status": status},
            "instance": {"id": "inst-1"}, "meta": meta()}


def test_activate_saves_licence():
    api = FakeAPI(activate=activated())
    lic = license.activate("ABCD-1234-EFGH-5678", post=api)
    assert lic["instance_id"] == "inst-1" and lic["plan"] == "Monthly"
    assert license.load()["key"] == "ABCD-1234-EFGH-5678"


def test_activate_refuses_key_of_another_product_and_frees_the_seat():
    api = FakeAPI(activate=activated(m=meta(product="999")), deactivate={"deactivated": True})
    with pytest.raises(license.LicenseError):
        license.activate("OTHER-KEY-0000-0000", post=api)
    assert [c[0] for c in api.calls] == ["activate", "deactivate"]
    assert license.load() is None


def test_activate_reports_api_error():
    api = FakeAPI(activate={"activated": False, "error": "This license key has reached the activation limit."})
    with pytest.raises(license.LicenseError, match="activation limit"):
        license.activate("FULL-KEY-0000-0000", post=api)
    assert license.load() is None


def test_refuses_when_store_not_configured(monkeypatch):
    monkeypatch.delenv("DBASIM_LS_STORE_ID")
    api = FakeAPI(activate=activated(), deactivate={"deactivated": True})
    with pytest.raises(license.LicenseError):
        license.activate("ABCD-1234-EFGH-5678", post=api)
    assert api.calls == []  # no seat is taken on a key we could not accept


# ---- free-first release: Pro not on sale yet (store IDs unset) --------------

@pytest.fixture
def not_on_sale(monkeypatch):
    monkeypatch.delenv("DBASIM_LS_STORE_ID")
    monkeypatch.delenv("DBASIM_LS_PRODUCT_ID")


def test_pro_scenario_says_coming_soon_when_not_on_sale(not_on_sale, capsys):
    assert cli.main(["start", "s04"]) == 1
    out = capsys.readouterr().out
    assert "เร็วๆ นี้" in out and "s01" in out
    assert "$9" not in out and "dbasim activate" not in out
    assert state.load()["active"] is None


def test_list_and_license_say_coming_soon_when_not_on_sale(not_on_sale, capsys):
    cli.main(["list"])
    assert "เร็วๆ นี้" in capsys.readouterr().out
    assert cli.main(["license"]) == 0
    out = capsys.readouterr().out
    assert "เร็วๆ นี้" in out and "dbasim activate" not in out


def test_coming_soon_in_english(not_on_sale, monkeypatch, capsys):
    monkeypatch.setenv("DBASIM_LANG", "en")
    assert cli.main(["start", "s05"]) == 1
    assert "coming soon" in capsys.readouterr().out


def test_require_pro_without_licence_explains_how_to_buy():
    with pytest.raises(license.LicenseError, match="dbasim activate"):
        license.require_pro(post=FakeAPI())


def test_require_pro_uses_recent_check_without_network():
    license.activate("ABCD-1234-EFGH-5678", post=FakeAPI(activate=activated()))
    api = FakeAPI()  # any call would raise KeyError
    assert license.require_pro(post=api)["status"] == "active"
    assert api.calls == []


def test_expired_subscription_locks_pro(monkeypatch):
    license.activate("ABCD-1234-EFGH-5678", post=FakeAPI(activate=activated()))
    later = license.load()["checked_at"] + license.RECHECK_SECONDS + 1
    with pytest.raises(license.LicenseError, match="หมดอายุ"):
        license.require_pro(post=FakeAPI(validate=validated(False, "expired")), now=later)
    assert license.load()["status"] == "expired"
    # and it stays locked on the next try, even offline
    with pytest.raises(license.LicenseError):
        license.require_pro(post=FakeAPI(validate=license.Offline("down")), now=later + 10)


def test_offline_grace_then_lock():
    license.activate("ABCD-1234-EFGH-5678", post=FakeAPI(activate=activated()))
    checked = license.load()["checked_at"]
    offline = FakeAPI(validate=license.Offline("no route"))
    assert license.require_pro(post=offline, now=checked + 2 * 24 * 3600)
    with pytest.raises(license.LicenseError):
        license.require_pro(post=offline, now=checked + license.OFFLINE_GRACE_SECONDS + 1)


def test_deactivate_forgets_licence():
    license.activate("ABCD-1234-EFGH-5678", post=FakeAPI(activate=activated()))
    api = FakeAPI(deactivate={"deactivated": True})
    assert license.deactivate(post=api) is True
    assert api.calls[0][1]["instance_id"] == "inst-1"
    assert license.load() is None


def test_masked_key_hides_the_middle():
    assert license.masked("ABCD-1234-EFGH-5678") == "ABCD-****-5678"


# ---- CLI gate -------------------------------------------------------------

def test_start_pro_scenario_without_licence_is_refused(capsys):
    assert cli.main(["start", "s04"]) == 1
    out = capsys.readouterr().out
    assert "dbasim Pro" in out and "dbasim activate" in out
    assert state.load()["active"] is None


def test_list_marks_free_and_pro(capsys):
    cli.main(["list"])
    out = capsys.readouterr().out
    rows = {line.split()[0]: line for line in out.splitlines() if line.startswith("s0")}
    assert "ฟรี" in rows["s01"] and "Pro" in rows["s04"] and "Pro" in rows["s05"]


def test_license_command_without_licence(capsys):
    assert cli.main(["license"]) == 0
    assert "dbasim activate" in capsys.readouterr().out


# ---- language ------------------------------------------------------------

def test_english_via_env(monkeypatch, capsys):
    monkeypatch.setenv("DBASIM_LANG", "en")
    cli.main(["list"])
    out = capsys.readouterr().out
    assert "Scenario" in out and "Free" in out
    assert not any("฀" <= ch <= "๿" for ch in out), out  # no Thai left


def test_lang_command_persists(capsys):
    cli.main(["lang", "en"])
    assert state.load()["lang"] == "en"
    assert "Language: English" in capsys.readouterr().out
    cli.main(["lang", "th"])
    assert "ภาษา: ไทย" in capsys.readouterr().out


def test_every_scenario_has_english_text(monkeypatch):
    from dbasim.scenarios import ALL
    monkeypatch.setenv("DBASIM_LANG", "en")
    for s in ALL:
        texts = [str(s.title), str(s.difficulty), str(s.story), str(s.solution)] + [str(h) for h in s.hints]
        for text in texts:
            assert text and not any("฀" <= ch <= "๿" for ch in text), (s.id, text[:60])


def test_default_language_is_english(tmp_path, monkeypatch, capsys):
    from dbasim import i18n
    monkeypatch.setenv("DBASIM_HOME", str(tmp_path / "fresh-install"))
    monkeypatch.delenv("DBASIM_LANG", raising=False)
    assert i18n.lang() == "en"
    for argv in (["list"], ["lang"], ["status"], ["license"]):
        cli.main(argv)
    out = capsys.readouterr().out
    assert "Scenario" in out and "Language: English" in out
    assert not any(0x0E00 <= ord(ch) <= 0x0E7F for ch in out), out  # no Thai left


def test_unknown_language_falls_back_to_english(monkeypatch):
    from dbasim import i18n
    monkeypatch.setenv("DBASIM_LANG", "xx")
    assert i18n.lang() == "en"
