"""Çok kullanıcılı Telegram botu.

Akış
  /start → (davet kodu) → KVKK onayı → bilgiler → branşlar → mod → özet → kayıt
  Her CHECK_INTERVAL saniyede maçlar çekilir; her aktif kullanıcıya, takip ettiği
  branşta daha önce bildirilmemiş maçlar için:
    otomatik mod → form hemen hazırlanır, captcha resmi gönderilir
    onay modu    → "✅ Başvur / ⏭ Geç" düğmeleri gönderilir
  Kullanıcı captcha'nın 6 karakterini yazınca form gönderilir.

Captcha oturumları (requests.Session) bellekte tutulur; kullanıcı başına aynı
anda tek captcha açık olur, diğer maçlar sırada bekler.
"""

import asyncio
import html
import logging
import time
from collections import deque
from dataclasses import dataclass, field

import requests
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import news, site
from . import storage as st
from . import validators as v
from .config import Config

log = logging.getLogger(__name__)

(INVITE, CONSENT, NAME, SURNAME, TC, BIRTH, PHONE, BOIB,
 ATTENDANT, ATT_TC, ATT_NAME, BRANCHES, MODE, CONFIRM) = range(14)

YES_NO_KB = ReplyKeyboardMarkup([["✅ Evet", "❌ Hayır"]], resize_keyboard=True, one_time_keyboard=True)
SKIP_KB = ReplyKeyboardMarkup([["⏭ Atla"]], resize_keyboard=True, one_time_keyboard=True)
CONSENT_KB = ReplyKeyboardMarkup([["Kabul ediyorum"], ["Vazgeç"]], resize_keyboard=True, one_time_keyboard=True)
MODE_KB = ReplyKeyboardMarkup([["⚡ Otomatik"], ["🙋 Önce bana sor"]], resize_keyboard=True, one_time_keyboard=True)
NO_KB = ReplyKeyboardRemove()

CONSENT_TEXT = (
    "🔒 <b>Kişisel verilerin hakkında</b>\n\n"
    "Formu senin adına doldurabilmem için ad, soyad, TC kimlik no, doğum tarihi "
    "ve isteğe bağlı olarak telefon, BOİB seri no ve refakatçi bilgilerini saklayacağım.\n\n"
    "• Bilgiler <b>şifreli</b> tutulur, yalnızca galatasaray.org formunu doldurmak için kullanılır.\n"
    "• Kimseyle paylaşılmaz; bot yöneticisi listede sadece kullanıcı adını görür.\n"
    "• İstediğin an <code>/sil</code> yazarak tüm verilerini kalıcı olarak silebilirsin.\n"
    "• TC, doğum tarihi ve telefon yazdığın mesajları sohbetten hemen silerim.\n\n"
    "Devam etmek için <b>Kabul ediyorum</b> de."
)

HELP_TEXT = (
    "🔴🟡 <b>GS Engelli Bilet Botu</b>\n\n"
    "Yeni maç açılınca sana haber veririm ve formu senin bilgilerinle doldururum. "
    "Formdaki captcha'yı (6 karakter) sen yazarsın, gerisini ben hallederim.\n\n"
    "/ayarlar — mod, branşlar, duraklat/devam\n"
    "/bilgilerim — kayıtlı bilgilerin (maskeli)\n"
    "/guncelle — bilgilerini baştan gir\n"
    "/basvurular — son başvuruların\n"
    "/durum — bot çalışıyor mu?\n"
    "/sil — tüm verilerimi sil\n"
    "/iptal — yarım kalan işlemi iptal et"
)

ADMIN_HELP = "\n\n<b>Yönetici</b>\n/davet [adet] — davet linki üret\n/kullanicilar — kullanıcı listesi\n/kontrol — maçları şimdi kontrol et\n/duyuru — duyuruları şimdi kontrol et"

STATUS_LABELS = {
    st.ASKED: "🙋 onay bekliyor",
    st.QUEUED: "⏳ sırada",
    st.CAPTCHA: "🔤 captcha bekliyor",
    st.SENT: "✅ gönderildi",
    st.FAILED: "❌ hata",
    st.SKIPPED: "⏭ atlandı",
    st.EXPIRED: "⌛ süre doldu",
    st.CLOSED: "📋 liste çıktı, başvurulmadı",
    st.WON: "🎉 listede!",
    st.LOST: "😔 listede yok",
}


@dataclass
class Pending:
    """Bir kullanıcının açık captcha'sı."""
    fixture: dict
    session: requests.Session | None = None
    token: str = ""
    ready: bool = False          # form hazırlanırken False


@dataclass
class Runtime:
    config: Config
    store: st.Storage
    pending: dict[int, Pending] = field(default_factory=dict)
    queues: dict[int, deque] = field(default_factory=dict)
    fetch_failures: int = 0
    started_at: float = field(default_factory=time.time)
    last_check_at: float | None = None
    last_fixtures: list = field(default_factory=list)
    check_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    news_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_news_check_at: float | None = None


def rt(context: ContextTypes.DEFAULT_TYPE) -> Runtime:
    return context.application.bot_data["rt"]


def esc(s) -> str:
    return html.escape(str(s))


def branch_label(branch: str) -> str:
    return site.BRANCH_NAMES.get(branch, branch)


def fixture_line(fx: dict) -> str:
    return f"🏟 <b>{esc(fx['text'])}</b> <i>({esc(branch_label(fx['branch']))})</i>"


def is_admin(context, chat_id: int) -> bool:
    return chat_id in rt(context).config.admin_ids


async def delete_quietly(update: Update):
    """Hassas veri içeren kullanıcı mesajını sohbetten siler (özel sohbette bota izin var)."""
    try:
        await update.message.delete()
    except Exception:  # 48 saati geçmiş ya da yetki yok — önemli değil
        pass


