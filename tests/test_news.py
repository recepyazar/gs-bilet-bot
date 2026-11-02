"""news.py ve liste duyurusu akışı — ağa çıkılmaz. İsimler uydurmadır."""

from types import SimpleNamespace

import pytest

from conftest import FakeSession, make_response
from gsbot import bot, news
from gsbot import storage as st
from test_bot_flow import FakeApp


def page(title, body_lines=(), next_id=None):
    nxt = f'<a href="/haber/x/y/{next_id}">SONRAKİ HABER</a>' if next_id else ""
    body = "".join(f"<p>{l}</p>" for l in body_lines)
    return (f"<html><head><title>{title} - GALATASARAY.ORG</title></head><body>"
            f"<div class='menu'>GALATASARAY.ORG</div><div class='single_content_full'>"
            f"<p>3 Kasım 2026 Salı 10:00</p><p>{title}</p>{body}"
            f"<a href='https://twitter.com/share'>Tweet</a>"
            f"<a href='/haber/x/y/1'>ÖNCEKİ HABER</a>{nxt}</div></body></html>")


LIST_TITLE = "VfB Stuttgart maçı gazi ve engelli taraftar bilet listesi"
INTRO = ("UEFA Şampiyonlar Ligi'nde 3 Kasım Salı günü oynayacağımız VfB Stuttgart maçında yer almaya "
         "hak kazanan gazi ve engelli taraftarlarımızın listesi aşağıdaki gibidir.")
NAMES = ["ALİ YILMAZ", "AYŞE\xa0 KAYA", "Mehmet Can DEMİR", "ZEYNEP ŞAHİN"]
STUTTGART = {"branch": "1", "id": "4431", "text": "3 Kasım 2026 Salı 20:45 Galatasaray - Stuttgart"}


def list_article(next_id=None):
    return news.parse_article(page(LIST_TITLE, [INTRO, *NAMES], next_id), 61300)


# ---------------------------------------------------------------- ayrıştırma

def test_parse_list_article():
    a = list_article(next_id=61301)
    assert a.title == LIST_TITLE and a.is_winner_list and a.next_id == 61301
    assert news.list_names(a) == {"ALİ YILMAZ", "AYŞE KAYA", "MEHMET CAN DEMİR", "ZEYNEP ŞAHİN"}


def test_not_found_and_regular_news():
    assert news.parse_article("<title>SAYFA BULUNAMADI! - GALATASARAY.ORG</title>", 1) is None
    a = news.parse_article(page("VfB Stuttgart maçı gazi ve engelli taraftar bilet başvurusu", ["Form açıldı."]), 2)
    assert a and not a.is_winner_list


def test_tr_upper_and_date():
    assert news.tr_upper("şükrü  ılgaz\xa0iğdır") == "ŞÜKRÜ ILGAZ İĞDIR"
    assert news.parse_date(INTRO) == (3, 11)
    assert news.parse_date("26 Ekim 2026 Pazartesi") == (26, 10)
    assert news.parse_date("tarih yok") is None


@pytest.mark.parametrize("name,surname,expected", [
    ("Ali", "Yılmaz", "exact"),
    ("ayşe", "kaya", "exact"),
    ("Mehmet", "Demir", "similar"),     # listede ikinci ad var
    ("Ali", "Kaya", None),
])
def test_match_person(name, surname, expected):
    names = news.list_names(list_article())
    assert news.match_person({"name": name, "surname": surname}, names) == expected


def test_fixture_matching_uses_opponent_and_date():
    a = list_article()
    assert news.fixture_matches(a, STUTTGART["text"])
    assert not news.fixture_matches(a, "26 Ekim 2026 Pazartesi Galatasaray - Fenerbahçe")
    assert not news.fixture_matches(a, "10 Kasım 2026 Galatasaray - Stuttgart")   # rövanş/farklı gün


def test_fetch_new_articles_follows_next_chain():
    session = FakeSession([
        make_response(page("Eski duyuru", [], next_id=61300)),
        make_response(page(LIST_TITLE, [INTRO, *NAMES], next_id=61301)),
        make_response(page("Başka haber", [])),
    ])
    found, cursor = news.fetch_new_articles(61299, session=session)
    assert [a.id for a in found] == [61300, 61301] and cursor == 61301
    assert session.calls[0][1].endswith("/haber/61299")


def test_fetch_new_articles_no_next_keeps_cursor():
    found, cursor = news.fetch_new_articles(5, session=FakeSession([make_response(page("Son", []))]))
    assert found == [] and cursor == 5


# ---------------------------------------------------------------- bot akışı

