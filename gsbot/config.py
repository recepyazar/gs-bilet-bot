"""Ortam değişkenlerinden (veya .env dosyasından) ayarları okur."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: str = ".env"):
    """Bağımlılık eklememek için minimal .env okuyucu. Var olan env'i ezmez."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    token: str
    secret_key: str
    admin_ids: set[int] = field(default_factory=set)
    db_path: str = "data/gsbot.db"
    check_interval: int = 120        # saniye
    captcha_wait: int = 600          # saniye
    healthcheck_url: str = ""         # healthchecks.io ping adresi (isteğe bağlı)

    @classmethod
    def from_env(cls, dotenv: str | None = ".env") -> "Config":
        if dotenv:
            _load_dotenv(dotenv)
        missing = [k for k in ("TELEGRAM_TOKEN", "GS_SECRET_KEY") if not os.environ.get(k, "").strip()]
        if missing:
            raise SystemExit(
                f"Eksik ayar: {', '.join(missing)}. .env.example dosyasına bak.\n"
                "Şifreleme anahtarı üretmek için: python -m gsbot anahtar"
            )
        admins = {
            int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.lstrip("-").isdigit()
        }
        return cls(
            token=os.environ["TELEGRAM_TOKEN"].strip(),
            secret_key=os.environ["GS_SECRET_KEY"].strip(),
            admin_ids=admins,
            db_path=os.environ.get("DB_PATH", "data/gsbot.db"),
            check_interval=int(os.environ.get("CHECK_INTERVAL", "120")),
            captcha_wait=int(os.environ.get("CAPTCHA_WAIT", "600")),
            healthcheck_url=os.environ.get("HEALTHCHECK_URL", "").strip(),
        )