# ==========================================================================
# Kayıt sohbeti
# ==========================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = rt(context)
    chat_id = update.effective_chat.id
    if r.store.is_registered(chat_id):
        await update.message.reply_html(
            HELP_TEXT + (ADMIN_HELP if is_admin(context, chat_id) else ""), reply_markup=NO_KB
        )
        return ConversationHandler.END

    context.user_data.clear()
    code = context.args[0] if context.args else ""
    if is_admin(context, chat_id):
        context.user_data["invite"] = None
    elif code and r.store.invite_valid(code):
        context.user_data["invite"] = code
    else:
        msg = "👋 Merhaba! Bu bot davetle çalışıyor.\n\nSana verilen <b>davet kodunu</b> yaz."
        if code:
            msg = "❌ Bu davet kodu geçersiz ya da kullanılmış.\n\nBaşka bir kodun varsa yaz."
        await update.message.reply_html(msg, reply_markup=NO_KB)
        return INVITE

    await update.message.reply_html(CONSENT_TEXT, reply_markup=CONSENT_KB)
    return CONSENT


async def got_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    if not rt(context).store.invite_valid(code):
        await update.message.reply_html("❌ Kod geçersiz ya da kullanılmış. Tekrar dene ya da /iptal.")
        return INVITE
    context.user_data["invite"] = code
    await update.message.reply_html(CONSENT_TEXT, reply_markup=CONSENT_KB)
    return CONSENT


async def got_consent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() != "kabul ediyorum":
        await update.message.reply_html("Tamam, hiçbir bilgi kaydetmedim. Fikrin değişirse /start.", reply_markup=NO_KB)
        context.user_data.clear()
        return ConversationHandler.END
    return await ask_name(update, context)


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["profile"] = {}
    await update.message.reply_html("1/9 — <b>Adın</b>?", reply_markup=NO_KB)
    return NAME


async def got_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = v.valid_name(update.message.text)
    if not name:
        await update.message.reply_html("❌ Sadece harf kullan (2-50 karakter).")
        return NAME
    context.user_data["profile"]["name"] = name
    await update.message.reply_html("2/9 — <b>Soyadın</b>?")
    return SURNAME


async def got_surname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    surname = v.valid_name(update.message.text)
    if not surname:
        await update.message.reply_html("❌ Sadece harf kullan (2-50 karakter).")
        return SURNAME
    context.user_data["profile"]["surname"] = surname
    await update.message.reply_html("3/9 — <b>TC Kimlik No</b> (11 hane)?\n<i>Mesajını görür görmez sileceğim.</i>")
    return TC


async def got_tc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tc = v.valid_tc(update.message.text)
    await delete_quietly(update)
    if not tc:
        await update.effective_chat.send_message("❌ Geçerli bir TC Kimlik No değil, tekrar yaz.")
        return TC
    context.user_data["profile"]["tc"] = tc
    await update.effective_chat.send_message(
        f"✅ TC alındı ({v.mask(tc)})\n\n4/9 — <b>Doğum tarihin</b>? <code>GG.AA.YYYY</code> (ör. <code>05.03.1990</code>)",
        parse_mode=ParseMode.HTML,
    )
    return BIRTH


async def got_birth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parsed = v.valid_birthdate(update.message.text)
    await delete_quietly(update)
    if not parsed:
        await update.effective_chat.send_message(
            "❌ Tarihi <code>GG.AA.YYYY</code> yaz, yıl 1920-2013 arası olmalı.", parse_mode=ParseMode.HTML
        )
        return BIRTH
    p = context.user_data["profile"]
    p["birth_day"], p["birth_month"], p["birth_year"] = parsed
    await update.effective_chat.send_message(
        "✅ Doğum tarihi alındı\n\n5/9 — <b>Telefon numaran</b>? (ör. <code>5321234567</code>)",
        parse_mode=ParseMode.HTML, reply_markup=SKIP_KB,
    )
    return PHONE


async def got_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if v.is_skip(text):
        context.user_data["profile"]["phone"] = ""
    else:
        phone = v.valid_phone(text)
        await delete_quietly(update)
        if not phone:
            await update.effective_chat.send_message(
                "❌ 5 ile başlayan 10 haneli numara bekliyorum (ör. 5321234567) ya da Atla.", reply_markup=SKIP_KB
            )
            return PHONE
        context.user_data["profile"]["phone"] = phone
    await update.effective_chat.send_message(
        "6/9 — <b>Gazi/Engelli kart seri no (BOİB)</b>?", parse_mode=ParseMode.HTML, reply_markup=SKIP_KB
    )
    return BOIB


