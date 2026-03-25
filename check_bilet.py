import requests
from bs4 import BeautifulSoup
import os
import json
import sys

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

URL = "https://www.galatasaray.org/engelli-bilet-formu"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    r = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()
    print(f"[+] Telegram mesajı gönderildi.")


def fetch_matches() -> list[str]:
    """Sayfadaki karşılaşma seçeneklerini döndürür."""
    r = requests.get(URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    matches = []

    # Tüm <select> elementlerini tara
    for select in soup.find_all("select"):
        name = (select.get("name") or select.get("id") or "").lower()
        # Karşılaşma ile ilgili select'i bul
        if any(kw in name for kw in ["karsil", "match", "mac", "karşılaşma", "oyun"]):
            for option in select.find_all("option"):
                val = option.get("value", "").strip()
                text = option.get_text(strip=True)
                if val and val not in ("0", "", "-1") and text:
                    matches.append(text)

    # Eğer name ile bulamazsak, içerik bazlı ara
    if not matches:
        for select in soup.find_all("select"):
            options = select.find_all("option")
            # Futbol karşılaşması içeren bir option varsa (vs, - gibi ayraçlar)
            for option in options:
                text = option.get_text(strip=True)
                val = option.get("value", "").strip()
                if val and val not in ("0", "", "-1"):
                    # Futbol maçı formatı: "Galatasaray - X" veya tarih içeriyorsa
                    if ("-" in text or "vs" in text.lower() or "galatasaray" in text.lower()):
                        matches.append(text)

    return list(set(matches))  # Tekrarları kaldır


def load_state() -> set:
    state_file = "last_matches.json"
    if os.path.exists(state_file):
        with open(state_file) as f:
            return set(json.load(f))
    return set()


def save_state(matches: list):
    with open("last_matches.json", "w") as f:
        json.dump(list(matches), f, ensure_ascii=False)


def main():
    print(f"[*] {URL} kontrol ediliyor...")

    try:
        current_matches = fetch_matches()
    except Exception as e:
        print(f"[!] Sayfa çekilemedi: {e}")
        sys.exit(1)

    print(f"[*] Bulunan karşılaşmalar: {current_matches}")

    previous_matches = load_state()
    new_matches = [m for m in current_matches if m not in previous_matches]

    if new_matches:
        print(f"[+] YENİ karşılaşma bulundu: {new_matches}")
        lines = "\n".join(f"  🏟 {m}" for m in new_matches)
        message = (
            "🔴⚽ <b>Galatasaray Engelli Bilet Formu — Yeni Karşılaşma!</b>\n\n"
            f"{lines}\n\n"
            f"👉 <a href='{URL}'>Forma gitmek için tıkla</a>"
        )
        send_telegram(message)
        save_state(set(current_matches))
    elif not current_matches:
        print("[*] Şu anda formda karşılaşma bulunmuyor.")
        # Önceden karşılaşma varken şimdi yoksa state'i temizle
        if previous_matches:
            save_state([])
    else:
        print(f"[*] Yeni karşılaşma yok. Mevcut: {current_matches}")
        save_state(set(current_matches))


if __name__ == "__main__":
    main()
