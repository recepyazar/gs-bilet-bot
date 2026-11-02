"""bot.py akışı: maç kontrolü → bildirim/captcha → gönderim. Telegram ve site taklit edilir."""

import asyncio
from types import SimpleNamespace

import pytest

from gsbot import bot
from gsbot import storage as st
from gsbot.config import Config

FX = {"branch": "1", "id": "9876", "text": "Galatasaray - Fenerbahçe"}


class FakeBot:
    username = "gs_test_bot"

    def __init__(self):
        self.messages, self.photos = [], []

    async def send_message(self, chat_id, text, **kw):
        self.messages.append((chat_id, text, kw.get("reply_markup")))
        return SimpleNamespace(message_id=len(self.messages))

    async def send_photo(self, chat_id, photo, **kw):
        self.photos.append((chat_id, photo, kw.get("caption")))


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_once(self, cb, when, data=None, name=None):
        self.jobs.append(SimpleNamespace(callback=cb, data=data, name=name, schedule_removal=lambda: None))

    def get_jobs_by_name(self, name):
        return [j for j in self.jobs if j.name == name]


class FakeApp:
    def __init__(self, store):
        self.bot = FakeBot()
        self.job_queue = FakeJobQueue()
        self.bot_data = {"rt": bot.Runtime(Config(token="t", secret_key="k", admin_ids={99}), store)}
        self.tasks = []

    def create_task(self, coro):
        self.tasks.append(asyncio.ensure_future(coro))

    async def settle(self):
        while self.tasks:
            await self.tasks.pop(0)


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_html(self, text, **kw):
        self.replies.append(text)


def text_update(chat_id, text):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), message=FakeMessage(text))


@pytest.fixture
def app(store, monkeypatch):
    monkeypatch.setattr(bot.site, "fetch_all_fixtures", lambda: [FX])
    monkeypatch.setattr(bot.site, "prepare_form", lambda: ("SESSION", "TOK", b"IMG"))
    return FakeApp(store)


async def test_auto_mode_sends_captcha_then_submits(app, store, profile, monkeypatch):
    store.save_user(1, "ali", profile, st.MODE_AUTO, "1")
    assert await bot.run_check(app) == 1
    await app.settle()

    assert app.bot.photos and app.bot.photos[0][0] == 1
    assert store.get_application(1, "1:9876")["status"] == st.CAPTCHA

    sent = {}

    def fake_submit(session, token, prof, fx, captcha):
        sent.update(session=session, token=token, tc=prof["tc"], fx=fx, captcha=captcha)
        return True, "Başvurunuz alınmıştır"

    monkeypatch.setattr(bot.site, "submit_and_read", fake_submit)
    upd = text_update(1, "Ab12Cd")
    await bot.on_captcha_text(upd, SimpleNamespace(application=app))

    assert sent == {"session": "SESSION", "token": "TOK", "tc": "10000000146", "fx": FX, "captcha": "Ab12Cd"}
    assert store.get_application(1, "1:9876")["status"] == st.SENT
    assert any("gönderildi" in r for r in upd.message.replies)

    # Bir sonraki kontrolde aynı maç tekrar bildirilmez
    app.bot.messages.clear()
    await bot.run_check(app)
    assert app.bot.messages == []


async def test_ask_mode_waits_for_approval(app, store, profile):
    store.save_user(2, "veli", profile, st.MODE_ASK, "1,2")
    await bot.run_check(app)
    await app.settle()
    assert app.bot.photos == []
    chat_id, text, kb = app.bot.messages[0]
    assert chat_id == 2 and "Başvurayım mı" in text
    assert [b.callback_data for b in kb.inline_keyboard[0]] == ["ap:1:9876", "sk:1:9876"]
    assert store.get_application(2, "1:9876")["status"] == st.ASKED

    # ✅ Başvur → captcha gelir
    answered = []

    async def answer(*a, **kw):
        answered.append(a)

    async def edit_markup(*a, **kw):
        pass

    q = SimpleNamespace(data="ap:1:9876", message=SimpleNamespace(chat=SimpleNamespace(id=2)),
                        answer=answer, edit_message_reply_markup=edit_markup)
    await bot.on_approval(SimpleNamespace(callback_query=q), SimpleNamespace(application=app))
    await app.settle()
    assert app.bot.photos and app.bot.photos[0][0] == 2


async def test_other_branch_and_paused_users_not_notified(app, store, profile):
    store.save_user(3, "a", profile, st.MODE_AUTO, "2")
    store.save_user(4, "b", profile, st.MODE_AUTO, "1")
    store.update_settings(4, active=False)
    await bot.run_check(app)
    await app.settle()
    assert app.bot.messages == [] and app.bot.photos == []


