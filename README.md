# 🔴🟡 Galatasaray Gazi ve Engelli Bilet Botu

Çok kullanıcılı Telegram botu. Davetle katılan herkes bilgilerini bir kez
girer. Galatasaray gazi/engelli bilet formunda yeni bir maç açılınca bot
herkese ayrı ayrı haber verir ve formu o kişinin bilgileriyle doldurur.

Formda 6 karakterlik bir görsel captcha var. Bot bunu kendisi çözmez: resmi
sana yollar, sen yazarsın, form ~1 saniyede gönderilir.

---

## Kullanıcı gözünden

```
Davet linkine tıkla → /start
  KVKK onayı → ad, soyad, TC, doğum tarihi, telefon*, BOİB*, refakatçi*
  → hangi branşlar? → mod seç → özet → kaydet          (* atlanabilir)
```

TC, doğum tarihi ve telefon yazdığın mesajları bot hemen sohbetten siler.

**İki mod var.** Sonradan `/ayarlar`'dan değiştirebilirsin.

| Mod | Maç açılınca |
|---|---|
| ⚡ **Otomatik** | Form hemen hazırlanır, captcha resmi gelir. 6 karakteri yazarsın, gönderilir. |
| 🙋 **Önce sor** | "X maçı açıldı, başvurayım mı?" gelir. **✅ Başvur** dersen captcha gelir, **⏭ Geç** dersen o maç atlanır. |

Captcha için 10 dakika bekler. Süre dolarsa ya da gönderim başarısız olursa
**🔁 Tekrar dene** düğmesi gelir. Aynı anda birden çok maç açılırsa sırayla,
birer birer sorar.

| Komut | |
|---|---|
| `/ayarlar` | Mod, branşlar (dokunarak aç/kapat), duraklat/devam |
| `/bilgilerim` | Kayıtlı bilgiler (maskeli) |
| `/guncelle` | Bilgileri baştan gir |
| `/basvurular` | Son 10 maç ve durumu |
| `/durum` | Bot çalışıyor mu, son kontrol ne zaman, açık maçlar |
| `/sil` | Tüm verileri kalıcı olarak sil |
| `/iptal` | Yarım kalan işlemi iptal et |

**Yönetici:** `/davet [adet]` tek kullanımlık davet linki üretir
(`t.me/<bot>?start=<kod>`). `/kullanicilar` kullanıcı listesini gösterir
(kişisel veri yok). `/kontrol` maçları hemen kontrol eder. Bot her
açılışta yöneticiye 🟢, düzgün kapanışta 🔴 mesajı atar. Ani çökmede mesaj
gelmez; o durumda `/durum`'a cevap gelmemesinden anlarsın.

---

## Kurulum

### 1. Telegram botu
@BotFather → `/newbot` → token'ı al.

### 2. `.env`
```bash
cp .env.example .env
python -m gsbot anahtar        # çıkan değeri GS_SECRET_KEY'e yaz
```
`TELEGRAM_TOKEN` ve `GS_SECRET_KEY`'i doldur. Botu bir kez çalıştırıp
bota `/kimim` yaz, çıkan chat ID'ni `ADMIN_IDS`'e yaz ve yeniden başlat.

> ⚠️ **`GS_SECRET_KEY`'i kaybetme.** Kaybolursa kayıtlı herkesin bilgisi
> çözülemez, herkes yeniden kayıt olmak zorunda kalır. `.env` ve `data/`
> klasörünü birlikte yedekle, ama ayrı yerlerde sakla.

### 3. Çalıştır

**Docker ile (önerilen):**
```bash
docker compose up -d --build
docker compose logs -f
```
Veritabanı `./data/gsbot.db` dosyasında durur, konteyner silinse de kaybolmaz.

**Docker'sız:**
```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m gsbot
```

