"""storage.py — SQLite + şifreleme."""

import sqlite3

import pytest
from cryptography.fernet import Fernet

from gsbot import storage as st


def test_profile_is_encrypted_at_rest(store, profile, tmp_path):
    store.save_user(1, "ali", profile, st.MODE_AUTO, "1,2")
    raw = sqlite3.connect(tmp_path / "t.db").execute("SELECT profile FROM users").fetchone()[0]
    assert b"10000000146" not in raw and "Yılmaz".encode() not in raw
    user = store.get_user(1)
    assert user.profile == profile and user.auto and user.branches == ["1", "2"] and user.active


def test_wrong_key_cannot_read(tmp_path, profile):
    path = str(tmp_path / "k.db")
    st.Storage(path, Fernet.generate_key().decode()).save_user(1, None, profile)
    with pytest.raises(RuntimeError):
        st.Storage(path, Fernet.generate_key().decode()).get_user(1)


def test_invalid_key_exits(tmp_path):
    with pytest.raises(SystemExit):
        st.Storage(str(tmp_path / "x.db"), "kisa-anahtar")


def test_update_settings_and_resave_keeps_single_row(store, profile):
    store.save_user(1, "ali", profile)
    store.update_settings(1, mode=st.MODE_AUTO, branches="3", active=False)
    u = store.get_user(1)
    assert (u.mode, u.branches, u.active) == (st.MODE_AUTO, ["3"], False)
    assert store.active_users() == []
    store.save_user(1, "ali", {**profile, "name": "Veli"})   # /guncelle aktif eder
    assert len(store.list_users()) == 1 and store.get_user(1).active


def test_list_users_has_no_personal_data(store, profile):
    store.save_user(1, "ali", profile)
    row = store.list_users()[0]
    assert "profile" not in row and "10000000146" not in str(row)


def test_invite_is_single_use(store):
    code = store.create_invite(99)
    assert store.invite_valid(code)
    assert store.use_invite(code, 1)
    assert not store.invite_valid(code)
    assert not store.use_invite(code, 2)
    assert not store.invite_valid("uydurma")


def test_delete_user_removes_everything(store, profile, fixture_row):
    store.save_user(1, "ali", profile)
    store.set_status(1, fixture_row, st.SENT)
    store.delete_user(1)
    assert store.get_user(1) is None and store.application_keys() == set()


def test_application_status_roundtrip(store, fixture_row):
    store.set_status(1, fixture_row, st.ASKED)
    store.set_status(1, fixture_row, st.FAILED, "captcha hatalı")
    app = store.get_application(1, "1:9876")
    assert app["status"] == st.FAILED and app["detail"] == "captcha hatalı"
    assert app["fixture"] == fixture_row
    assert store.application_keys() == {(1, "1:9876")}


def test_reset_inflight_only_drops_unfinished(store):
    for i, status in enumerate([st.QUEUED, st.CAPTCHA, st.SENT, st.ASKED]):
        store.set_status(1, {"branch": "1", "id": str(i), "text": "m"}, status)
    assert store.reset_inflight() == 2
    assert store.application_keys() == {(1, "1:2"), (1, "1:3")}


def test_plan_notifications_filters_branch_active_and_done(profile):
    a = st.User(1, None, profile, st.MODE_AUTO, ["1"], True)
    b = st.User(2, None, profile, st.MODE_ASK, ["1", "2"], True)
    c = st.User(3, None, profile, st.MODE_ASK, ["1", "2"], False)
    f1 = {"branch": "1", "id": "10", "text": "x"}
    f2 = {"branch": "2", "id": "20", "text": "y"}
    plan = st.plan_notifications([a, b, c], [f1, f2], done={(2, "1:10")})
    assert [(u.chat_id, f["id"]) for u, f in plan] == [(1, "10"), (2, "20")]
