"""SQLite depolama. Kişisel bilgiler (profil) Fernet ile şifreli saklanır.

Tablolar
  users         chat_id başına şifreli profil + ayarlar (mod, branşlar, aktif)
  invites       davet kodları
  applications  kullanıcı × karşılaşma: o maç için ne yapıldı (bildirildi/gönderildi/...)

`applications` satırı varsa kullanıcı o maç için bir daha bildirilmez.
"""

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .site import ALL_BRANCHES

MODE_AUTO = "otomatik"   # maç açılınca form hemen hazırlanır, captcha gelir
MODE_ASK = "onay"        # önce "başvurayım mı?" diye sorulur

# applications.status
ASKED = "soruldu"        # onay modunda düğmeler gönderildi
QUEUED = "sirada"        # captcha sırasında
CAPTCHA = "captcha"      # captcha resmi gönderildi, cevap bekleniyor
SENT = "gonderildi"
FAILED = "hata"
SKIPPED = "atlandi"
EXPIRED = "zaman_asimi"
CLOSED = "liste_cikti"   # liste yayınlandı, başvurmadan kapandı
WON = "listede"          # gönderildi ve adı listede
LOST = "listede_yok"     # gönderildi ama adı listede yok

RETRYABLE = (ASKED, FAILED, EXPIRED, SKIPPED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id     INTEGER PRIMARY KEY,
    username    TEXT,
    profile     BLOB    NOT NULL,
    mode        TEXT    NOT NULL DEFAULT 'onay',
    branches    TEXT    NOT NULL DEFAULT '1,2,3,4,5,6',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS invites (
    code        TEXT PRIMARY KEY,
    created_by  INTEGER NOT NULL,
    created_at  INTEGER NOT NULL,
    used_by     INTEGER,
    used_at     INTEGER
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS news (
    id           INTEGER PRIMARY KEY,
    title        TEXT    NOT NULL,
    is_list      INTEGER NOT NULL,
    processed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS closed_fixtures (
    fixture_key TEXT PRIMARY KEY,
    news_id     INTEGER NOT NULL,
    closed_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    chat_id      INTEGER NOT NULL,
    fixture_key  TEXT    NOT NULL,
    branch       TEXT    NOT NULL,
    fixture_id   TEXT    NOT NULL,
    fixture_text TEXT    NOT NULL,
    status       TEXT    NOT NULL,
    detail       TEXT,
    updated_at   INTEGER NOT NULL,
    PRIMARY KEY (chat_id, fixture_key)
);
"""


@dataclass
class User:
    chat_id: int
    username: str | None
    profile: dict
    mode: str
    branches: list[str]
    active: bool

    @property
    def auto(self) -> bool:
        return self.mode == MODE_AUTO


class Storage:
    def __init__(self, path: str, secret_key: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()
        try:
            self.fernet = Fernet(secret_key.encode() if isinstance(secret_key, str) else secret_key)
        except (ValueError, TypeError) as e:
            raise SystemExit(
                "GS_SECRET_KEY geçerli bir Fernet anahtarı değil. "
                "Üretmek için: python -m gsbot anahtar"
            ) from e

    # ------------------------------------------------------------ şifreleme

    def _encrypt(self, profile: dict) -> bytes:
        return self.fernet.encrypt(json.dumps(profile, ensure_ascii=False).encode("utf-8"))

    def _decrypt(self, blob: bytes) -> dict:
        try:
            return json.loads(self.fernet.decrypt(blob).decode("utf-8"))
        except InvalidToken as e:
            raise RuntimeError("Profil çözülemedi: GS_SECRET_KEY değişmiş olabilir.") from e

    def _user(self, row) -> User:
        return User(
            chat_id=row["chat_id"],
            username=row["username"],
            profile=self._decrypt(row["profile"]),
            mode=row["mode"],
            branches=[b for b in row["branches"].split(",") if b],
            active=bool(row["active"]),
        )

    # ---------------------------------------------------------- kullanıcılar

    def save_user(self, chat_id: int, username: str | None, profile: dict,
                  mode: str = MODE_ASK, branches: str = ALL_BRANCHES):
        now = int(time.time())
        self.db.execute(
            """INSERT INTO users (chat_id, username, profile, mode, branches, active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 username=excluded.username, profile=excluded.profile, mode=excluded.mode,
                 branches=excluded.branches, active=1, updated_at=excluded.updated_at""",
            (chat_id, username, self._encrypt(profile), mode, branches, now, now),
        )
        self.db.commit()

    def get_user(self, chat_id: int) -> User | None:
        row = self.db.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        return self._user(row) if row else None

    def is_registered(self, chat_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM users WHERE chat_id=?", (chat_id,)).fetchone() is not None

    def active_users(self) -> list[User]:
        rows = self.db.execute("SELECT * FROM users WHERE active=1").fetchall()
        return [self._user(r) for r in rows]

    def list_users(self) -> list[dict]:
        """Admin listesi — kişisel veri içermez."""
        rows = self.db.execute(
            "SELECT chat_id, username, mode, branches, active, created_at FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_settings(self, chat_id: int, *, mode: str | None = None,
                        branches: str | None = None, active: bool | None = None):
        sets, args = [], []
        if mode is not None:
            sets.append("mode=?"); args.append(mode)
        if branches is not None:
            sets.append("branches=?"); args.append(branches)
        if active is not None:
            sets.append("active=?"); args.append(int(active))
        if not sets:
            return
        sets.append("updated_at=?"); args.append(int(time.time()))
        self.db.execute(f"UPDATE users SET {', '.join(sets)} WHERE chat_id=?", (*args, chat_id))
        self.db.commit()

    def delete_user(self, chat_id: int):
        self.db.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
        self.db.execute("DELETE FROM applications WHERE chat_id=?", (chat_id,))
        self.db.commit()

    # --------------------------------------------------------------- davetler

    def create_invite(self, admin_id: int) -> str:
        code = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")
        self.db.execute(
            "INSERT INTO invites (code, created_by, created_at) VALUES (?, ?, ?)",
            (code, admin_id, int(time.time())),
        )
        self.db.commit()
        return code

    def invite_valid(self, code: str) -> bool:
        row = self.db.execute("SELECT used_by FROM invites WHERE code=?", (code.strip(),)).fetchone()
        return row is not None and row["used_by"] is None

    def use_invite(self, code: str, chat_id: int) -> bool:
        cur = self.db.execute(
            "UPDATE invites SET used_by=?, used_at=? WHERE code=? AND used_by IS NULL",
            (chat_id, int(time.time()), code.strip()),
        )
        self.db.commit()
        return cur.rowcount == 1

    # ------------------------------------------------------------ başvurular

    def application_keys(self) -> set[tuple[int, str]]:
        rows = self.db.execute("SELECT chat_id, fixture_key FROM applications").fetchall()
        return {(r["chat_id"], r["fixture_key"]) for r in rows}

    def set_status(self, chat_id: int, fixture: dict, status: str, detail: str | None = None):
        self.db.execute(
            """INSERT INTO applications (chat_id, fixture_key, branch, fixture_id, fixture_text, status, detail, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, fixture_key) DO UPDATE SET
                 status=excluded.status, detail=excluded.detail, updated_at=excluded.updated_at""",
            (chat_id, f"{fixture['branch']}:{fixture['id']}", fixture["branch"], fixture["id"],
             fixture["text"], status, detail, int(time.time())),
        )
        self.db.commit()

    def get_application(self, chat_id: int, key: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM applications WHERE chat_id=? AND fixture_key=?", (chat_id, key)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["fixture"] = {"branch": d["branch"], "id": d["fixture_id"], "text": d["fixture_text"]}
        return d

    def recent_applications(self, chat_id: int, limit: int = 10) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM applications WHERE chat_id=? ORDER BY updated_at DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def applications_for(self, key: str) -> list[dict]:
        rows = self.db.execute("SELECT * FROM applications WHERE fixture_key=?", (key,)).fetchall()
        return [dict(r) for r in rows]

    def known_fixtures(self) -> list[dict]:
        """Başvuru tablosunda geçen (bildirilmiş) tüm karşılaşmalar."""
        rows = self.db.execute(
            "SELECT DISTINCT branch, fixture_id, fixture_text FROM applications"
        ).fetchall()
        return [{"branch": r["branch"], "id": r["fixture_id"], "text": r["fixture_text"]} for r in rows]

    def all_users(self) -> list[User]:
        return [self._user(r) for r in self.db.execute("SELECT * FROM users").fetchall()]

    # ------------------------------------------------------------- duyurular

    def get_kv(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_kv(self, key: str, value: str):
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))
        self.db.commit()

    def news_processed(self, news_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM news WHERE id=?", (news_id,)).fetchone() is not None

    def mark_news(self, news_id: int, title: str, is_list: bool):
        self.db.execute("INSERT OR IGNORE INTO news (id, title, is_list, processed_at) VALUES (?, ?, ?, ?)",
                        (news_id, title, int(is_list), int(time.time())))
        self.db.commit()

    def close_fixture(self, key: str, news_id: int):
        self.db.execute("INSERT OR IGNORE INTO closed_fixtures (fixture_key, news_id, closed_at) VALUES (?, ?, ?)",
                        (key, news_id, int(time.time())))
        self.db.commit()

    def closed_keys(self) -> set[str]:
        return {r["fixture_key"] for r in self.db.execute("SELECT fixture_key FROM closed_fixtures").fetchall()}

    def reset_inflight(self) -> int:
        """Bot yeniden başlarken yarım kalan (sırada/captcha) başvuruları siler.

        Captcha oturumu bellekte tutulduğu için yeniden başlayınca kaybolur;
        satırı silmek, maç hâlâ açıksa bir sonraki kontrolde yeniden ele alınmasını sağlar.
        """
        cur = self.db.execute("DELETE FROM applications WHERE status IN (?, ?)", (QUEUED, CAPTCHA))
        self.db.commit()
        return cur.rowcount


def plan_notifications(users: list[User], fixtures: list[dict],
                       done: set[tuple[int, str]], closed: set[str] = frozenset()) -> list[tuple[User, dict]]:
    """Hangi kullanıcıya hangi karşılaşmanın bildirileceğini hesaplar.

    Listesi yayınlanmış (closed) maçlar form açık olsa bile atlanır.
    """
    fixtures = [f for f in fixtures if f"{f['branch']}:{f['id']}" not in closed]
    out = []
    for user in users:
        if not user.active:
            continue
        for fx in fixtures:
            key = f"{fx['branch']}:{fx['id']}"
            if fx["branch"] in user.branches and (user.chat_id, key) not in done:
                out.append((user, fx))
    return out
