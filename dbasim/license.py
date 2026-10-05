"""dbasim Pro licence: a Lemon Squeezy subscription licence key, checked against the
public License API (no secret key in the client, no server of our own).

The key stays valid while the subscription is paid; Lemon Squeezy marks it expired or
disabled once the subscription ends, and the next online check locks Pro scenarios again.
Saved in ~/.dbasim/license.json.
"""
import json
import os
import platform
import time
import urllib.error
import urllib.parse
import urllib.request

from . import state
from .i18n import t

API = "https://api.lemonsqueezy.com/v1/licenses/"

# Fill these in from the Lemon Squeezy dashboard before release (see SELLING.md).
# A key from any other store or product is refused, so they must be set.
STORE_ID = None
PRODUCT_ID = None
PRICING_URL = "https://dbasim.example/#pricing"

RECHECK_SECONDS = 24 * 3600          # online re-check at most once a day
OFFLINE_GRACE_SECONDS = 7 * 24 * 3600  # keep working this long without internet


class LicenseError(Exception):
    pass


def _ids():
    store = os.environ.get("DBASIM_LS_STORE_ID") or STORE_ID
    product = os.environ.get("DBASIM_LS_PRODUCT_ID") or PRODUCT_ID
    return (str(store) if store else None, str(product) if product else None)


def pricing_url():
    return os.environ.get("DBASIM_PRICING_URL") or PRICING_URL


def _path():
    return state.home() / "license.json"


def load():
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save(lic):
    _path().write_text(json.dumps(lic, indent=2), encoding="utf-8")


def _forget():
    try:
        _path().unlink()
    except FileNotFoundError:
        pass


def masked(key):
    return key[:4] + "-****-" + key[-4:] if len(key) > 8 else "****"


class Offline(Exception):
    pass