Bot sürekli açık bir makinede çalışmalı: ucuz bir VPS (Hetzner, Oracle Cloud
ücretsiz katman vb.) ya da evde sürekli açık bir bilgisayar/Raspberry Pi.
Webhook gerekmez, bot long polling kullanır. Sunucunun dışarıya port
açmasına gerek yok.

| Ayar | Varsayılan | |
|---|---|---|
| `CHECK_INTERVAL` | `120` | Maç kontrol aralığı (sn) |
| `CAPTCHA_WAIT` | `600` | Captcha bekleme süresi (sn) |
| `DB_PATH` | `data/gsbot.db` | SQLite dosyası |

### 4. Bot çökerse haber al (isteğe bağlı)

Bot kapalıyken `/durum`'a cevap veremez. Bunu dışarıdan izlemek için:

1. [healthchecks.io](https://healthchecks.io)'ya ücretsiz üye ol → **Add Check**
2. **Period: 5 dakika**, **Grace: 5 dakika** yap (bot 2 dakikada bir sinyal yollar)
3. **Integrations → Telegram** → çıkan linkle healthchecks botunu Telegram'da başlat
4. Check'in ping adresini (`https://hc-ping.com/...`) `.env`'e `HEALTHCHECK_URL` olarak yaz, botu yeniden başlat

Bot ~10 dakika sinyal göndermezse healthchecks sana Telegram'dan yazar,
geri gelince de haber verir. Docker'daki `restart: unless-stopped` ayarı
çöken botu zaten kendiliğinden yeniden başlatır; 🟢 mesajı gelir.

---

## Nasıl çalışır?

Maçlar formun HTML'inde değil, sayfanın JavaScript'inin çağırdığı bir uçta:

```
POST https://www.galatasaray.org/disabledticked/fixtures   branch=1..6
  → [{"Value":"<fixtureId>","Text":"<karşılaşma>"}]
```

Bot `CHECK_INTERVAL`'da bir 6 branşı tarar. Her aktif kullanıcı için, takip
ettiği branştaki ve ona daha önce bildirilmemiş maçları bulur. Sonradan
katılan biri de o an açık olan maçlar için bildirim alır. Her başvuru kendi
oturumuyla hazırlanır (anti-forgery token + captcha oturuma bağlıdır).

```
gsbot/
  site.py        galatasaray.org istemcisi (maçlar, form, captcha, sonuç)
  storage.py     SQLite: kullanıcılar (şifreli profil), davetler, başvurular
  bot.py         Telegram: kayıt sohbeti, ayarlar, kontrol döngüsü, captcha akışı
  validators.py  TC/tarih/telefon/branş doğrulama, maskeleme
  config.py      .env okuma
```

Bot yeniden başlarsa bellekteki captcha oturumları kaybolur. O maçlar,
hâlâ açıksa, bir sonraki kontrolde yeniden ele alınır.

---

## Güvenlik ve KVKK

Başkalarının TC kimlik numarası ve doğum tarihini saklıyorsun. Bu
sorumluluk sende:

- Profiller veritabanında **Fernet (AES) ile şifreli**. Anahtar sadece
  `.env`'de duruyor.
- Kayıtta açık rıza metni gösteriliyor. Kullanıcı `/sil` ile her şeyi
  silebiliyor.
- Log'lara kişisel veri yazılmaz. httpx'in token'lı URL logları kapalı.
- Kayıt davetle yapılıyor. Kodlar tek kullanımlık.
- `.env` ve `data/` git'e girmez (`.gitignore`).

---

## Testler

```bash
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest
```

Hiçbir test ağa çıkmaz. Telegram ve galatasaray.org taklit edilir. Kapsam:
doğrulayıcılar, site istemcisi (payload, ayrıştırma, sonuç okuma),
şifreli depolama, tek kullanımlık davet, bildirim planlama, ve uçtan uca
akış (otomatik/onay modu, captcha sırası, süre dolması, hatalı captcha,
site çökünce yöneticiye tek uyarı). CI her push'ta Python 3.11 ve 3.12'de koşar.