async def got_boib(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["profile"]["boib"] = "" if v.is_skip(text) else text[:40]
    await update.message.reply_html("7/9 — <b>Refakatçi</b> istiyor musun?", reply_markup=YES_NO_KB)
    return ATTENDANT


async def got_attendant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = v.valid_yes_no(update.message.text)
    if answer is None:
        await update.message.reply_html("Evet ya da Hayır seç.", reply_markup=YES_NO_KB)
        return ATTENDANT
    p = context.user_data["profile"]
    p["attendant"] = answer
    if answer:
        await update.message.reply_html("Refakatçinin <b>TC Kimlik No</b>'su?", reply_markup=NO_KB)
        return ATT_TC
    p["attendant_tc"] = p["attendant_name"] = ""
    return await ask_branches(update, context)


async def got_att_tc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tc = v.valid_tc(update.message.text)
    await delete_quietly(update)
    if not tc:
        await update.effective_chat.send_message("❌ Geçerli bir TC Kimlik No değil, tekrar yaz.")
        return ATT_TC
    context.user_data["profile"]["attendant_tc"] = tc
    await update.effective_chat.send_message(
        f"✅ Alındı ({v.mask(tc)})\n\nRefakatçinin <b>adı soyadı</b>?", parse_mode=ParseMode.HTML
    )
    return ATT_NAME


async def got_att_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = v.valid_name(update.message.text)
    if not name:
        await update.message.reply_html("❌ Sadece harf kullan.")
        return ATT_NAME
    context.user_data["profile"]["attendant_name"] = name
    return await ask_branches(update, context)


async def ask_branches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = "\n".join(f"  <code>{k}</code> — {esc(n)}" for k, n in site.BRANCH_NAMES.items())
    await update.effective_chat.send_message(
        f"8/9 — Hangi <b>branşları</b> takip edeyim?\n{lines}\n\n"
        "Numaraları virgülle yaz (ör. <code>1,2</code>). Hepsi için <b>Atla</b>.",
        parse_mode=ParseMode.HTML, reply_markup=SKIP_KB,
    )
    return BRANCHES


async def got_branches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    branches = site.ALL_BRANCHES if v.is_skip(text) else v.valid_branches(text)
    if not branches:
        await update.message.reply_html("❌ Sadece 1-6 arası numaralar, virgülle ayrılmış.", reply_markup=SKIP_KB)
        return BRANCHES
    context.user_data["branches"] = branches
    await update.message.reply_html(
        "9/9 — Yeni maç açılınca ne yapayım?\n\n"
        "⚡ <b>Otomatik</b> — formu hemen hazırlarım, captcha resmini atarım, "
        "sen 6 karakteri yazarsın, gönderirim.\n\n"
        "🙋 <b>Önce bana sor</b> — \"X maçı açıldı, başvurayım mı?\" diye sorarım, "
        "evet dersen captcha'yı atarım.\n\n"
        "<i>Bunu sonra /ayarlar'dan değiştirebilirsin.</i>",
        reply_markup=MODE_KB,
    )
    return MODE


async def got_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if "otomatik" in text:
        mode = st.MODE_AUTO
    elif "sor" in text or "onay" in text:
        mode = st.MODE_ASK
    else:
        await update.message.reply_html("Seçeneklerden birini seç.", reply_markup=MODE_KB)
        return MODE
    context.user_data["mode"] = mode
    summary = profile_summary(context.user_data["profile"], mode, context.user_data["branches"])
    await update.message.reply_html(f"{summary}\n\nKaydedeyim mi?", reply_markup=YES_NO_KB)
    return CONFIRM


async def got_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = v.valid_yes_no(update.message.text)
    if answer is None:
        await update.message.reply_html("Evet ya da Hayır seç.", reply_markup=YES_NO_KB)
        return CONFIRM
    if not answer:
        context.user_data.clear()
        await update.message.reply_html("Kaydetmedim. Baştan başlamak için /start.", reply_markup=NO_KB)
        return ConversationHandler.END

    r = rt(context)
    chat_id = update.effective_chat.id
    ud = context.user_data
    is_update = r.store.is_registered(chat_id)
    if not is_update and ud.get("invite") and not r.store.use_invite(ud["invite"], chat_id):
        ud.clear()
        await update.message.reply_html("❌ Davet kodu bu arada kullanılmış. Yeni kod iste.", reply_markup=NO_KB)
        return ConversationHandler.END

    r.store.save_user(chat_id, update.effective_user.username, ud["profile"], ud["mode"], ud["branches"])
    ud.clear()
    await update.message.reply_html(
        ("✅ <b>Bilgilerin güncellendi.</b>" if is_update else "✅ <b>Kaydın tamam!</b>")
        + "\n\nYeni maç açılınca haber vereceğim.\n\n" + HELP_TEXT,
        reply_markup=NO_KB,
    )
    if not is_update:
        for admin in r.config.admin_ids:
            if admin != chat_id:
                await safe_send(context, admin, f"👤 Yeni kullanıcı: @{esc(update.effective_user.username or chat_id)}")
    # Şu an açık maçlar varsa hemen ele al
    context.application.create_task(run_check(context.application))
    return ConversationHandler.END


async def guncelle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not rt(context).store.is_registered(update.effective_chat.id):
        return await start(update, context)
    context.user_data.clear()
    await update.message.reply_html("Bilgilerini baştan alıyorum. Vazgeçmek için /iptal.")
    return await ask_name(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_html("🚫 İptal edildi, hiçbir şey kaydedilmedi.", reply_markup=NO_KB)
    return ConversationHandler.END


def profile_summary(p: dict, mode: str, branches: str) -> str:
    attendant = (
        f"{esc(p['attendant_name'])} ({v.mask(p['attendant_tc'])})" if p.get("attendant") else "yok"
    )
    picked = ", ".join(branch_label(b) for b in branches.split(",") if b)
    return (
        "📋 <b>Bilgilerin</b>\n\n"
        f"Ad Soyad: {esc(p['name'])} {esc(p['surname'])}\n"
        f"TC: {v.mask(p['tc'])}\n"
        f"Doğum: {p['birth_day']}.{p['birth_month']}.{p['birth_year']}\n"
        f"Telefon: {v.mask(p.get('phone', '')) or 'yok'}\n"
        f"BOİB: {esc(p.get('boib') or 'yok')}\n"
        f"Refakatçi: {attendant}\n"
        f"Branşlar: {esc(picked)}\n"
        f"Mod: {'⚡ Otomatik' if mode == st.MODE_AUTO else '🙋 Önce sor'}"
    )


# ==========================================================================
# Ayarlar ve diğer komutlar
# ==========================================================================

def settings_view(user: st.User) -> tuple[str, InlineKeyboardMarkup]:
    mode_txt = "⚡ Otomatik" if user.auto else "🙋 Önce sor"
    text = (
        "⚙️ <b>Ayarlar</b>\n\n"
        f"Mod: <b>{mode_txt}</b>\n"
        f"Durum: <b>{'▶️ Aktif' if user.active else '⏸ Duraklatıldı'}</b>\n\n"
        "Takip ettiğin branşlara dokunarak aç/kapat."
    )
    rows = [[InlineKeyboardButton(
        f"Mod: {mode_txt} → değiştir", callback_data="set:mode")]]
    btns = [
        InlineKeyboardButton(f"{'✅' if b in user.branches else '▫️'} {n}", callback_data=f"set:br:{b}")
        for b, n in site.BRANCH_NAMES.items()
    ]
    rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([InlineKeyboardButton("⏸ Duraklat" if user.active else "▶️ Devam ettir", callback_data="set:active")])
    return text, InlineKeyboardMarkup(rows)


async def need_user(update: Update, context) -> st.User | None:
    user = rt(context).store.get_user(update.effective_chat.id)
    if not user:
        await update.effective_chat.send_message("Önce kayıt olman lazım: /start")
    return user


async def ayarlar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await need_user(update, context)
    if user:
        text, kb = settings_view(user)
        await update.message.reply_html(text, reply_markup=kb)


async def on_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    r = rt(context)
    user = r.store.get_user(q.message.chat.id)
    if not user:
        await q.answer("Kaydın bulunamadı, /start", show_alert=True)
        return
    parts = q.data.split(":")
    if parts[1] == "mode":
        r.store.update_settings(user.chat_id, mode=st.MODE_ASK if user.auto else st.MODE_AUTO)
    elif parts[1] == "active":
        r.store.update_settings(user.chat_id, active=not user.active)
    elif parts[1] == "br":
        chosen = set(user.branches) ^ {parts[2]}
        if not chosen:
            await q.answer("En az bir branş seçili kalmalı.", show_alert=True)
            return
        r.store.update_settings(user.chat_id, branches=",".join(sorted(chosen)))
    await q.answer("Kaydedildi")
    text, kb = settings_view(r.store.get_user(user.chat_id))
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def bilgilerim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await need_user(update, context)
    if user:
        await update.message.reply_html(
            profile_summary(user.profile, user.mode, ",".join(user.branches))
            + f"\nDurum: {'▶️ Aktif' if user.active else '⏸ Duraklatıldı'}"
            + "\n\nDeğiştirmek için /guncelle veya /ayarlar"
        )


async def basvurular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await need_user(update, context)
    if not user:
        return
    rows = rt(context).store.recent_applications(user.chat_id)
    if not rows:
        await update.message.reply_html("Henüz bir maç bildirilmedi.")
        return
    lines = [
        f"• {esc(r['fixture_text'])} — {STATUS_LABELS.get(r['status'], r['status'])}" for r in rows
    ]
    await update.message.reply_html("🗂 <b>Son başvuruların</b>\n\n" + "\n".join(lines))


async def sil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not rt(context).store.is_registered(update.effective_chat.id):
        await update.message.reply_html("Kayıtlı bir verin yok.")
        return
    await update.message.reply_html(
        "⚠️ Tüm bilgilerin ve başvuru geçmişin <b>kalıcı olarak</b> silinecek. Emin misin?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Evet, sil", callback_data="del:yes"),
            InlineKeyboardButton("Vazgeç", callback_data="del:no"),
        ]]),
    )