def _post(endpoint, fields):
    """POST to the License API. Returns the JSON body (also for 4xx answers)."""
    req = urllib.request.Request(
        API + endpoint,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Accept": "application/json", "User-Agent": "dbasim"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except (ValueError, OSError):
            raise Offline(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Offline(str(exc)) from None


def _check_product(meta):
    store, product = _ids()
    if not store or not product:
        raise LicenseError(t(
            "dbasim Pro ยังไม่เปิดขาย (ยังไม่ได้ตั้งค่าร้านค้า)",
            "dbasim Pro is not on sale yet (store not configured)",
        ))
    meta = meta or {}
    if str(meta.get("store_id")) != store or str(meta.get("product_id")) != product:
        raise LicenseError(t("license key นี้ไม่ใช่ของ dbasim Pro", "This licence key is not for dbasim Pro"))


def _status_problem(lic_key):
    status = (lic_key or {}).get("status")
    if status == "expired":
        return t("subscription หมดอายุแล้ว ต่ออายุแล้วสั่ง dbasim license --refresh",
                 "Your subscription has expired. Renew it, then run: dbasim license --refresh")
    if status == "disabled":
        return t("license key นี้ถูกระงับ (subscription ถูกยกเลิกหรือคืนเงินแล้ว)",
                 "This licence key is disabled (subscription cancelled or refunded)")
    return t(f"license key ใช้ไม่ได้ (สถานะ: {status})", f"Licence key is not usable (status: {status})")


def activate(key, post=_post):
    key = key.strip()
    if not key:
        raise LicenseError(t("ใส่ license key ด้วย", "Please give a licence key"))
    old = load()
    if old and old.get("key") == key and old.get("instance_id"):
        return refresh(post=post)
    try:
        body = post("activate", {"license_key": key,
                                 "instance_name": f"dbasim on {platform.node() or 'computer'}"})
    except Offline as exc:
        raise LicenseError(t(f"ต่อ Lemon Squeezy ไม่ได้ ลองใหม่เมื่อมีอินเทอร์เน็ต ({exc})",
                             f"Could not reach Lemon Squeezy. Try again when online ({exc})"))
    if not body.get("activated"):
        raise LicenseError(t(f"เปิดใช้ไม่สำเร็จ: {body.get('error') or 'ไม่ทราบสาเหตุ'}",
                             f"Activation failed: {body.get('error') or 'unknown error'}"))
    instance_id = (body.get("instance") or {}).get("id")
    try:
        _check_product(body.get("meta"))
    except LicenseError:
        # do not keep a seat on someone else's product
        try:
            post("deactivate", {"license_key": key, "instance_id": instance_id})
        except Offline:
            pass
        raise
    if (body.get("license_key") or {}).get("status") != "active":
        raise LicenseError(_status_problem(body.get("license_key")))
    meta = body.get("meta") or {}
    lic = {"key": key, "instance_id": instance_id, "plan": meta.get("variant_name"),
           "email": meta.get("customer_email"), "status": "active", "checked_at": time.time()}
    _save(lic)
    return lic


def refresh(post=_post):
    """Ask Lemon Squeezy whether the saved key is still valid. Raises LicenseError if not."""
    lic = load()
    if not lic:
        raise LicenseError(t("ยังไม่ได้เปิดใช้ dbasim Pro", "dbasim Pro is not activated"))
    body = post("validate", {"license_key": lic["key"], "instance_id": lic.get("instance_id") or ""})
    lic_key = body.get("license_key")
    if not body.get("valid") or (lic_key or {}).get("status") != "active":
        lic["status"] = (lic_key or {}).get("status") or "invalid"
        lic["checked_at"] = time.time()
        _save(lic)
        if body.get("error") and not lic_key:
            raise LicenseError(t(f"license ใช้ไม่ได้: {body['error']}", f"Licence not valid: {body['error']}"))
        raise LicenseError(_status_problem(lic_key))
    _check_product(body.get("meta"))
    lic.update(status="active", checked_at=time.time(), plan=(body.get("meta") or {}).get("variant_name"))
    _save(lic)
    return lic


def require_pro(post=_post, now=None):
    """Return the licence when Pro may be used, else raise LicenseError with what to do next."""
    now = now or time.time()
    lic = load()
    if not lic:
        raise LicenseError(need_pro_message())
    fresh = lic.get("status") == "active" and now - lic.get("checked_at", 0) < RECHECK_SECONDS
    if fresh:
        return lic
    try:
        return refresh(post=post)
    except Offline:
        if lic.get("status") == "active" and now - lic.get("checked_at", 0) < OFFLINE_GRACE_SECONDS:
            return lic
        raise LicenseError(t(
            "ตรวจ license ไม่ได้เพราะไม่มีอินเทอร์เน็ตนานเกิน 7 วัน ต่อเน็ตแล้วลองใหม่",
            "Could not check your licence: offline for more than 7 days. Go online and try again",
        ))


def deactivate(post=_post):
    lic = load()
    if not lic:
        return False
    try:
        post("deactivate", {"license_key": lic["key"], "instance_id": lic.get("instance_id") or ""})
    except Offline as exc:
        raise LicenseError(t(f"ต่อ Lemon Squeezy ไม่ได้ ({exc}) ยังไม่ได้ถอดเครื่องนี้ออก",
                             f"Could not reach Lemon Squeezy ({exc}); this computer is still activated"))
    _forget()
    return True


def need_pro_message():
    return t(
        "โจทย์นี้อยู่ใน dbasim Pro (3 โจทย์แรกเล่นฟรี)\n"
        f"  สมัคร: {pricing_url()}  รายเดือน $9 หรือรายปี $79\n"
        "  ได้ license key ทางอีเมลแล้วสั่ง: dbasim activate <license-key>",
        "This scenario is part of dbasim Pro (the first 3 scenarios are free).\n"
        f"  Subscribe: {pricing_url()}  $9/month or $79/year\n"
        "  You will get a licence key by email, then run: dbasim activate <licence-key>",
    )
