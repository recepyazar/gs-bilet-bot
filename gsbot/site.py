"""galatasaray.org engelli bilet formu istemcisi.

Maçlar sayfanın statik HTML'inde değil, POST /disabledticked/fixtures
AJAX ucundan branşa göre JSON olarak gelir. Form ASP.NET anti-forgery
token'ı ve oturuma bağlı 6 karakterlik bir görsel captcha kullanır;
bu yüzden her başvuru kendi requests.Session'ı ile hazırlanır.

Bu modüldeki her şey senkron (requests); bot tarafı asyncio.to_thread ile çağırır.
"""

import html
import json
import logging
import re

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE = "https://www.galatasaray.org"
FORM_URL = f"{BASE}/engelli-bilet-formu"
FIXTURES_URL = f"{BASE}/disabledticked/fixtures"
CAPTCHA_URL = f"{BASE}/captcha/17"

BRANCH_NAMES = {
    "1": "Futbol",
    "2": "Basketbol Erkek",
    "3": "Basketbol Kadın",
    "4": "Basketbol Tekerlekli",
    "5": "Voleybol Erkek",
    "6": "Voleybol Kadın",
}
ALL_BRANCHES = ",".join(BRANCH_NAMES)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}


# --------------------------------------------------------------------------
# Karşılaşmalar
# --------------------------------------------------------------------------

def fixture_key(fixture: dict) -> str:
    return f"{fixture['branch']}:{fixture['id']}"


def fetch_fixtures(session: requests.Session, branch: str) -> list[dict]:
    """Bir branş için açık karşılaşmaları döndürür."""
    r = session.post(
        FIXTURES_URL,
        data={"branch": branch},
        headers={**HEADERS, "X-Requested-With": "XMLHttpRequest", "Referer": FORM_URL},
        timeout=20,
    )
    r.raise_for_status()
    try:
        data = r.json()
    except json.JSONDecodeError:
        log.warning("branch=%s JSON değil, atlanıyor: %r", branch, r.text[:120])
        return []
    if not isinstance(data, list):
        return []
    fixtures = []
    for obj in data:
        if not isinstance(obj, dict):
            continue
        value = str(obj.get("Value", "")).strip()
        text = html.unescape(str(obj.get("Text", ""))).strip()
        if value and text:
            fixtures.append({"branch": branch, "id": value, "text": text})
    return fixtures


def fetch_all_fixtures(branches=None, session: requests.Session | None = None) -> list[dict]:
    session = session or requests.Session()
    fixtures = []
    for branch in branches or BRANCH_NAMES:
        fixtures.extend(fetch_fixtures(session, branch))
    return fixtures


# --------------------------------------------------------------------------
# Form
# --------------------------------------------------------------------------