async def on_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data != "del:yes":
        await q.edit_message_text("Vazgeçildi, hiçbir şey silinmedi.")
        return
    r = rt(context)
    chat_id = q.message.chat.id
    r.store.delete_user(chat_id)
    r.pending.pop(chat_id, None)
    r.queues.pop(chat_id, None)
    await q.edit_message_text("🗑 Tüm verilerin silindi. Tekrar kullanmak istersen yeni bir davet koduyla /start.")


def ago(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} sn"
    if seconds < 3600:
        return f"{seconds // 60} dk"
    if seconds < 86400:
        return f"{seconds // 3600} sa {seconds % 3600 // 60} dk"
    return f"{seconds // 86400} gün {seconds % 86400 // 3600} sa"


def bot_version() -> str:
    """CD betiğinin build öncesi yazdığı sürüm (commit kısa hash + başlık)."""
    try:
        from pathlib import Path
        return (Path(__file__).parent / "VERSION").read_text(encoding="utf-8").strip() or "bilinmiyor"
    except OSError:
        return "bilinmiyor (yerel)"


def status_text(r: Runtime, chat_id: int) -> str:
    now = time.time()
    if r.last_check_at is None:
        health, check = "🟡", "henüz kontrol yapılmadı"
    else:
        late = now - r.last_check_at > 3 * r.config.check_interval
        health = "🟡" if late or r.fetch_failures else "🟢"
        check = f"{ago(now - r.last_check_at)} önce"
    lines = [f"{health} <b>Bot çalışıyor</b>", f"Son başarılı kontrol: {check}"]
    if r.fetch_failures:
        lines.append(f"⚠️ Site son {r.fetch_failures} denemede yanıt vermedi")
    if r.last_news_check_at is not None:
        lines.append(f"Son duyuru kontrolü: {ago(now - r.last_news_check_at)} önce")
    if r.last_fixtures:
        lines.append("\n<b>Açık maçlar</b>")
        lines += [fixture_line(fx) for fx in r.last_fixtures]
    elif r.last_check_at is not None:
        lines.append("\nŞu an açık maç yok.")

    user = r.store.get_user(chat_id)
    if user:
        lines.append(
            f"\n<b>Sen</b>: {'▶️ aktif' if user.active else '⏸ duraklatıldı'}, "
            f"{'⚡ otomatik' if user.auto else '🙋 önce sor'}"
        )
    if chat_id in r.config.admin_ids:
        users = r.store.list_users()
        active = sum(1 for u in users if u["active"])
        lines.append(
            f"\n<b>Yönetici</b>\nAçık kalma süresi: {ago(now - r.started_at)}\n"
            f"Kullanıcı: {len(users)} ({active} aktif)\n"
            f"Bekleyen captcha: {len(r.pending)}\n"
            f"Kontrol aralığı: {r.config.check_interval} sn\n"
            f"Sürüm: {esc(bot_version())}"
        )
    return "\n".join(lines)


async def durum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(status_text(rt(context), update.effective_chat.id))


async def kimim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(f"Chat ID'n: <code>{update.effective_chat.id}</code>")


