"""galatasaray.org "gazi ve engelli taraftar bilet listesi" duyurularını okur.

Duyurular /haber/<numara> adresinde. Her duyurunun altında aynı kategorideki
(RAMS Park) bir sonraki duyuruya giden "SONRAKİ HABER" linki var; bot son
bilinen duyurudan bu zinciri takip ederek yeni duyuruları bulur.

Liste duyurusu biçimi (Ekim 2026):
    başlık : "Fenerbahçe maçı gazi ve engelli taraftar bilet listesi"
    giriş  : "... 26 Ekim Pazartesi günü oynayacağımız Fenerbahçe maçında yer almaya
              hak kazanan gazi ve engelli taraftarlarımızın listesi aşağıdaki gibidir."
    sonra  : satır satır büyük harfle AD SOYAD

Bu modül listeki isimleri hiçbir yere kaydetmez; sadece eşleştirme için kullanır.
"""

import re
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

from .site import BASE, HEADERS

NEWS_URL = BASE + "/haber/{id}"

LIST_TITLE_RE = re.compile(r"gazi\s+ve\s+engelli.*listes", re.I)
MONTHS = {
    "OCAK": 1, "ŞUBAT": 2, "MART": 3, "NİSAN": 4, "MAYIS": 5, "HAZİRAN": 6,
    "TEMMUZ": 7, "AĞUSTOS": 8, "EYLÜL": 9, "EKİM": 10, "KASIM": 11, "ARALIK": 12,
}
DATE_RE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\b")
# Rakip adını eşleştirirken anlamsız kalan kelimeler
STOP_WORDS = {"GALATASARAY", "MAÇI", "MAÇ", "SPOR", "KULÜBÜ", "BASKETBOL", "VOLEYBOL", "KADIN", "ERKEK"}
FOOTER_WORDS = {"TWEET", "ÖNCEKİ HABER", "SONRAKİ HABER", "PAYLAŞ"}


@dataclass
class Article:
    id: int
    title: str
    lines: list[str] = field(default_factory=list)
    next_id: int | None = None

    @property
    def url(self) -> str:
        return NEWS_URL.format(id=self.id)

    @property
    def is_winner_list(self) -> bool:
        return bool(LIST_TITLE_RE.search(self.title))

    @property
    def intro(self) -> str:
        """İsim listesinden önceki açıklama cümlesi."""
        for line in self.lines:
            if "hak kazanan" in line.lower() or "listesi aşağıda" in line.lower():
                return line
        return ""


# --------------------------------------------------------------------------
# Metin yardımcıları
# --------------------------------------------------------------------------

def tr_upper(text: str) -> str:
    """Türkçe büyük harf: i→İ, ı→I. Boşlukları (nbsp dahil) teke indirir."""
    text = text.replace("i", "İ").replace("ı", "I").upper()
    return " ".join(text.replace("\xa0", " ").split())


def parse_date(text: str) -> tuple[int, int] | None:
    """'26 Ekim' / '3 Kasım 2026' → (gün, ay)"""
    m = DATE_RE.search(tr_upper(text))
    return (int(m.group(1)), MONTHS[m.group(2)]) if m else None


def name_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[A-ZÇĞİÖŞÜÂÎÛ]+", tr_upper(text)) if len(w) >= 4 and w not in STOP_WORDS}


# --------------------------------------------------------------------------
# Sayfa okuma
# --------------------------------------------------------------------------

def parse_article(html_text: str, news_id: int) -> Article | None:
    soup = BeautifulSoup(html_text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    title = re.sub(r"\s*-\s*GALATASARAY\.ORG\s*$", "", title, flags=re.I).strip()
    box = soup.select_one(".single_content_full")
    if not box or not title or "SAYFA BULUNAMADI" in title.upper():
        return None

    next_id = None
    for a in box.find_all("a", href=True):
        m = re.search(r"/(\d+)/?$", a["href"])
        if m and "SONRAKİ" in tr_upper(a.get_text()):
            next_id = int(m.group(1))
        a.decompose()                       # link metinleri isim sanılmasın
    for tag in box(["script", "style"]):
        tag.decompose()
    lines = [" ".join(l.replace("\xa0", " ").split()) for l in box.get_text("\n", strip=True).split("\n")]
    return Article(id=news_id, title=title, lines=[l for l in lines if l], next_id=next_id)


def fetch_article(news_id: int, session: requests.Session | None = None) -> Article | None:
    session = session or requests.Session()
    r = session.get(NEWS_URL.format(id=news_id), headers=HEADERS, timeout=20)
    r.raise_for_status()
    return parse_article(r.text, news_id)


def fetch_new_articles(cursor: int, limit: int = 10,
                       session: requests.Session | None = None) -> tuple[list[Article], int]:
    """`cursor` duyurusundan "sonraki" zincirini takip eder.

    (yeni duyurular, yeni cursor) döndürür. Cursor duyurusu silinmişse olduğu gibi kalır.
    """
    session = session or requests.Session()
    current = fetch_article(cursor, session)
    found = []
    while current and current.next_id and len(found) < limit:
        nxt = fetch_article(current.next_id, session)
        if not nxt:
            break
        found.append(nxt)
        current = nxt
    return found, (found[-1].id if found else cursor)


# --------------------------------------------------------------------------
# Liste ve eşleştirme
# --------------------------------------------------------------------------

def list_names(article: Article) -> set[str]:
    """Giriş cümlesinden sonraki isim satırları (normalize edilmiş)."""
    intro = article.intro
    try:
        start = article.lines.index(intro) + 1 if intro else 0
    except ValueError:
        start = 0
    names = set()
    for line in article.lines[start:]:
        up = tr_upper(line)
        if up in FOOTER_WORDS:
            continue
        words = up.split()
        if 2 <= len(words) <= 6 and len(up) <= 60 and re.fullmatch(r"[A-ZÇĞİÖŞÜÂÎÛ' .-]+", up):
            names.add(up)
    return names


def match_person(profile: dict, names: set[str]) -> str | None:
    """'exact' — ad soyad birebir; 'similar' — ilk ad + soyad tutuyor (ikinci ad farkı); None."""
    full = tr_upper(f"{profile['name']} {profile['surname']}")
    if full in names:
        return "exact"
    first = full.split()[0]
    last = tr_upper(profile["surname"]).split()[-1]
    for n in names:
        parts = n.split()
        if parts[0] == first and parts[-1] == last:
            return "similar"
    return None


def fixture_matches(article: Article, fixture_text: str) -> bool:
    """Duyuru bu karşılaşmaya mı ait? Rakip adı + (varsa) tarih tutmalı."""
    opponent = article.title.split(" maçı")[0] if " maçı" in article.title else article.title
    if not (name_tokens(opponent) & name_tokens(fixture_text)):
        return False
    d1, d2 = parse_date(article.intro), parse_date(fixture_text)
    return d1 == d2 if (d1 and d2) else True
