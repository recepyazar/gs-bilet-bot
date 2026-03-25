# 🔴⚽ Galatasaray Engelli Bilet Takip Botu

Galatasaray engelli bilet formunu her 5 dakikada otomatik kontrol eder.  
Formda futbol karşılaşması çıktığında anında Telegram bildirimi gönderir.

---

## Kurulum (10 dakika)

### 1. Bu repoyu GitHub'a yükle

1. [github.com](https://github.com) → **New repository** → isim ver (örn: `gs-bilet-bot`)
2. Repoyu **Private** yap (token güvenliği için)
3. Dosyaları yükle:
   - `check_bilet.py`
   - `.github/workflows/check.yml`

### 2. Telegram bilgilerini ekle

GitHub reposunda: **Settings → Secrets and variables → Actions → New repository secret**

| Secret Adı | Değer |
|---|---|
| `TELEGRAM_TOKEN` | BotFather'dan aldığın token |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID'n |

### 3. GitHub Actions'ı etkinleştir

- Repo sayfasında **Actions** sekmesine git
- "I understand my workflows, go ahead and enable them" butonuna tıkla
- **GS Engelli Bilet Takip** workflow'una gir → **Run workflow** ile test et

---

## Nasıl Çalışır?

```
Her 5 dakika
    ↓
galatasaray.org/engelli-bilet-formu sayfasını çek
    ↓
Karşılaşma seçeneği var mı?
    ↓ EVET (ve daha önce görülmemişse)
Telegram'a bildirim gönder 🔔
```

---

## Telegram Bot Nasıl Alınır?

1. Telegram'da **@BotFather**'ı aç
2. `/newbot` yaz ve talimatları izle
3. Sana verilen token'ı kopyala

**Chat ID almak için:**
1. Bota bir mesaj gönder
2. Tarayıcıda aç: `https://api.telegram.org/botTOKEN/getUpdates`
3. `"chat":{"id":` yazan sayıyı al

---

## Notlar

- GitHub Actions ücretsiz hesapta ayda 2.000 dakika ücretsiz (bu bot ~300 dk/ay kullanır)
- Kişisel repolarda her 5 dakika cron çalışır
- Karşılaşma bir kez bildirilir, tekrar bildirim gelmez (state tutulur)