def load_form_page(session: requests.Session) -> str:
    """Formu çeker, anti-forgery token'ı döndürür (cookie'ler session'a yazılır)."""
    r = session.get(FORM_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    token = soup.find("input", {"name": "__RequestVerificationToken"})
    if not token or not token.get("value"):
        raise RuntimeError("__RequestVerificationToken bulunamadı, form değişmiş olabilir.")
    return token["value"]


def fetch_captcha(session: requests.Session) -> bytes:
    r = session.get(CAPTCHA_URL, headers={**HEADERS, "Referer": FORM_URL}, timeout=20)
    r.raise_for_status()
    return r.content


def prepare_form(session: requests.Session | None = None) -> tuple[requests.Session, str, bytes]:
    """Yeni bir oturum açar: (session, token, captcha resmi)."""
    session = session or requests.Session()
    token = load_form_page(session)
    image = fetch_captcha(session)
    return session, token, image


def format_phone(raw: str) -> str:
    """Sitenin beklediği 0(5xx) xxx xx xx maskesine çevirir."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:          # 5321234567
        digits = "0" + digits
    elif len(digits) == 12 and digits.startswith("90"):
        digits = "0" + digits[2:]
    if len(digits) != 11:
        return raw or ""
    return f"{digits[0]}({digits[1:4]}) {digits[4:7]} {digits[7:9]} {digits[9:11]}"


def build_payload(token: str, profile: dict, fixture: dict, captcha: str) -> list[tuple[str, str]]:
    """Formun POST gövdesi. `profile` storage'daki kullanıcı profilidir."""
    attendant = bool(profile.get("attendant"))
    pairs = [
        ("__RequestVerificationToken", token),
        ("Name", profile["name"]),
        ("Surname", profile["surname"]),
        ("TCIdentityNumber", profile["tc"]),
        ("Phone", format_phone(profile.get("phone", ""))),
        ("BOIBSerailNumber", profile.get("boib", "")),
        ("AttendantTCIdentityNumber", profile.get("attendant_tc", "") if attendant else ""),
        ("AttendantNameSurname", profile.get("attendant_name", "") if attendant else ""),
        ("BirthDateDay", profile["birth_day"]),
        ("BirthDateMonth", profile["birth_month"]),
        ("BirthDateYear", profile["birth_year"]),
        ("Branch", fixture["branch"]),
        ("FixtureId", fixture["id"]),
        ("Captcha", captcha),
    ]
    # Checkbox + hidden ikilisi: işaretliyse "true,false", değilse sadece "false"
    # (ASP.NET model binder ilk değeri alır)
    if attendant:
        pairs.append(("IsAttendantRequested", "true"))
    pairs.append(("IsAttendantRequested", "false"))
    return pairs


def submit_form(session: requests.Session, token: str, profile: dict, fixture: dict, captcha: str):
    r = session.post(
        FORM_URL,
        data=build_payload(token, profile, fixture, captcha),
        headers={**HEADERS, "Referer": FORM_URL, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    r.raise_for_status()
    return r


def read_result(response: requests.Response) -> tuple[bool, str]:
    """Dönen sayfadan başarı/hata durumunu çıkarmaya çalışır."""
    soup = BeautifulSoup(response.text, "html.parser")
    errors = [
        e.get_text(strip=True)
        for e in soup.select(".validation-error, .field-validation-error, .validation-summary-errors")
        if e.get_text(strip=True)
    ]
    if errors:
        return False, " | ".join(dict.fromkeys(errors))[:500]

    form_shown = soup.find("input", {"name": "TCIdentityNumber"}) is not None
    text = content_text(soup)
    hit = SUCCESS_RE.search(text)

    # Form hâlâ sayfadaysa gönderim kabul edilmemiş demektir (başarı mesajı yoksa)
    if form_shown:
        if hit:
            return True, hit.group(0).strip()[:300]
        return False, "Form tekrar gösterildi, gönderim onaylanmadı."
    if hit:
        return True, hit.group(0).strip()[:300]
    return True, text[:300] if text else "Form gönderildi."


SUCCESS_RE = re.compile(r"[^.!]{0,160}(başar|teşekkür|alınmış|alındı|kaydedil|iletil)[^.!]{0,160}[.!]?", re.I)
PAGE_TITLE = "GALATASARAY GAZİ VE ENGELLİ BİLET FORMU"


def content_text(soup: BeautifulSoup) -> str:
    """Sayfanın sadece içerik kutusundaki metni (menü, alt bilgi ve form hariç)."""
    box = (soup.select_one(".single_content_full")
           or soup.select_one(".register_all_content")
           or soup.body or soup)
    for tag in box(["script", "style", "form", "nav", "header", "footer"]):
        tag.decompose()
    text = box.get_text(" ", strip=True)
    if text.upper().startswith(PAGE_TITLE):
        text = text[len(PAGE_TITLE):].strip()
    return text


def submit_and_read(session, token, profile, fixture, captcha) -> tuple[bool, str]:
    return read_result(submit_form(session, token, profile, fixture, captcha))