async def test_bad_captcha_text_is_rejected_without_submitting(app, store, profile, monkeypatch):
    store.save_user(1, "ali", profile, st.MODE_AUTO, "1")
    await bot.run_check(app)
    await app.settle()
    monkeypatch.setattr(bot.site, "submit_and_read", lambda *a: pytest.fail("gönderilmemeli"))
    upd = text_update(1, "kısa")
    await bot.on_captcha_text(upd, SimpleNamespace(application=app))
    assert "6 karakter" in upd.message.replies[0]
    assert store.get_application(1, "1:9876")["status"] == st.CAPTCHA


async def test_captcha_expiry_marks_expired_and_offers_retry(app, store, profile):
    store.save_user(1, "ali", profile, st.MODE_AUTO, "1")
    await bot.run_check(app)
    await app.settle()
    job = app.job_queue.jobs[-1]
    await bot.expire_captcha(SimpleNamespace(application=app, job=job))
    assert store.get_application(1, "1:9876")["status"] == st.EXPIRED
    assert 1 not in app.bot_data["rt"].pending
    _, text, kb = app.bot.messages[-1]
    assert "süresi doldu" in text and kb.inline_keyboard[0][0].callback_data == "ap:1:9876"


async def test_failed_form_prepare_is_reported(app, store, profile, monkeypatch):
    def boom():
        raise RuntimeError("site kapalı")
    monkeypatch.setattr(bot.site, "prepare_form", boom)
    store.save_user(1, "ali", profile, st.MODE_AUTO, "1")
    await bot.run_check(app)
    await app.settle()
    assert store.get_application(1, "1:9876")["status"] == st.FAILED
    assert "site kapalı" in app.bot.messages[-1][1]


async def test_site_down_alerts_admin_once_after_three_failures(app, store, monkeypatch):
    def boom():
        raise ConnectionError("yok")
    monkeypatch.setattr(bot.site, "fetch_all_fixtures", boom)
    for _ in range(5):
        assert await bot.run_check(app) is None
    alerts = [m for m in app.bot.messages if m[0] == 99]
    assert len(alerts) == 1


async def test_two_matches_are_queued_one_captcha_at_a_time(app, store, profile, monkeypatch):
    fx2 = {"branch": "1", "id": "5555", "text": "Galatasaray - Trabzonspor"}
    monkeypatch.setattr(bot.site, "fetch_all_fixtures", lambda: [FX, fx2])
    monkeypatch.setattr(bot.site, "submit_and_read", lambda *a: (True, "ok"))
    store.save_user(1, "ali", profile, st.MODE_AUTO, "1")
    await bot.run_check(app)
    await app.settle()
    assert len(app.bot.photos) == 1
    await bot.on_captcha_text(text_update(1, "AAAAAA"), SimpleNamespace(application=app))
    assert len(app.bot.photos) == 2
    assert store.get_application(1, "1:5555")["status"] == st.CAPTCHA


async def test_status_before_and_after_check(app, store, profile):
    r = app.bot_data["rt"]
    assert "henüz kontrol yapılmadı" in bot.status_text(r, 1)
    store.save_user(1, "ali", profile, st.MODE_ASK, "1")
    await bot.run_check(app)
    text = bot.status_text(r, 1)
    assert "🟢" in text and "sn önce" in text and "Fenerbahçe" in text and "önce sor" in text
    assert "Yönetici" not in text
    assert "Kullanıcı: 1 (1 aktif)" in bot.status_text(r, 99)


async def test_status_turns_yellow_when_site_fails(app, monkeypatch):
    r = app.bot_data["rt"]
    await bot.run_check(app)

    def boom():
        raise ConnectionError("yok")
    monkeypatch.setattr(bot.site, "fetch_all_fixtures", boom)
    await bot.run_check(app)
    text = bot.status_text(r, 99)
    assert text.startswith("🟡") and "1 denemede" in text


def test_ago_formats():
    assert bot.ago(5) == "5 sn" and bot.ago(125) == "2 dk" and bot.ago(3700) == "1 sa 1 dk"
    assert bot.ago(90000) == "1 gün 1 sa"


async def test_healthcheck_ping(monkeypatch):
    calls = []
    monkeypatch.setattr(bot.requests, "get", lambda url, timeout: calls.append(url))
    await bot.ping_healthcheck("")
    await bot.ping_healthcheck("https://hc-ping.com/abc")
    assert calls == ["https://hc-ping.com/abc"]

    def boom(*a, **kw):
        raise ConnectionError
    monkeypatch.setattr(bot.requests, "get", boom)
    await bot.ping_healthcheck("https://hc-ping.com/abc")   # çökmemeli
