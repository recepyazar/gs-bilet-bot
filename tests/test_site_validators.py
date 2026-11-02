"""site.py ve validators.py — ağa çıkılmaz."""

import pytest

from conftest import FakeSession, make_response
from gsbot import site
from gsbot import validators as v


# ------------------------------------------------------------- doğrulama

@pytest.mark.parametrize("raw", ["10000000146", "100 000 001 46"])
def test_valid_tc_accepts(raw):
    assert v.valid_tc(raw) == "10000000146"


@pytest.mark.parametrize("raw", ["", "1234", "10000000147", "00000000000", "1000000014a6x"])
def test_valid_tc_rejects(raw):
    assert v.valid_tc(raw) is None


@pytest.mark.parametrize("raw", ["5321234567", "05321234567", "+90 532 123 45 67"])
def test_valid_phone(raw):
    assert v.valid_phone(raw) == "5321234567"


@pytest.mark.parametrize("raw", ["123", "2121234567", "532123456"])
def test_valid_phone_rejects(raw):
    assert v.valid_phone(raw) is None


def test_valid_birthdate():
    assert v.valid_birthdate("05.03.1990") == ("5", "3", "1990")
    assert v.valid_birthdate("5/3/1990") == ("5", "3", "1990")
    assert v.valid_birthdate("32.01.1990") is None
    assert v.valid_birthdate("01.01.2020") is None
    assert v.valid_birthdate("dün") is None


def test_valid_branches_sorts_and_dedupes():
    assert v.valid_branches("2, 1 2") == "1,2"
    assert v.valid_branches("7") is None
    assert v.valid_branches("") is None


def test_valid_name():
    assert v.valid_name("  Ayşe   Nur ") == "Ayşe Nur"
    assert v.valid_name("A") is None
    assert v.valid_name("Ali123") is None


def test_yes_no_and_captcha():
    assert v.valid_yes_no("✅ Evet") is True
    assert v.valid_yes_no("hayır") is False
    assert v.valid_yes_no("belki") is None
    assert v.valid_captcha(" Ab3dE9 ") == "Ab3dE9"
    assert v.valid_captcha("abc") is None


def test_mask_hides_middle():
    assert v.mask("10000000146") == "100******46"
    assert v.mask("") == ""
    assert "1234" not in v.mask("1234")


# ------------------------------------------------------------------- site

@pytest.mark.parametrize("raw,expected", [
    ("5321234567", "0(532) 123 45 67"),
    ("+905321234567", "0(532) 123 45 67"),
    ("0(532) 123 45 67", "0(532) 123 45 67"),
    ("", ""),
])
def test_format_phone(raw, expected):
    assert site.format_phone(raw) == expected


def test_fetch_fixtures_parses_and_unescapes():
    session = FakeSession([make_response(json_body=[
        {"Value": "101", "Text": "Galatasaray - Be&#x15F;ikta&#x15F;"},
        {"Value": "", "Text": "boş"},
        "çöp",
    ])])
    rows = site.fetch_fixtures(session, "1")
    assert rows == [{"branch": "1", "id": "101", "text": "Galatasaray - Beşiktaş"}]
    method, url, kw = session.calls[0]
    assert (method, url, kw["data"]) == ("POST", site.FIXTURES_URL, {"branch": "1"})


@pytest.mark.parametrize("body", ["<html>hata</html>", "{}"])
def test_fetch_fixtures_bad_body_is_empty(body):
    assert site.fetch_fixtures(FakeSession([make_response(body)]), "1") == []


def test_fetch_all_fixtures_hits_every_branch():
    session = FakeSession([make_response(json_body=[]) for _ in range(6)])
    site.fetch_all_fixtures(session=session)
    assert [c[2]["data"]["branch"] for c in session.calls] == list(site.BRANCH_NAMES)


def test_prepare_form_returns_token_and_image():
    page = '<form><input name="__RequestVerificationToken" value="TOK"></form>'
    img = make_response("IMG")
    session = FakeSession([make_response(page), img])
    s, token, image = site.prepare_form(session)
    assert (s, token, image) == (session, "TOK", b"IMG")
    assert session.calls[1][1] == site.CAPTCHA_URL


def test_prepare_form_without_token_raises():
    with pytest.raises(RuntimeError):
        site.prepare_form(FakeSession([make_response("<form></form>")]))


def test_payload_contains_all_fields(profile, fixture_row):
    pairs = site.build_payload("TOK", profile, fixture_row, "AB12CD")
    d = dict(pairs)
    assert d["TCIdentityNumber"] == "10000000146"
    assert d["Phone"] == "0(532) 123 45 67"
    assert d["FixtureId"] == "9876" and d["Branch"] == "1" and d["Captcha"] == "AB12CD"
    assert d["AttendantTCIdentityNumber"] == ""
    assert [val for k, val in pairs if k == "IsAttendantRequested"] == ["false"]


def test_payload_with_attendant(profile, fixture_row):
    profile.update(attendant=True, attendant_tc="10000000146", attendant_name="Veli Can")
    pairs = site.build_payload("TOK", profile, fixture_row, "AB12CD")
    assert [val for k, val in pairs if k == "IsAttendantRequested"] == ["true", "false"]
    assert dict(pairs)["AttendantNameSurname"] == "Veli Can"


def test_read_result_variants():
    err = make_response('<span class="field-validation-error">Captcha hatalı</span><span class="validation-error"> </span>')
    assert site.read_result(err) == (False, "Captcha hatalı")
    again = make_response('<input name="TCIdentityNumber">')
    assert site.read_result(again)[0] is False
    ok = make_response("<p>Başvurunuz alınmıştır</p>")
    assert site.read_result(ok)[0] is True


def test_read_result_ignores_site_menu():
    page = make_response(
        "<html><body><div class='menu'>GALATASARAY.ORG Facebook Twitter KULLANICI ADI ŞİFRE</div>"
        "<div class='single_content_full'><h1>GALATASARAY GAZİ VE ENGELLİ BİLET FORMU</h1>"
        "<p>Başvurunuz başarıyla alınmıştır.</p></div><footer>BRANŞLAR</footer></body></html>"
    )
    ok, detail = site.read_result(page)
    assert ok and detail == "Başvurunuz başarıyla alınmıştır."


def test_read_result_unknown_success_page_shows_only_content_box():
    page = make_response(
        "<html><body><div>GALATASARAY.ORG Facebook</div>"
        "<div class='single_content_full'><h1>GALATASARAY GAZİ VE ENGELLİ BİLET FORMU</h1></div></body></html>"
    )
    assert site.read_result(page) == (True, "Form gönderildi.")
