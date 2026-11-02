"""Kullanıcı cevaplarını doğrulayan saf fonksiyonlar. Geçersizse None döner."""

import re

from .site import BRANCH_NAMES

YES_WORDS = ("evet", "e", "yes", "y", "var", "true", "1", "✅ evet")
NO_WORDS = ("hayır", "hayir", "h", "no", "n", "yok", "false", "0", "❌ hayır")
SKIP_WORDS = ("atla", "yok", "-", "gec", "geç", "⏭ atla")


def valid_name(value: str) -> str | None:
    value = " ".join(value.split())
    if not (2 <= len(value) <= 50):
        return None
    if not re.fullmatch(r"[A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû' .-]+", value):
        return None
    return value


def valid_tc(value: str) -> str | None:
    if re.search(r"[^\d\s]", value):
        return None
    d = re.sub(r"\D", "", value)
    if len(d) != 11 or d[0] == "0":
        return None
    n = [int(c) for c in d]
    if (sum(n[0:9:2]) * 7 - sum(n[1:8:2])) % 10 != n[9]:
        return None
    if sum(n[:10]) % 10 != n[10]:
        return None
    return d


def valid_phone(value: str) -> str | None:
    d = re.sub(r"\D", "", value)
    if len(d) == 12 and d.startswith("90"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if len(d) == 10 and d.startswith("5") else None


def valid_birthdate(value: str) -> tuple[str, str, str] | None:
    parts = re.split(r"[.\-/\s]+", value.strip())
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    day, month, year = (int(p) for p in parts)
    if not (1 <= day <= 31 and 1 <= month <= 12 and 1920 <= year <= 2013):
        return None
    return str(day), str(month), str(year)


def valid_yes_no(value: str) -> bool | None:
    answer = value.strip().lower()
    if answer in YES_WORDS:
        return True
    if answer in NO_WORDS:
        return False
    return None


def valid_branches(value: str) -> str | None:
    picked = [b.strip() for b in re.split(r"[,\s]+", value.strip()) if b.strip()]
    if not picked or any(b not in BRANCH_NAMES for b in picked):
        return None
    return ",".join(sorted(dict.fromkeys(picked)))


def valid_captcha(value: str) -> str | None:
    value = value.strip()
    return value if re.fullmatch(r"[A-Za-z0-9]{6}", value) else None


def is_skip(value: str) -> bool:
    return value.strip().lower() in SKIP_WORDS


def mask(value: str) -> str:
    """TC/telefon gibi değerleri özet ve log için maskeler: 105******46"""
    if not value:
        return ""
    if len(value) <= 5:
        return "*" * len(value)
    return value[:3] + "*" * (len(value) - 5) + value[-2:]