@pytest.fixture
def app(store, monkeypatch):
    monkeypatch.setattr(bot.site, "fetch_all_fixtures", lambda: [STUTTGART])
    monkeypatch.setattr(bot.site, "prepare_form", lambda: ("SESSION", "TOK", b"IMG"))
    return FakeApp(store)


def user_profile(profile, name, surname):
    return {**profile, "name": name, "surname": surname}


async def test_winner_list_notifies_and_closes_fixture(app, store, profile, monkeypatch):
    store.save_user(1, "ali", user_profile(profile, "Ali", "Yılmaz"), st.MODE_ASK, "1")     # başvurdu, listede
    store.save_user(2, "veli", user_profile(profile, "Veli", "Kurt"), st.MODE_ASK, "1")     # başvurdu, yok
    store.save_user(3, "ayse", user_profile(profile, "Ayşe", "Kaya"), st.MODE_ASK, "1")     # bot dışında başvurmuş
    store.save_user(4, "can", user_profile(profile, "Can", "Er"), st.MODE_ASK, "1")         # onay bekliyor
    for chat in (1, 2):
        store.set_status(chat, STUTTGART, st.SENT)
    store.set_status(4, STUTTGART, st.ASKED)

    monkeypatch.setattr(bot.news, "fetch_new_articles", lambda cursor: ([list_article()], 61300))
    await bot.run_news_check(app)

    msgs = {}
    for chat_id, text, _ in app.bot.messages:
        msgs.setdefault(chat_id, []).append(text)
    assert "Listede adın var" in msgs[1][0]
    assert "listede yoksun" in msgs[2][0]
    assert "Listede adın var" in msgs[3][0]
    assert "başvuru kapandı" in msgs[4][0]
    assert "kullanıcılardan 2 kişi" in msgs[99][0]

    assert store.get_application(1, "1:4431")["status"] == st.WON
    assert store.get_application(2, "1:4431")["status"] == st.LOST
    assert store.get_application(4, "1:4431")["status"] == st.CLOSED
    assert store.closed_keys() == {"1:4431"}
    assert store.get_kv("news_cursor") == "61300"

    # Aynı duyuru ikinci kez işlenmez
    app.bot.messages.clear()
    await bot.run_news_check(app)
    assert app.bot.messages == []


async def test_closed_fixture_is_bypassed_even_if_form_open(app, store, profile):
    store.close_fixture("1:4431", 61300)
    store.save_user(5, "yeni", profile, st.MODE_AUTO, "1")
    await bot.run_check(app)          # form hâlâ Stuttgart'ı gösteriyor
    await app.settle()
    assert app.bot.messages == [] and app.bot.photos == []


async def test_list_cancels_open_captcha(app, store, profile, monkeypatch):
    store.save_user(1, "ali", user_profile(profile, "Veli", "Kurt"), st.MODE_AUTO, "1")
    await bot.run_check(app)
    await app.settle()
    assert 1 in app.bot_data["rt"].pending
    monkeypatch.setattr(bot.news, "fetch_new_articles", lambda cursor: ([list_article()], 61300))
    await bot.run_news_check(app)
    assert 1 not in app.bot_data["rt"].pending
    assert store.get_application(1, "1:4431")["status"] == st.CLOSED


async def test_approval_button_after_list_is_refused(app, store, profile):
    store.save_user(4, "can", profile, st.MODE_ASK, "1")
    store.set_status(4, STUTTGART, st.ASKED)
    store.close_fixture("1:4431", 61300)
    alerts = []

    async def answer(text=None, **kw):
        alerts.append(text)

    async def edit(*a, **kw):
        pass

    q = SimpleNamespace(data="ap:1:4431", message=SimpleNamespace(chat=SimpleNamespace(id=4)),
                        answer=answer, edit_message_reply_markup=edit)
    await bot.on_approval(SimpleNamespace(callback_query=q), SimpleNamespace(application=app))
    await app.settle()
    assert "liste" in alerts[0].lower() and app.bot.photos == []


async def test_unmatched_list_still_notifies_names_and_warns_admin(app, store, profile, monkeypatch):
    other = news.parse_article(page("Ajax maçı gazi ve engelli taraftar bilet listesi",
                                    ["5 Aralık günü oynayacağımız Ajax maçında hak kazanan listesi aşağıdaki gibidir.",
                                     "ALİ YILMAZ"]), 61400)
    store.save_user(1, "ali", user_profile(profile, "Ali", "Yılmaz"), st.MODE_ASK, "1")
    monkeypatch.setattr(bot.news, "fetch_new_articles", lambda cursor: ([other], 61400))
    await bot.run_news_check(app)
    texts = [t for c, t, _ in app.bot.messages]
    assert any("Listede adın var" in t for t in texts)
    assert any("eşleştiremedim" in t for t in texts)
    assert store.closed_keys() == set()