async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sohbet dışı, captcha beklenmeyen metinler."""
    if rt(context).store.is_registered(update.effective_chat.id):
        await update.message.reply_html("Şu an beklediğim bir şey yok. Komutlar için /start")
    else:
        await update.message.reply_html("Başlamak için /start")


# ---------------------------------------------------------------- yönetici

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(context, update.effective_chat.id):
            return
        return await func(update, context)
    return wrapper


@admin_only
async def davet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = max(1, min(10, int(context.args[0]))) if context.args else 1
    except ValueError:
        count = 1
    username = context.bot.username
    lines = []
    for _ in range(count):
        code = rt(context).store.create_invite(update.effective_chat.id)
        lines.append(f"<code>{code}</code>\nhttps://t.me/{username}?start={code}")
    await update.message.reply_html(
        "🎟 <b>Davet</b> (her biri tek kullanımlık)\n\n" + "\n\n".join(lines),
        disable_web_page_preview=True,
    )


@admin_only
async def kullanicilar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = rt(context).store.list_users()
    if not users:
        await update.message.reply_html("Henüz kullanıcı yok.")
        return
    lines = [
        f"{'▶️' if u['active'] else '⏸'} @{esc(u['username'] or u['chat_id'])} — "
        f"{'⚡' if u['mode'] == st.MODE_AUTO else '🙋'} — branş {u['branches']}"
        for u in users
    ]
    await update.message.reply_html(f"👥 <b>{len(users)} kullanıcı</b>\n\n" + "\n".join(lines))


@admin_only
async def kontrol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html("🔎 Kontrol ediliyor...")
    found = await run_check(context.application)
    await update.message.reply_html(
        "⚠️ Site yanıt vermedi." if found is None else f"Açık karşılaşma: {found}"
    )


# ==========================================================================
# Maç kontrolü
# ==========================================================================

async def safe_send(context_or_app, chat_id: int, text: str, **kwargs):
    """Mesaj gönderir; kullanıcı botu engellediyse onu duraklatır."""
    app = getattr(context_or_app, "application", context_or_app)
    try:
        return await app.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML,
                                          disable_web_page_preview=True, **kwargs)
    except Forbidden:
        log.info("chat %s botu engellemiş, duraklatılıyor", chat_id)
        app.bot_data["rt"].store.update_settings(chat_id, active=False)
    except Exception as e:
        log.warning("chat %s mesaj gönderilemedi: %s", chat_id, type(e).__name__)
    return None


async def check_job(context: ContextTypes.DEFAULT_TYPE):
    await run_check(context.application)
    await ping_healthcheck(context.application.bot_data["rt"].config.healthcheck_url)


async def ping_healthcheck(url: str):
    """Dış gözcüye "yaşıyorum" sinyali. Bot çökerse sinyal kesilir, gözcü haber verir.

    Site çökse de ping atılır: gözcü botun kendisini izler, site hatalarını bot zaten bildiriyor.
    """
    if not url:
        return
    try:
        await asyncio.to_thread(requests.get, url, timeout=10)
    except Exception as e:
        log.warning("Healthcheck ping atılamadı: %s", type(e).__name__)


async def run_check(app: Application) -> int | None:
    """Maçları çeker ve gerekenleri bildirir. Açık maç sayısını (hata ise None) döndürür."""
    r: Runtime = app.bot_data["rt"]
    async with r.check_lock:
        try:
            fixtures = await asyncio.to_thread(site.fetch_all_fixtures)
        except Exception as e:
            r.fetch_failures += 1
            log.warning("Karşılaşmalar çekilemedi (%d. kez): %s", r.fetch_failures, e)
            if r.fetch_failures == 3:
                for admin in r.config.admin_ids:
                    await safe_send(app, admin, f"⚠️ Site 3 kez üst üste yanıt vermedi: {esc(e)}")
            return None
        if r.fetch_failures >= 3:
            for admin in r.config.admin_ids:
                await safe_send(app, admin, "✅ Site yeniden yanıt veriyor.")
        r.fetch_failures = 0
        r.last_check_at = time.time()
        r.last_fixtures = fixtures

        plan = st.plan_notifications(r.store.active_users(), fixtures, r.store.application_keys(), r.store.closed_keys())
        if plan:
            log.info("%d bildirim (%d açık maç)", len(plan), len(fixtures))
        for user, fx in plan:
            key = site.fixture_key(fx)
            if user.auto:
                r.store.set_status(user.chat_id, fx, st.QUEUED)
                sent = await safe_send(app, user.chat_id,
                                       f"🔔 <b>Yeni maç!</b>\n{fixture_line(fx)}\n\nForm hazırlanıyor...")
                if sent:
                    enqueue(app, user.chat_id, fx)
            else:
                r.store.set_status(user.chat_id, fx, st.ASKED)
                await safe_send(
                    app, user.chat_id,
                    f"🔔 <b>Yeni maç!</b>\n{fixture_line(fx)}\n\nBaşvurayım mı?",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Başvur", callback_data=f"ap:{key}"),
                        InlineKeyboardButton("⏭ Geç", callback_data=f"sk:{key}"),
                    ]]),
                )
        return len(fixtures)


# ==========================================================================
# Duyurular: "gazi ve engelli taraftar bilet listesi"
# ==========================================================================

async def news_job(context: ContextTypes.DEFAULT_TYPE):
    await run_news_check(context.application)


async def run_news_check(app: Application) -> list | None:
    """Yeni duyuruları bulur, liste olanları işler. İşlenen duyuruları (hata ise None) döndürür."""
    r: Runtime = app.bot_data["rt"]
    async with r.news_lock:
        cursor = int(r.store.get_kv("news_cursor") or r.config.news_start_id)
        try:
            articles, new_cursor = await asyncio.to_thread(news.fetch_new_articles, cursor)
        except Exception as e:
            log.warning("Duyurular çekilemedi: %s", e)
            return None
        r.last_news_check_at = time.time()
        for article in articles:
            if not r.store.news_processed(article.id):
                if article.is_winner_list:
                    await handle_winner_list(app, article)
                r.store.mark_news(article.id, article.title, article.is_winner_list)
        if new_cursor != cursor:
            r.store.set_kv("news_cursor", str(new_cursor))
        return articles


async def handle_winner_list(app: Application, article: news.Article):
    r: Runtime = app.bot_data["rt"]
    names = news.list_names(article)
    log.info("Liste duyurusu %d: %d isim", article.id, len(names))

    # Hangi maç(lar)a ait? Açık maçlar + daha önce bildirilmiş maçlar
    candidates = {site.fixture_key(f): f for f in [*r.store.known_fixtures(), *r.last_fixtures]}
    matched = {k: f for k, f in candidates.items() if news.fixture_matches(article, f["text"])}

    # Bu maçlar artık kapalı: form açık olsa bile kimse için başvuru yapılmaz
    for key, fx in matched.items():
        r.store.close_fixture(key, article.id)
        await cancel_fixture(app, key)

    link = f"<a href='{article.url}'>Duyuruyu aç</a>"
    winners = 0
    for user in r.store.all_users():
        result = news.match_person(user.profile, names)
        applied = [a for k in matched for a in r.store.applications_for(k)
                   if a["chat_id"] == user.chat_id and a["status"] in (st.SENT, st.WON, st.LOST)]
        if result == "exact":
            winners += 1
            for a in applied:
                r.store.set_status(user.chat_id, candidates[a["fixture_key"]], st.WON)
            await safe_send(app, user.chat_id,
                            f"🎉 <b>Listede adın var!</b>\n\n📋 {esc(article.title)}\n\n"
                            f"Listede <b>{esc(user.profile['name'])} {esc(user.profile['surname'])}</b> "
                            f"adıyla bir kayıt var. Aynı isimde başka biri de olabilir; "
                            f"duyurudaki talimatları mutlaka kontrol et.\n\n👉 {link}")
        elif result == "similar":
            await safe_send(app, user.chat_id,
                            f"🤔 <b>Listede adına benzeyen bir kayıt var.</b>\n\n📋 {esc(article.title)}\n\n"
                            f"Ad ve soyadın tutuyor ama ikinci ad farklı. Sen olabilirsin, kontrol et.\n\n👉 {link}")
        elif applied:
            for a in applied:
                r.store.set_status(user.chat_id, candidates[a["fixture_key"]], st.LOST)
            await safe_send(app, user.chat_id,
                            f"😔 <b>Bu sefer listede yoksun.</b>\n\n📋 {esc(article.title)}\n\n"
                            f"Bir sonraki maçta tekrar deneriz.\n👉 {link}")

    matched_txt = ", ".join(esc(f["text"]) for f in matched.values()) or "⚠️ hiçbir maçla eşleştiremedim (başvurular kapatılmadı)"
    for admin in r.config.admin_ids:
        await safe_send(app, admin,
                        f"📋 <b>Liste yayınlandı</b>: {esc(article.title)}\n"
                        f"{len(names)} isim, kullanıcılardan {winners} kişi listede.\n"
                        f"Kapatılan maç: {matched_txt}\n{link}")


async def cancel_fixture(app: Application, key: str):
    """Listesi çıkan maç için sıradaki/açık captcha ve onay bekleyen başvuruları kapatır."""
    r: Runtime = app.bot_data["rt"]
    for row in r.store.applications_for(key):
        if row["status"] in (st.SENT, st.WON, st.LOST, st.SKIPPED):
            continue
        chat_id = row["chat_id"]
        fx = {"branch": row["branch"], "id": row["fixture_id"], "text": row["fixture_text"]}
        q = r.queues.get(chat_id)
        if q:
            r.queues[chat_id] = type(q)(x for x in q if site.fixture_key(x) != key)
        p = r.pending.get(chat_id)
        if p and site.fixture_key(p.fixture) == key:
            finish_pending(app, chat_id)
        r.store.set_status(chat_id, fx, st.CLOSED)
        if row["status"] in (st.ASKED, st.QUEUED, st.CAPTCHA):
            await safe_send(app, chat_id, f"📋 {fixture_line(fx)}\nListe yayınlandı, bu maç için başvuru kapandı.")
            if p and site.fixture_key(p.fixture) == key:
                await start_next(app, chat_id)


@admin_only
async def duyuru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    articles = await run_news_check(context.application)
    if articles is None:
        await update.message.reply_html("⚠️ Duyurular çekilemedi.")
        return
    cursor = rt(context).store.get_kv("news_cursor") or rt(context).config.news_start_id
    lines = [f"• {esc(a.title)}{' 📋' if a.is_winner_list else ''}" for a in articles] or ["Yeni duyuru yok."]
    await update.message.reply_html("📰 <b>Duyurular</b>\n" + "\n".join(lines) + f"\n\nSon bilinen: /haber/{cursor}")


# ==========================================================================
# Captcha akışı
# ==========================================================================

def enqueue(app: Application, chat_id: int, fx: dict):
    r: Runtime = app.bot_data["rt"]
    key = site.fixture_key(fx)
    q = r.queues.setdefault(chat_id, deque())
    current = r.pending.get(chat_id)
    if (current and site.fixture_key(current.fixture) == key) or any(site.fixture_key(x) == key for x in q):
        return
    q.append(fx)
    r.store.set_status(chat_id, fx, st.QUEUED)
    app.create_task(start_next(app, chat_id))


async def start_next(app: Application, chat_id: int):
    """Kullanıcının sıradaki maçı için formu hazırlar ve captcha'yı yollar."""
    r: Runtime = app.bot_data["rt"]
    q = r.queues.get(chat_id)
    while q and chat_id not in r.pending:
        fx = q.popleft()
        if not r.store.is_registered(chat_id):
            r.queues.pop(chat_id, None)
            return
        if site.fixture_key(fx) in r.store.closed_keys():
            r.store.set_status(chat_id, fx, st.CLOSED)
            continue
        pending = Pending(fixture=fx)
        r.pending[chat_id] = pending          # hazırlanırken başka maç başlamasın
        try:
            session, token, image = await asyncio.to_thread(site.prepare_form)
        except Exception as e:
            log.warning("Form hazırlanamadı: %s", e)
            r.pending.pop(chat_id, None)
            r.store.set_status(chat_id, fx, st.FAILED, str(e)[:200])
            await safe_send(app, chat_id, f"⚠️ {fixture_line(fx)}\nForm hazırlanamadı: {esc(e)}",
                            reply_markup=retry_kb(fx))
            continue

        pending.session, pending.token, pending.ready = session, token, True
        user = r.store.get_user(chat_id)
        minutes = r.config.captcha_wait // 60
        try:
            await app.bot.send_photo(
                chat_id, image,
                caption=(
                    f"🎫 {fixture_line(fx)}\n\n"
                    f"Form <b>{esc(user.profile['name'])} {esc(user.profile['surname'])}</b> bilgileriyle hazır.\n"
                    f"Resimdeki <b>6 karakteri</b> yaz, göndereyim. ({minutes} dk bekliyorum)"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=captcha_kb(),
            )
        except Exception as e:
            log.warning("chat %s captcha gönderilemedi: %s", chat_id, type(e).__name__)
            r.pending.pop(chat_id, None)
            r.store.set_status(chat_id, fx, st.FAILED, "captcha gönderilemedi")
            continue
        r.store.set_status(chat_id, fx, st.CAPTCHA)
        for job in app.job_queue.get_jobs_by_name(f"cap-{chat_id}"):
            job.schedule_removal()
        app.job_queue.run_once(expire_captcha, r.config.captcha_wait,
                               data=(chat_id, site.fixture_key(fx)), name=f"cap-{chat_id}")
        return


def captcha_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Yeni resim", callback_data="cap:new"),
        InlineKeyboardButton("⏭ Bu maçı atla", callback_data="cap:skip"),
    ]])


def retry_kb(fx: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔁 Tekrar dene", callback_data=f"ap:{site.fixture_key(fx)}"),
        InlineKeyboardButton("🌐 Elle doldur", url=site.FORM_URL),
    ]])


def finish_pending(app: Application, chat_id: int) -> Pending | None:
    r: Runtime = app.bot_data["rt"]
    p = r.pending.pop(chat_id, None)
    for job in app.job_queue.get_jobs_by_name(f"cap-{chat_id}"):
        job.schedule_removal()
    return p


async def expire_captcha(context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    r: Runtime = app.bot_data["rt"]
    chat_id, key = context.job.data
    p = r.pending.get(chat_id)
    if not p or site.fixture_key(p.fixture) != key:
        return
    r.pending.pop(chat_id, None)
    r.store.set_status(chat_id, p.fixture, st.EXPIRED)
    await safe_send(app, chat_id, f"⌛ {fixture_line(p.fixture)}\nCaptcha süresi doldu, gönderim yapılmadı.",
                    reply_markup=retry_kb(p.fixture))
    await start_next(app, chat_id)


async def on_captcha_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    r = rt(context)
    chat_id = update.effective_chat.id
    p = r.pending.get(chat_id)
    if not p:
        return await fallback_text(update, context)
    if not p.ready:
        await update.message.reply_html("⏳ Form hazırlanıyor, resim birazdan gelecek.")
        return
    answer = v.valid_captcha(update.message.text)
    if not answer:
        await update.message.reply_html("❌ 6 karakter bekliyorum (harf/rakam). Tekrar yaz.")
        return

    finish_pending(app, chat_id)
    user = r.store.get_user(chat_id)
    if not user:
        return
    await update.message.reply_html("📤 Gönderiyorum...")
    try:
        ok, detail = await asyncio.to_thread(site.submit_and_read, p.session, p.token, user.profile, p.fixture, answer)
    except Exception as e:
        ok, detail = False, f"Bağlantı hatası: {e}"

    r.store.set_status(chat_id, p.fixture, st.SENT if ok else st.FAILED, detail[:300])
    if ok:
        await update.message.reply_html(
            f"✅ <b>Başvuru gönderildi!</b>\n{fixture_line(p.fixture)}\n\n<i>{esc(detail)}</i>"
        )
    else:
        await update.message.reply_html(
            f"❌ <b>Başvuru gönderilemedi.</b>\n{fixture_line(p.fixture)}\n\n<i>{esc(detail)}</i>",
            reply_markup=retry_kb(p.fixture),
        )
    await start_next(app, chat_id)


async def on_captcha_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    app = context.application
    r = rt(context)
    chat_id = q.message.chat.id
    p = r.pending.get(chat_id)
    if not p or not p.ready:
        await q.answer("Bu captcha artık geçerli değil.", show_alert=True)
        return
    if q.data == "cap:skip":
        await q.answer("Atlandı")
        finish_pending(app, chat_id)
        r.store.set_status(chat_id, p.fixture, st.SKIPPED)
        await q.edit_message_caption(f"⏭ {fixture_line(p.fixture)} atlandı.", parse_mode=ParseMode.HTML)
        await start_next(app, chat_id)
        return
    await q.answer("Yeni resim geliyor")
    try:
        image = await asyncio.to_thread(site.fetch_captcha, p.session)
    except Exception as e:
        await safe_send(app, chat_id, f"⚠️ Yeni resim alınamadı: {esc(e)}")
        return
    await app.bot.send_photo(chat_id, image, caption="🔄 Yeni captcha — 6 karakteri yaz.",
                             reply_markup=captcha_kb())


async def on_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Onay modundaki ✅ Başvur / ⏭ Geç ve 🔁 Tekrar dene düğmeleri."""
    q = update.callback_query
    r = rt(context)
    chat_id = q.message.chat.id
    action, key = q.data.split(":", 1)
    if not r.store.is_registered(chat_id):
        await q.answer("Kaydın bulunamadı, /start", show_alert=True)
        return
    row = r.store.get_application(chat_id, key)
    if not row:
        await q.answer("Bu maç bulunamadı.", show_alert=True)
        return
    fx = row["fixture"]
    if action == "sk":
        r.store.set_status(chat_id, fx, st.SKIPPED)
        await q.answer("Geçildi")
        await q.edit_message_text(f"⏭ {fixture_line(fx)} — geçildi.", parse_mode=ParseMode.HTML)
        return
    if key in r.store.closed_keys():
        await q.answer("Bu maçın listesi yayınlandı, başvuru kapandı.", show_alert=True)
        try:
            await q.edit_message_reply_markup(None)
        except Exception:
            pass
        return
    if row["status"] == st.SENT:
        await q.answer("Bu maça zaten başvurdun.", show_alert=True)
        return
    if row["status"] not in st.RETRYABLE:
        await q.answer("Bu maç zaten işlemde.")
        return
    await q.answer("Form hazırlanıyor")
    try:
        await q.edit_message_reply_markup(None)
    except Exception:
        pass
    enqueue(context.application, chat_id, fx)


# ==========================================================================
# Uygulama
# ==========================================================================

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("İşlenmeyen hata: %s", type(context.error).__name__, exc_info=context.error)


async def post_init(app: Application):
    r: Runtime = app.bot_data["rt"]
    removed = r.store.reset_inflight()
    if removed:
        log.info("Yarım kalan %d başvuru yeniden ele alınacak", removed)
    await app.bot.set_my_commands([
        ("start", "Başla / yardım"),
        ("ayarlar", "Mod, branşlar, duraklat"),
        ("bilgilerim", "Kayıtlı bilgilerim"),
        ("guncelle", "Bilgilerimi baştan gir"),
        ("basvurular", "Son başvurularım"),
        ("durum", "Bot çalışıyor mu?"),
        ("sil", "Tüm verilerimi sil"),
        ("iptal", "İşlemi iptal et"),
    ])
    for admin in r.config.admin_ids:
        await safe_send(app, admin, "🟢 <b>Bot başladı.</b> Durum için /durum")
    if not r.config.admin_ids:
        log.warning("ADMIN_IDS boş: davet kodu üretilemez. /kimim ile chat ID'ni öğrenip .env'e yaz.")


async def post_shutdown(app: Application):
    """Düzgün kapanışta (Ctrl+C, docker stop) yöneticilere haber verir.
    Elektrik kesilmesi / çökme gibi ani kapanışlarda çalışmaz; onu /durum'a cevap gelmemesinden anlarsın."""
    r: Runtime = app.bot_data["rt"]
    for admin in r.config.admin_ids:
        await safe_send(app, admin, "🔴 <b>Bot kapanıyor.</b>")


def build_app(config: Config, store: st.Storage | None = None) -> Application:
    store = store or st.Storage(config.db_path, config.secret_key)
    app = Application.builder().token(config.token).post_init(post_init).post_shutdown(post_shutdown).build()
    app.bot_data["rt"] = Runtime(config=config, store=store)

    text = filters.TEXT & ~filters.COMMAND
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("guncelle", guncelle)],
        states={
            INVITE: [MessageHandler(text, got_invite)],
            CONSENT: [MessageHandler(text, got_consent)],
            NAME: [MessageHandler(text, got_name)],
            SURNAME: [MessageHandler(text, got_surname)],
            TC: [MessageHandler(text, got_tc)],
            BIRTH: [MessageHandler(text, got_birth)],
            PHONE: [MessageHandler(text, got_phone)],
            BOIB: [MessageHandler(text, got_boib)],
            ATTENDANT: [MessageHandler(text, got_attendant)],
            ATT_TC: [MessageHandler(text, got_att_tc)],
            ATT_NAME: [MessageHandler(text, got_att_name)],
            BRANCHES: [MessageHandler(text, got_branches)],
            MODE: [MessageHandler(text, got_mode)],
            CONFIRM: [MessageHandler(text, got_confirm)],
        },
        fallbacks=[CommandHandler("iptal", cancel), CommandHandler("start", start)],
        conversation_timeout=30 * 60,
    )
    private = filters.ChatType.PRIVATE

    app.add_handler(conv)
    app.add_handler(CommandHandler("ayarlar", ayarlar, filters=private))
    app.add_handler(CommandHandler("bilgilerim", bilgilerim, filters=private))
    app.add_handler(CommandHandler("basvurular", basvurular, filters=private))
    app.add_handler(CommandHandler("sil", sil, filters=private))
    app.add_handler(CommandHandler("kimim", kimim))
    app.add_handler(CommandHandler("durum", durum, filters=private))
    app.add_handler(CommandHandler("iptal", cancel))
    app.add_handler(CommandHandler("davet", davet, filters=private))
    app.add_handler(CommandHandler("kullanicilar", kullanicilar, filters=private))
    app.add_handler(CommandHandler("kontrol", kontrol, filters=private, block=False))
    app.add_handler(CommandHandler("duyuru", duyuru, filters=private, block=False))
    # Ağa çıkan işleyiciler block=False: bir kullanıcının gönderimi diğerlerini bekletmesin
    app.add_handler(MessageHandler(text & private, on_captcha_text, block=False))
    app.add_handler(CallbackQueryHandler(on_settings, pattern=r"^set:"))
    app.add_handler(CallbackQueryHandler(on_delete, pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(on_captcha_button, pattern=r"^cap:", block=False))
    app.add_handler(CallbackQueryHandler(on_approval, pattern=r"^(ap|sk):", block=False))
    app.add_error_handler(on_error)

    app.job_queue.run_repeating(check_job, interval=config.check_interval, first=10, name="check")
    app.job_queue.run_repeating(news_job, interval=config.news_interval, first=30, name="news")
    return app
