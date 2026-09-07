# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import Flask, Response, jsonify, request, send_from_directory
import requests
import os
import json
import gzip
import time
import threading
import re
import html as html_lib
from html.parser import HTMLParser
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

app = Flask(__name__, static_folder=".", static_url_path="")

TV_URL = "https://scanner.tradingview.com/turkey/scan"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# KAP public website endpoints. Hikâye Radar uses these only at low frequency and with cache.
KAP_BASE = "https://www.kap.org.tr"
KAP_COMPANIES_URL = KAP_BASE + "/tr/api/company/items/IGS/A"
KAP_DISCLOSURES_URL = KAP_BASE + "/tr/api/disclosure/members/byCriteria"
KAP_DETAIL_URL = KAP_BASE + "/tr/api/notification/attachment-detail/{index}"
KAP_COMPANY_LIST_PAGE = KAP_BASE + "/tr/bist-sirketler"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Encoding": "gzip, deflate",
})

# ---------------------------------------------------------------------
# Bant genişliği koruması
# ---------------------------------------------------------------------
# 1) Yahoo cevabını frontend'in kullandığı minimum yapıya indirger.
# 2) JSON'u gzip ile sıkıştırır.
# 3) Aynı symbol/range/interval isteğini RAM'de TTL ile önbelleğe alır.
# 4) GET /api/yahoo için tarayıcı cache başlığı verir.
# 5) /api/metrics ile hangi uç ne kadar veri göndermiş görülebilir.
#
# Not: RAM cache deploy/restart sonrası sıfırlanır; veri doğruluğunu bozmaz.

MAX_CACHE_ITEMS = int(os.environ.get("BIST_CACHE_ITEMS", "1600"))

_cache_lock = threading.Lock()
_cache: "OrderedDict[str, tuple[float, bytes, str]]" = OrderedDict()

_metrics_lock = threading.Lock()
_metrics = {
    "started_at": time.time(),
    "requests": defaultdict(int),
    "bytes_out_raw": defaultdict(int),
    "bytes_out_wire": defaultdict(int),
    "cache_hit": defaultdict(int),
    "cache_miss": defaultdict(int),
}


def _metric_add(bucket: str, key: str, value: int = 1):
    with _metrics_lock:
        _metrics[bucket][key] += value


def _cache_get(key: str):
    now = time.time()
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        expires_at, body, ctype = item
        if expires_at <= now:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return body, ctype


def _cache_set(key: str, ttl: int, body: bytes, ctype: str):
    with _cache_lock:
        _cache[key] = (time.time() + ttl, body, ctype)
        _cache.move_to_end(key)
        while len(_cache) > MAX_CACHE_ITEMS:
            _cache.popitem(last=False)


def _istanbul_market_open() -> bool:
    """Yaklaşık BIST seans filtresi. Tatil takvimi içermez."""
    now = datetime.now(ZoneInfo("Europe/Istanbul"))
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return (9 * 60 + 40) <= mins <= (18 * 60 + 15)


def _yahoo_ttl(interval: str) -> int:
    market_open = _istanbul_market_open()
    interval = (interval or "1d").lower()

    # Günlük/haftalık geçmiş veri piyasa açıkken bile dakika dakika değişmek zorunda değil.
    if interval.endswith("d") or interval.endswith("wk") or interval.endswith("mo"):
        return 15 * 60 if market_open else 8 * 60 * 60

    # Intraday taramalar daha taze kalsın.
    return 60 if market_open else 8 * 60 * 60


def _browser_max_age(interval: str) -> int:
    market_open = _istanbul_market_open()
    interval = (interval or "1d").lower()
    if interval.endswith("d") or interval.endswith("wk") or interval.endswith("mo"):
        return 5 * 60 if market_open else 2 * 60 * 60
    return 30 if market_open else 2 * 60 * 60


def _minify_json_bytes(obj) -> bytes:
    return json.dumps(
        obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _compact_yahoo(payload: dict) -> dict:
    """
    Frontend'in mevcut yahoo() fonksiyonuyla uyumlu minimum Yahoo Chart cevabı.
    index.html yalnızca:
      chart.result[0].timestamp
      chart.result[0].indicators.quote[0].open/high/low/close/volume
    alanlarını kullanıyor.
    """
    chart = payload.get("chart") or {}
    result = chart.get("result")
    if not result:
        return {
            "chart": {
                "result": None,
                "error": chart.get("error") or {"description": "Yahoo veri yok"}
            }
        }

    r = result[0] or {}
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}

    minimal_q = {
        "open": q.get("open") or [],
        "high": q.get("high") or [],
        "low": q.get("low") or [],
        "close": q.get("close") or [],
        "volume": q.get("volume") or [],
    }

    return {
        "chart": {
            "result": [{
                "timestamp": r.get("timestamp") or [],
                "indicators": {"quote": [minimal_q]},
            }],
            "error": chart.get("error"),
        }
    }


def _respond_bytes(
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "application/json",
    cache_control: str | None = None,
    metric_key: str = "other",
    extra_headers: dict | None = None,
):
    raw_len = len(body)
    accept = request.headers.get("Accept-Encoding", "")
    use_gzip = "gzip" in accept.lower() and raw_len >= 700

    wire = gzip.compress(body, compresslevel=6) if use_gzip else body

    resp = Response(wire, status=status, content_type=content_type)
    if use_gzip:
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Vary"] = "Accept-Encoding"

    if cache_control:
        resp.headers["Cache-Control"] = cache_control

    if extra_headers:
        for k, v in extra_headers.items():
            resp.headers[k] = str(v)

    resp.headers["X-Raw-Bytes"] = str(raw_len)
    resp.headers["X-Wire-Bytes"] = str(len(wire))

    _metric_add("requests", metric_key)
    _metric_add("bytes_out_raw", metric_key, raw_len)
    _metric_add("bytes_out_wire", metric_key, len(wire))
    return resp


@app.get("/")
def home():
    return send_from_directory(".", "index.html")


@app.post("/api/tradingview")
def tradingview_proxy():
    metric_key = "tradingview"
    body_obj = request.get_json(force=True)
    # Aynı tarama gövdesi kısa süre içinde tekrar gelirse upstream'i tekrar çağırmayalım.
    cache_key = "tv:" + json.dumps(body_obj, sort_keys=True, separators=(",", ":"))
    cached = _cache_get(cache_key)

    if cached:
        _metric_add("cache_hit", metric_key)
        body, ctype = cached
        return _respond_bytes(
            body,
            content_type=ctype,
            cache_control="no-store",
            metric_key=metric_key,
            extra_headers={"X-BIST-Cache": "HIT"},
        )

    _metric_add("cache_miss", metric_key)
    try:
        r = SESSION.post(
            TV_URL,
            json=body_obj,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )

        try:
            # TV JSON'unu boşluklardan arındır.
            body = _minify_json_bytes(r.json())
            ctype = "application/json"
        except Exception:
            body = r.content
            ctype = r.headers.get("Content-Type", "application/json")

        if r.ok:
            _cache_set(cache_key, 60, body, ctype)

        return _respond_bytes(
            body,
            status=r.status_code,
            content_type=ctype,
            cache_control="no-store",
            metric_key=metric_key,
            extra_headers={"X-BIST-Cache": "MISS"},
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/api/yahoo")
def yahoo_proxy():
    metric_key = "yahoo"
    symbol = request.args.get("symbol", "").strip()
    range_ = request.args.get("range", "2y").strip()
    interval = request.args.get("interval", "1d").strip()

    if not symbol:
        return jsonify({"error": "symbol gerekli"}), 400

    ttl = _yahoo_ttl(interval)
    browser_age = _browser_max_age(interval)
    cache_key = f"yahoo:{symbol}|{range_}|{interval}"

    cached = _cache_get(cache_key)
    if cached:
        _metric_add("cache_hit", metric_key)
        body, ctype = cached
        return _respond_bytes(
            body,
            content_type=ctype,
            cache_control=f"public, max-age={browser_age}, stale-while-revalidate={browser_age}",
            metric_key=metric_key,
            extra_headers={"X-BIST-Cache": "HIT"},
        )

    _metric_add("cache_miss", metric_key)

    try:
        r = SESSION.get(
            YAHOO_URL.format(symbol=symbol),
            params={
                "range": range_,
                "interval": interval,
                "includePrePost": "false",
                # events gönderilmiyor; mevcut frontend dividend/split event alanını kullanmıyor.
            },
            timeout=30,
        )

        ctype = "application/json"

        try:
            original = r.json()
            compact = _compact_yahoo(original)
            body = _minify_json_bytes(compact)
        except Exception:
            # Hata cevabı JSON değilse olduğu gibi ilet.
            body = r.content
            ctype = r.headers.get("Content-Type", "application/json")

        if r.ok:
            _cache_set(cache_key, ttl, body, ctype)

        return _respond_bytes(
            body,
            status=r.status_code,
            content_type=ctype,
            cache_control=f"public, max-age={browser_age}, stale-while-revalidate={browser_age}",
            metric_key=metric_key,
            extra_headers={"X-BIST-Cache": "MISS"},
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


# ---------------------------------------------------------------------
# Hikâye Radar v0.3 · KAP katalizör + finansal doğrulama katmanı
# ---------------------------------------------------------------------
# Bu katman KAP'ın herkese açık web uçlarını yalnızca düşük yoğunlukta kullanır.
# Şirket listesi 24 saat, hisse bazlı hikâye sonucu 6 saat cache'lenir.
# KAP katalizörünün yanında en son finansal rapordaki cari/önceki dönem
# karşılaştırmalarından bilanço (0-25) ve kârlılık/nakit (0-15) puanı üretir.

_STORY_LOOKBACK_DAYS = int(os.environ.get("STORY_LOOKBACK_DAYS", "180"))
_STORY_TTL = int(os.environ.get("STORY_CACHE_SECONDS", str(6 * 60 * 60)))
_KAP_COMPANY_TTL = int(os.environ.get("KAP_COMPANY_CACHE_SECONDS", str(24 * 60 * 60)))

_STORY_RULES = [
    ("Yeni İş / Sözleşme", 12, [
        "yeni iş", "sözleşme", "ihale", "sipariş", "iş ilişkisi", "proje",
        "satış sözleşmesi", "tedarik sözleşmesi", "yüklenici", "iş alımı"
    ]),
    ("Yatırım / Kapasite", 13, [
        "yatırım", "kapasite", "tesis", "fabrika", "üretim hattı", "devreye alma",
        "modernizasyon", "kapasite artışı", "yatırım teşvik"
    ]),
    ("İhracat / Yeni Pazar", 11, [
        "ihracat", "yeni pazar", "yurt dışı", "yurtdışı", "uluslararası",
        "distribütör", "dağıtım anlaşması"
    ]),
    ("Teşvik / Regülasyon", 10, [
        "teşvik", "destek", "hibe", "ruhsat", "lisans", "izin", "regülasyon",
        "vergi istisnası", "yatırım teşvik belgesi"
    ]),
    ("Yeni Ürün / Teknoloji", 10, [
        "yeni ürün", "ürün geliştirme", "teknoloji", "ar-ge", "arge", "patent",
        "yazılım", "platform", "ürün lansmanı"
    ]),
    ("Satın Alma / Ortaklık", 13, [
        "satın alma", "devralma", "birleşme", "ortaklık", "iştirak", "pay alımı",
        "stratejik ortak", "şirket satın"
    ]),
    ("Borç Azaltma / Finansal Dönüşüm", 9, [
        "borç", "refinansman", "kredi kapama", "borç ödeme", "finansal yeniden yapılandırma",
        "sermaye güçlendirme"
    ]),
]

_NEGATIVE_HINTS = [
    "iptal", "fesih", "sona er", "olumsuz", "zarar", "ceza", "dava", "temerrüt",
    "konkordato", "iflas", "faaliyet durdur", "üretime ara"
]

def _story_symbol(raw: str) -> str:
    s = (raw or "").strip().upper().replace("BIST:", "")
    if s.endswith(".IS"):
        s = s[:-3]
    return s if re.fullmatch(r"[A-Z0-9]{2,12}", s) else ""

def _cache_json_get(key: str):
    item = _cache_get(key)
    if not item:
        return None
    body, _ctype = item
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None

def _cache_json_set(key: str, ttl: int, obj):
    _cache_set(key, ttl, _minify_json_bytes(obj), "application/json")

def _kap_headers(referer: str) -> dict:
    return {
        "Referer": referer,
        "Accept": "application/json,text/plain,*/*",
        "Content-Type": "application/json",
    }

def _kap_companies():
    key = "kap:companies:IGS:A"
    cached = _cache_json_get(key)
    if cached is not None:
        return cached
    r = SESSION.get(
        KAP_COMPANIES_URL,
        timeout=15,
        headers=_kap_headers(KAP_BASE + "/tr/bildirim-sorgu"),
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError("KAP şirket listesi beklenen formatta değil")
    _cache_json_set(key, _KAP_COMPANY_TTL, data)
    return data

def _kap_find_company(symbol: str):
    for c in _kap_companies():
        raw = str(c.get("stockCode") or c.get("stockCodes") or "")
        codes = {x.strip().upper() for x in re.split(r"[,;/\s]+", raw) if x.strip()}
        if symbol in codes:
            return c
    return None

def _kap_criteria(oid: str, days: int):
    end = datetime.now(ZoneInfo("Europe/Istanbul")).date()
    start = end - timedelta(days=max(30, min(365, days)))
    return {
        "fromDate": start.isoformat(),
        "toDate": end.isoformat(),
        "memberType": "IGS",
        "mkkMemberOidList": [oid] if oid else [],
        "inactiveMkkMemberOidList": [],
        "disclosureClass": "",
        "subjectList": [],
        "isLate": "",
        "mainSector": "",
        "sector": "",
        "subSector": "",
        "marketOid": "",
        "index": "",
        "bdkReview": "",
        "bdkMemberOidList": [],
        "year": "",
        "term": "",
        "ruleType": "",
        "period": "",
        "fromSrc": False,
        "srcCategory": "",
        "disclosureIndexList": [],
    }

def _kap_disclosures(symbol: str, oid: str, days: int):
    r = SESSION.post(
        KAP_DISCLOSURES_URL,
        json=_kap_criteria(oid, days),
        timeout=18,
        headers=_kap_headers(KAP_BASE + "/tr/bildirim-sorgu"),
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError("KAP bildirim listesi beklenen formatta değil")
    # OID filtresi çalışmasa dahi yanlış şirkete puan vermemek için sembolü tekrar doğrula.
    out = []
    for d in data:
        codes = " ".join([str(d.get("stockCodes") or ""), str(d.get("relatedStocks") or "")]).upper()
        if re.search(rf"(^|[^A-Z0-9]){re.escape(symbol)}([^A-Z0-9]|$)", codes):
            out.append(d)
    return out

def _strip_html(v) -> str:
    if isinstance(v, list):
        v = " ".join(str(x) for x in v)
    txt = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", str(v or ""), flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html_lib.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()

def _kap_detail_text(index) -> str:
    if not index:
        return ""
    key = f"kap:detail:{index}"
    cached = _cache_json_get(key)
    if cached is not None:
        return str(cached.get("text") or "")
    url = KAP_DETAIL_URL.format(index=index)
    r = SESSION.get(
        url, timeout=15, headers=_kap_headers(KAP_BASE + f"/tr/Bildirim/{index}")
    )
    r.raise_for_status()
    j = r.json()
    item = j[0] if isinstance(j, list) and j else (j if isinstance(j, dict) else {})
    text = _strip_html(item.get("disclosureBody") or "")
    _cache_json_set(key, 24 * 60 * 60, {"text": text[:30000]})
    return text

def _parse_publish_date(v: str):
    v = str(v or "").strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%Y.%m.%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(v[:19], fmt).replace(tzinfo=ZoneInfo("Europe/Istanbul"))
        except Exception:
            pass
    return None

def _classify_story(text: str):
    low = (text or "").casefold()
    best = ("Diğer", 0, [])
    for story_type, base, words in _STORY_RULES:
        hits = [w for w in words if w.casefold() in low]
        if hits:
            score = base + min(3, len(hits) - 1)
            if score > best[1]:
                best = (story_type, score, hits)
    neg = [w for w in _NEGATIVE_HINTS if w.casefold() in low]
    if neg and best[1] > 0:
        return best[0], max(2, best[1] - 7), best[2], neg
    return best[0], best[1], best[2], neg

def _extract_materiality(text: str) -> str:
    if not text:
        return ""
    pieces = []
    # Açıklamadaki sayısal büyüklükleri yalnız kanıt olarak göster; şirket büyüklüğüne oranlamayı burada uydurma.
    for m in re.finditer(r"(?<!\d)(%\s?\d{1,3}(?:[.,]\d{1,2})?|\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?\s?(?:TL|TRY|USD|EUR|milyon|milyar))", text, flags=re.I):
        val = re.sub(r"\s+", " ", m.group(1)).strip()
        if val not in pieces:
            pieces.append(val)
        if len(pieces) >= 4:
            break
    return " · ".join(pieces)



# ---------------------------------------------------------------------
# Hikâye Radar v0.3 · finansal rapor ayrıştırma / puanlama
# ---------------------------------------------------------------------
_FIN_TTL = int(os.environ.get("STORY_FIN_CACHE_SECONDS", str(12 * 60 * 60)))

class _KapTableParser(HTMLParser):
    """KAP disclosureBody içindeki tabloları bağımlılık eklemeden satır/hücrelere ayırır."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None
        self._cell_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._cell_depth = 1
        elif self._cell is not None:
            self._cell_depth += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None:
            txt = re.sub(r"\s+", " ", " ".join(self._cell)).strip()
            self._row.append(txt)
            self._cell = None
            self._cell_depth = 0
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            t = str(data or "").strip()
            if t:
                self._cell.append(t)


def _kap_detail_json(index):
    if not index:
        return {}
    key = f"kap:detailjson:{index}"
    cached = _cache_json_get(key)
    if cached is not None:
        return cached
    url = KAP_DETAIL_URL.format(index=index)
    r = SESSION.get(url, timeout=20, headers=_kap_headers(KAP_BASE + f"/tr/Bildirim/{index}"))
    r.raise_for_status()
    j = r.json()
    item = j[0] if isinstance(j, list) and j else (j if isinstance(j, dict) else {})
    # Finansal rapor gövdeleri büyük olabilir. Ham gövdeyi genel RAM cache'e yığmak yerine
    # bu fonksiyonu yalnız finansal snapshot oluşturulurken kullanıyoruz.
    return item


def _tr_number(v):
    s = html_lib.unescape(str(v or "")).strip()
    if not s or s in ("-", "—"):
        return None
    s = s.replace("\xa0", " ").replace(" ", "")
    # Hücre yalnız sayı olmalı; tarih, yüzde veya açıklama metnini alma.
    if not re.fullmatch(r"[-+]?\(?\d{1,3}(?:\.\d{3})*(?:,\d+)?\)?|[-+]?\(?\d+(?:,\d+)?\)?", s):
        return None
    neg_paren = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "." in s:
        # KAP TL tablolarında nokta çoğunlukla binlik ayırıcıdır.
        parts = s.split(".")
        s = "".join(parts) if all(len(x) == 3 for x in parts[1:]) else s
    elif "," in s:
        s = s.replace(",", ".")
    try:
        x = float(s)
        return -x if neg_paren else x
    except Exception:
        return None


def _row_numbers(row):
    vals = []
    for c in row:
        x = _tr_number(c)
        if x is not None:
            vals.append(x)
    return vals


def _find_fin_row(rows, taxonomy=None, label=None, prefer_values=2):
    candidates = []
    for row in rows:
        joined = " | ".join(row)
        low = joined.casefold()
        ok = False
        if taxonomy and taxonomy.casefold() in low:
            ok = True
        if label and label.casefold() in low:
            ok = True
        if not ok:
            continue
        nums = _row_numbers(row)
        if len(nums) >= prefer_values:
            candidates.append((len(nums), row, nums))
    if not candidates:
        return None
    # Gelir tablosunda 4 karşılaştırmalı değer olan satırı; bilanço/nakitte en dolu satırı tercih et.
    candidates.sort(key=lambda x: x[0], reverse=True)
    nums = candidates[0][2]
    return nums[-prefer_values:]


def _pct_change(cur, prev):
    if cur is None or prev is None or abs(prev) < 1e-9:
        return None
    return (cur / prev - 1.0) * 100.0


def _safe_ratio(a, b):
    if a is None or b is None or abs(b) < 1e-9:
        return None
    return a / b


def _score_growth(g, cuts=(5, 15, 30), pts=(2, 4, 6, 8)):
    if g is None or g <= 0:
        return 0
    if g >= cuts[2]: return pts[3]
    if g >= cuts[1]: return pts[2]
    if g >= cuts[0]: return pts[1]
    return pts[0]


def _score_profit(cur, prev, max_pts):
    if cur is None or prev is None:
        return 0
    if cur > 0 and prev <= 0:
        return max_pts
    if cur <= 0:
        return 0
    if prev <= 0:
        return max(1, max_pts - 1)
    g = _pct_change(cur, prev)
    if g is None: return 0
    if g >= 30: return max_pts
    if g >= 10: return max(1, max_pts - 2)
    if g > 0: return max(1, max_pts - 4)
    return 1


def _kap_financial_disclosures(symbol: str, oid: str, days: int = 365):
    """KAP'tan yalnız Finansal Rapor (FR) sınıfını ayrı sorgular.

    Hikâye bildirim listesinden finansal rapor seçmek güvenli değildir; ÖDA metninde
    "finansal rapor" ifadesi geçebilir. Bu nedenle KAP kriterine disclosureClass=FR
    verip sonucu ayrıca sembolle doğruluyoruz.
    """
    criteria = _kap_criteria(oid, days)
    criteria["disclosureClass"] = "FR"
    r = SESSION.post(
        KAP_DISCLOSURES_URL,
        json=criteria,
        timeout=18,
        headers=_kap_headers(KAP_BASE + "/tr/bildirim-sorgu"),
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError("KAP finansal bildirim listesi beklenen formatta değil")
    out = []
    for d in data:
        codes = " ".join([str(d.get("stockCodes") or ""), str(d.get("relatedStocks") or "")]).upper()
        if re.search(rf"(^|[^A-Z0-9]){re.escape(symbol)}([^A-Z0-9]|$)", codes):
            out.append(d)
    return out


def _kap_public_financial_rows(index):
    """KAP finansal tabloyu bildirim sayfasının gerçek HTML tablosundan oku.

    attachment-detail ucu bazı FR kayıtlarında finansal XBRL tablo gövdesini
    döndürmeyebiliyor. KAP'ın /tr/Bildirim/<index> sayfası ise tabloyu HTML
    olarak içeriyor; bu yüzden finansal doğrulamada ana kaynak budur.
    """
    if not index:
        return []
    key = f"kap:publicfinrows:v034:{index}"
    cached = _cache_json_get(key)
    if cached is not None:
        return cached.get("rows") or []
    url = KAP_BASE + f"/tr/Bildirim/{index}"
    r = SESSION.get(url, timeout=25, headers={
        "Referer": KAP_BASE + "/tr/bildirim-sorgu",
        "Accept": "text/html,application/xhtml+xml",
    })
    r.raise_for_status()
    parser = _KapTableParser()
    parser.feed(r.text)
    rows = parser.rows
    # Yalnız finansal tablo olduğu doğrulanan sayfayı cache'le.
    blob = "\n".join(" | ".join(row) for row in rows)
    markers = (
        "ifrs-full_Revenue", "ifrs-full_Assets", "ifrs-full_ProfitLoss",
        "kap-fr_StatementOfFinancialPosition", "CashFlowsFromUsedInOperatingActivities",
    )
    hits = sum(m.casefold() in blob.casefold() for m in markers)
    if hits >= 2:
        _cache_json_set(key, _FIN_TTL, {"rows": rows, "taxonomy_hits": hits})
        return rows
    return []


def _latest_financial_disclosure(disclosures):
    """En yeni gerçek Finansal Rapor kaydını KAP bildirim HTML'i ile doğrula."""
    ranked = []
    for d in disclosures:
        subject = str(d.get("subject") or "").strip()
        summary = str(d.get("summary") or "").strip()
        blob = f"{subject} {summary}".casefold()
        dt = _parse_publish_date(d.get("publishDate")) or datetime.min.replace(
            tzinfo=ZoneInfo("Europe/Istanbul")
        )
        pri = 3 if subject.casefold() == "finansal rapor" else 2 if "finansal rapor" in blob else 0
        if any(x in blob for x in ("sorumluluk beyan", "faaliyet raporu", "sürdürülebilirlik")):
            pri = -1
        ranked.append((dt, pri, d))
    ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)

    # En yeni adaylardan public KAP sayfasını aç; gerçek XBRL tablo varsa kabul et.
    for _dt, _pri, d in ranked[:12]:
        idx = d.get("disclosureIndex")
        if not idx:
            continue
        try:
            rows = _kap_public_financial_rows(idx)
        except Exception:
            rows = []
        if rows:
            return d
    return None

def _kap_financial_page_id(company: dict, symbol: str):
    """KAP şirketinin sayısal web sayfası kimliğini mümkün olduğunca sağlam bul."""
    for k in ("kapMemberId", "memberId", "companyId", "id", "kapId", "kapCompanyId"):
        v = str(company.get(k) or "").strip()
        if v.isdigit():
            return v

    # Bazı KAP şirket-listesi sürümleri doğrudan URL/path döndürüyor.
    for k in ("url", "link", "path", "companyUrl", "memberUrl"):
        v = str(company.get(k) or "")
        m = re.search(r"/(?:sirket-finansal-bilgileri|sirket-bilgileri/ozet)/(\d+)(?:-|/|$)", v, re.I)
        if m:
            return m.group(1)

    # Son çare: BIST şirketleri sayfasında sembolün yakınındaki şirket linkini ara.
    # Bu yalnız cache miss'te çalışır; yoğun tarama yapılmaz.
    try:
        key = "kap:bist-company-list-page"
        cached = _cache_json_get(key)
        page = str((cached or {}).get("html") or "")
        if not page:
            r = SESSION.get(KAP_COMPANY_LIST_PAGE, timeout=18, headers={
                "Referer": KAP_BASE + "/tr/", "Accept": "text/html,application/xhtml+xml"
            })
            r.raise_for_status()
            page = r.text
            _cache_json_set(key, _KAP_COMPANY_TTL, {"html": page[:1500000]})
        # Önce sembol çevresindeki 3000 karakteri dene.
        pos = page.upper().find(symbol.upper())
        chunks = [page[max(0, pos-3000):pos+3000]] if pos >= 0 else []
        chunks.append(page)
        for chunk in chunks:
            m = re.search(r"/(?:tr/)?(?:sirket-finansal-bilgileri|sirket-bilgileri/ozet)/(\d+)-[^\"'<> ]+", chunk, re.I)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


class _SimpleHtmlTableParser(HTMLParser):
    """KAP Özet Finansal Bilgiler HTML tablosunu satır/hücre olarak okur."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "tr": self._row = []
        elif t in ("td", "th") and self._row is not None: self._cell = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("td", "th") and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", " ".join(self._cell)).strip())
            self._cell = None
        elif t == "tr" and self._row is not None:
            if self._row: self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            x = str(data or "").strip()
            if x: self._cell.append(x)


def _norm_fin_label(s: str) -> str:
    s = html_lib.unescape(str(s or "")).casefold()
    s = s.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _summary_row(rows, *labels):
    wanted = [_norm_fin_label(x) for x in labels]
    for row in rows:
        if not row: continue
        lab = _norm_fin_label(row[0])
        if any(w == lab or w in lab for w in wanted):
            vals = [_tr_number(x) for x in row[1:]]
            return vals
    return []


def _summary_headers(rows):
    # KAP'ta bölüm başlığı satırı: FİNANSAL DURUM TABLOSU | 2023/12 | ...
    for row in rows:
        if row and "finansal durum tablosu" in _norm_fin_label(row[0]):
            return [str(x).strip() for x in row[1:]]
    # Fallback: dönem biçimi taşıyan en dolu satır.
    best=[]
    for row in rows:
        vals=[str(x).strip() for x in row if re.fullmatch(r"20\d{2}/(?:03|06|09|12)", str(x).strip())]
        if len(vals)>len(best): best=vals
    return best


def _kap_summary_financial_snapshot(symbol: str, company: dict):
    page_id = _kap_financial_page_id(company, symbol)
    if not page_id:
        return {"available": False, "reason": "KAP şirket finansal sayfa kimliği bulunamadı.", "source": "kap_summary"}

    slug = re.sub(r"[^a-z0-9]+", "-", str(company.get("kapMemberTitle") or symbol).casefold()
                  .replace("ı","i").replace("ğ","g").replace("ü","u").replace("ş","s").replace("ö","o").replace("ç","c")).strip("-")
    url = KAP_BASE + f"/tr/sirket-finansal-bilgileri/{page_id}-{slug}"
    key = f"kap:finsummary:v033:{symbol}:{page_id}"
    cached = _cache_json_get(key)
    if cached is not None: return cached

    r = SESSION.get(url, timeout=20, headers={"Referer": KAP_BASE + "/tr/", "Accept": "text/html,application/xhtml+xml"})
    r.raise_for_status()
    parser = _SimpleHtmlTableParser(); parser.feed(r.text)
    rows = parser.rows
    headers = _summary_headers(rows)

    rev = _summary_row(rows, "Hasılat")
    op = _summary_row(rows, "Esas Faaliyet Kârı (Zararı)", "Esas Faaliyet Karı (Zararı)")
    net = _summary_row(rows, "Net Dönem Kârı (Zararı)", "Net Dönem Karı (Zararı)")
    assets = _summary_row(rows, "Toplam Varlıklar")
    liab = _summary_row(rows, "Toplam Yükümlülükler")
    cash = _summary_row(rows, "Nakit ve Nakit Benzerleri")

    n = max(len(headers), len(rev), len(op), len(net), len(assets), len(liab))
    if n == 0:
        snap={"available":False,"reason":"KAP Özet Finansal Bilgiler tablosu ayrıştırılamadı.","source":"kap_summary","kap_url":url}
        _cache_json_set(key, 60*60, snap); return snap

    # Sağdaki sütun en güncel dönemdir. Özet sayfa geçmiş yıllarda yalnız yıllık sütunlar
    # taşıyabildiği için büyüme yalnız AYNI dönem (örn 2026/06 vs 2025/06) bulunursa hesaplanır.
    cur_i = n-1
    cur_period = headers[cur_i] if cur_i < len(headers) else None
    prev_i = None
    if cur_period and re.fullmatch(r"20\d{2}/\d{2}", cur_period):
        y,m = cur_period.split("/")
        target=f"{int(y)-1}/{m}"
        if target in headers: prev_i=headers.index(target)

    def at(a,i): return a[i] if i is not None and i < len(a) else None
    rev_cur, rev_prev = at(rev,cur_i), at(rev,prev_i)
    op_cur, op_prev = at(op,cur_i), at(op,prev_i)
    net_cur, net_prev = at(net,cur_i), at(net,prev_i)
    assets_cur = at(assets,cur_i); liab_cur=at(liab,cur_i); cash_cur=at(cash,cur_i)
    # Bilanço yönü için bir önceki mevcut sütun kullanılabilir; gelir büyümesi için kullanılmaz.
    bal_prev_i = cur_i-1 if cur_i > 0 else None
    assets_prev=at(assets,bal_prev_i); liab_prev=at(liab,bal_prev_i)

    rev_g=_pct_change(rev_cur,rev_prev); op_g=_pct_change(op_cur,op_prev); net_g=_pct_change(net_cur,net_prev)
    margin_cur=(_safe_ratio(op_cur,rev_cur)*100) if _safe_ratio(op_cur,rev_cur) is not None else None
    margin_prev=(_safe_ratio(op_prev,rev_prev)*100) if _safe_ratio(op_prev,rev_prev) is not None else None
    margin_delta=(margin_cur-margin_prev) if margin_cur is not None and margin_prev is not None else None

    # Karşılaştırılabilir dönem varsa klasik büyüme skoru; yoksa sadece cari kaliteyi ölç ve
    # büyüme puanı uydurma. Böylece 2026/06'yı 2025/12 ile kıyaslamayız.
    s_rev=_score_growth(rev_g) if prev_i is not None else (2 if rev_cur is not None and rev_cur>0 else 0)
    s_op=_score_profit(op_cur,op_prev,7) if prev_i is not None else (4 if op_cur is not None and op_cur>0 else 0)
    s_net=_score_profit(net_cur,net_prev,6) if prev_i is not None else (3 if net_cur is not None and net_cur>0 else 0)
    if margin_delta is not None:
        s_margin=4 if margin_delta>=3 else 3 if margin_delta>=1 else 2 if margin_delta>0 else 1 if margin_cur and margin_cur>0 else 0
    else: s_margin=2 if margin_cur is not None and margin_cur>0 else 0
    bilanco_score=min(25,s_rev+s_op+s_net+s_margin)

    lev_cur=_safe_ratio(liab_cur,assets_cur); lev_prev=_safe_ratio(liab_prev,assets_prev)
    if lev_cur is not None and lev_prev is not None:
        improvement=(lev_prev-lev_cur)*100
        s_lev=3 if improvement>=3 else 2 if improvement>0 else 1 if lev_cur<.5 else 0
    else: s_lev=0
    # Özet sayfada OCF yoksa nakit skorunu sahte veriyle doldurma; yalnız bilanço/nakit mevcudiyeti
    # ve kaldıraç yönünden sınırlı puan ver.
    s_cash_level=3 if cash_cur is not None and cash_cur>0 else 0
    cash_score=min(15,s_cash_level+s_lev)

    def r2(x): return None if x is None else round(x,2)
    snap={
      "available": any(v is not None for v in (rev_cur,op_cur,net_cur,assets_cur)),
      "source":"kap_summary","comparison_period": headers[prev_i] if prev_i is not None else None,
      "period":cur_period,"scores":{"bilanco":bilanco_score,"cash":cash_score},
      "metrics":{
        "revenue":{"current":rev_cur,"previous":rev_prev,"growth_pct":r2(rev_g)},
        "operating_profit":{"current":op_cur,"previous":op_prev,"growth_pct":r2(op_g)},
        "net_profit":{"current":net_cur,"previous":net_prev,"growth_pct":r2(net_g)},
        "operating_margin_pct":{"current":r2(margin_cur),"previous":r2(margin_prev),"delta_pp":r2(margin_delta)},
        "operating_cash_flow":{"current":None,"previous":None,"growth_pct":None},
        "cash":{"current":cash_cur,"previous":None},
        "liabilities_to_assets":{"current":r2(lev_cur),"previous":r2(lev_prev)},
        "cash_conversion":None,
      },
      "score_breakdown":{"revenue":s_rev,"operating_profit":s_op,"net_profit":s_net,"margin":s_margin,"cash_level":s_cash_level,"leverage":s_lev},
      "kap_url":url,
      "note":"Özet finansal sayfa kullanıldı. Aynı dönem geçen yıl sütunu yoksa büyüme puanı sınırlı tutulur; OCF uydurulmaz."
    }
    _cache_json_set(key,_FIN_TTL,snap); return snap


def _financial_snapshot(symbol: str, disclosures: list, oid: str | None = None, company: dict | None = None):
    """v0.3.4: KAP özet sayfa + gerçek Bildirim HTML finansal tablo doğrulaması."""
    if company:
        try:
            snap=_kap_summary_financial_snapshot(symbol,company)
            if snap.get("available"):
                return snap
        except Exception as exc:
            summary_error=str(exc)
        else:
            summary_error=snap.get("reason") or "özet finansal veri yok"
    else:
        summary_error="company bilgisi yok"

    # Eski FR parser fallback: servis yapısı tekrar uyumlu hale gelirse çalışmaya devam eder.
    financial_disclosures = _kap_financial_disclosures(symbol, oid or "", 365) if oid else disclosures
    fr = _latest_financial_disclosure(financial_disclosures)
    if not fr:
        return {"available": False, "reason": f"Özet finansal başarısız: {summary_error}; Finansal Rapor bildirimi ayrıştırılamadı."}
    idx = fr.get("disclosureIndex")
    key = f"kap:fin:v034:{symbol}:{idx}"
    cached = _cache_json_get(key)
    if cached is not None: return cached
    # v0.3.4: finansal tabloyu öncelikle gerçek KAP Bildirim HTML sayfasından oku.
    rows = _kap_public_financial_rows(idx)
    if not rows:
        # Eski attachment-detail yapısı uyumluysa son çare olarak onu da dene.
        item=_kap_detail_json(idx); body=item.get("disclosureBody") or []
        if isinstance(body,str): body=[body]
        parser=_KapTableParser()
        for part in body:
            try: parser.feed(str(part or ""))
            except Exception: pass
        rows=parser.rows
    rev4=_find_fin_row(rows,taxonomy="ifrs-full_Revenue",label="Hasılat",prefer_values=4)
    op4=_find_fin_row(rows,label="Esas Faaliyet Kârı (Zararı)",prefer_values=4) or _find_fin_row(rows,label="Esas Faaliyet Karı (Zararı)",prefer_values=4)
    net4=_find_fin_row(rows,taxonomy="ifrs-full_ProfitLoss",prefer_values=4)
    ocf2=_find_fin_row(rows,taxonomy="ifrs-full_CashFlowsFromUsedInOperatingActivities",prefer_values=2)
    assets2=_find_fin_row(rows,taxonomy="ifrs-full_Assets",label="TOPLAM VARLIKLAR",prefer_values=2)
    liab2=_find_fin_row(rows,taxonomy="ifrs-full_Liabilities",label="TOPLAM YÜKÜMLÜLÜKLER",prefer_values=2)
    cash2=_find_fin_row(rows,taxonomy="ifrs-full_CashAndCashEquivalents",prefer_values=2)
    def first2(v): return (v[0],v[1]) if v and len(v)>=2 else (None,None)
    rev_cur,rev_prev=first2(rev4); op_cur,op_prev=first2(op4); net_cur,net_prev=first2(net4); ocf_cur,ocf_prev=first2(ocf2)
    assets_cur,assets_prev=first2(assets2); liab_cur,liab_prev=first2(liab2); cash_cur,cash_prev=first2(cash2)
    rev_g=_pct_change(rev_cur,rev_prev); op_g=_pct_change(op_cur,op_prev); net_g=_pct_change(net_cur,net_prev); ocf_g=_pct_change(ocf_cur,ocf_prev)
    margin_cur=(_safe_ratio(op_cur,rev_cur)*100) if _safe_ratio(op_cur,rev_cur) is not None else None
    margin_prev=(_safe_ratio(op_prev,rev_prev)*100) if _safe_ratio(op_prev,rev_prev) is not None else None
    margin_delta=(margin_cur-margin_prev) if margin_cur is not None and margin_prev is not None else None
    s_rev=_score_growth(rev_g); s_op=_score_profit(op_cur,op_prev,7); s_net=_score_profit(net_cur,net_prev,6)
    s_margin=4 if margin_delta is not None and margin_delta>=3 else 3 if margin_delta is not None and margin_delta>=1 else 2 if margin_delta is not None and margin_delta>0 else 1 if margin_cur and margin_cur>0 else 0
    bilanço_score=min(25,s_rev+s_op+s_net+s_margin)
    s_ocf_level=5 if ocf_cur is not None and ocf_cur>0 else 0
    s_ocf_growth=4 if ocf_cur is not None and ocf_prev is not None and ocf_cur>0 and ocf_prev<=0 else 4 if ocf_g is not None and ocf_g>=25 else 3 if ocf_g is not None and ocf_g>=5 else 2 if ocf_g is not None and ocf_g>0 else 0
    conv=_safe_ratio(ocf_cur,net_cur) if net_cur is not None and net_cur>0 else None
    s_conv=3 if conv is not None and conv>=1 else 2 if conv is not None and conv>=.5 else 1 if conv is not None and conv>0 else 0
    lev_cur=_safe_ratio(liab_cur,assets_cur); lev_prev=_safe_ratio(liab_prev,assets_prev)
    s_lev=0
    if lev_cur is not None and lev_prev is not None:
        improvement=(lev_prev-lev_cur)*100; s_lev=3 if improvement>=3 else 2 if improvement>0 else 1 if lev_cur<.5 else 0
    cash_score=min(15,s_ocf_level+s_ocf_growth+s_conv+s_lev)
    def r2(x): return None if x is None else round(x,2)
    snap={"available":any(v is not None for v in (rev_cur,op_cur,net_cur,ocf_cur)),"source":"kap_fr_fallback","disclosure_index":idx,"publish_date":fr.get("publishDate"),"year":fr.get("year"),"period":fr.get("period"),"scores":{"bilanco":bilanço_score,"cash":cash_score},"metrics":{"revenue":{"current":rev_cur,"previous":rev_prev,"growth_pct":r2(rev_g)},"operating_profit":{"current":op_cur,"previous":op_prev,"growth_pct":r2(op_g)},"net_profit":{"current":net_cur,"previous":net_prev,"growth_pct":r2(net_g)},"operating_margin_pct":{"current":r2(margin_cur),"previous":r2(margin_prev),"delta_pp":r2(margin_delta)},"operating_cash_flow":{"current":ocf_cur,"previous":ocf_prev,"growth_pct":r2(ocf_g)},"cash":{"current":cash_cur,"previous":cash_prev},"liabilities_to_assets":{"current":r2(lev_cur),"previous":r2(lev_prev)},"cash_conversion":r2(conv)},"score_breakdown":{"revenue":s_rev,"operating_profit":s_op,"net_profit":s_net,"margin":s_margin,"ocf_level":s_ocf_level,"ocf_growth":s_ocf_growth,"cash_conversion":s_conv,"leverage":s_lev},"kap_url":KAP_BASE+f"/tr/Bildirim/{idx}","summary_error":summary_error}
    _cache_json_set(key,_FIN_TTL,snap); return snap

def _build_story_payload(symbol: str):
    company = _kap_find_company(symbol)
    if not company:
        raise LookupError(f"{symbol} KAP işlem gören şirket listesinde bulunamadı")
    oid = str(company.get("mkkMemberOid") or "")
    if not oid:
        raise RuntimeError(f"{symbol} için KAP şirket OID bilgisi bulunamadı")
    disclosures = _kap_disclosures(symbol, oid, _STORY_LOOKBACK_DAYS)
    financial = _financial_snapshot(symbol, disclosures, oid, company)
    now = datetime.now(ZoneInfo("Europe/Istanbul"))

    candidates = []
    for d in disclosures:
        base_text = " | ".join(str(d.get(k) or "") for k in ("subject", "summary"))
        stype, base_score, hits, neg = _classify_story(base_text)
        if base_score <= 0:
            continue
        dt = _parse_publish_date(d.get("publishDate"))
        age = (now - dt).days if dt else 999
        recency_bonus = 3 if age <= 30 else 2 if age <= 60 else 1 if age <= 120 else 0
        score = min(20, base_score + recency_bonus)
        candidates.append({
            "d": d, "type": stype, "score": score, "hits": hits, "negative": neg,
            "date": dt, "age": age, "text": base_text,
        })

    candidates.sort(key=lambda x: (x["score"], -(x["age"] if x["age"] != 999 else 999)), reverse=True)
    # En güçlü birkaç bildirimin detay metnini kontrollü şekilde aç ve sınıflandırmayı zenginleştir.
    for c in candidates[:3]:
        try:
            detail = _kap_detail_text(c["d"].get("disclosureIndex"))
        except Exception:
            detail = ""
        if detail:
            stype, sc, hits, neg = _classify_story(c["text"] + " | " + detail)
            c["type"], c["hits"], c["negative"] = stype, hits, neg
            c["score"] = min(20, max(c["score"], sc + (3 if c["age"] <= 30 else 2 if c["age"] <= 60 else 1 if c["age"] <= 120 else 0)))
            c["detail"] = detail
            material = _extract_materiality(detail)
            if material:
                # Ölçülebilir tutar/oran açıklanmışsa katalizör kanıtı biraz güçlenir.
                c["score"] = min(20, c["score"] + 1)
                c["material"] = material

    best = candidates[0] if candidates else None
    if not best:
        return {
            "symbol": symbol,
            "scores": {"kap": 0, "bilanco": (financial.get("scores") or {}).get("bilanco", 0), "cash": (financial.get("scores") or {}).get("cash", 0)},
            "story_type": "Diğer",
            "source": "KAP",
            "source_date": now.date().isoformat(),
            "summary": f"Son {_STORY_LOOKBACK_DAYS} günde otomatik sözlükte güçlü katalizör eşleşmesi bulunamadı.",
            "impact": "Ölçülebilir hikâye etkisi bulunamadı; bu sonuç 'hikâye yok' anlamına gelmez.",
            "explanation": f"{symbol}: {len(disclosures)} KAP bildirimi incelendi; güçlü katalizör eşleşmesi yok. Finansal doğrulama: Bilanço {(financial.get('scores') or {}).get('bilanco', 0)}/25 · Kârlılık/Nakit {(financial.get('scores') or {}).get('cash', 0)}/15.",
            "financials": financial,
            "meta": {"disclosure_count": len(disclosures), "candidate_count": 0, "lookback_days": _STORY_LOOKBACK_DAYS, "company": company.get("kapMemberTitle")},
        }

    d = best["d"]
    title = str(d.get("subject") or d.get("summary") or "KAP Bildirimi")
    summary = str(d.get("summary") or title)
    detail = best.get("detail") or ""
    material = best.get("material") or _extract_materiality(detail)
    date_iso = best["date"].date().isoformat() if best["date"] else now.date().isoformat()
    evidence = ", ".join(best["hits"][:5]) if best["hits"] else "anahtar eşleşme"
    neg_note = f" Negatif/iptal riski kelimeleri: {', '.join(best['negative'][:3])}." if best["negative"] else ""
    impact = material if material else "KAP açıklamasında otomatik yakalanan tutar/oran yok; şirket büyüklüğüne göre maddilik oranı henüz hesaplanmıyor."
    explanation = (
        f"{symbol}: son {_STORY_LOOKBACK_DAYS} günde {len(disclosures)} bildirim incelendi; "
        f"{len(candidates)} katalizör adayı bulundu. En güçlü aday: {title} · KAP skoru {best['score']}/20 · "
        f"eşleşmeler: {evidence}.{neg_note} Finansal doğrulama: Bilanço {(financial.get('scores') or {}).get('bilanco', 0)}/25 · Kârlılık/Nakit {(financial.get('scores') or {}).get('cash', 0)}/15."
    )
    return {
        "symbol": symbol,
        "scores": {"kap": best["score"], "bilanco": (financial.get("scores") or {}).get("bilanco", 0), "cash": (financial.get("scores") or {}).get("cash", 0)},
        "story_type": best["type"],
        "source": "KAP",
        "source_date": date_iso,
        "summary": summary[:1200],
        "impact": impact[:1000],
        "explanation": explanation[:1800],
        "kap_url": KAP_BASE + f"/tr/Bildirim/{d.get('disclosureIndex')}",
        "financials": financial,
        "meta": {
            "company": company.get("kapMemberTitle"),
            "disclosure_count": len(disclosures),
            "candidate_count": len(candidates),
            "lookback_days": _STORY_LOOKBACK_DAYS,
            "disclosure_index": d.get("disclosureIndex"),
            "publish_date": d.get("publishDate"),
            "subject": d.get("subject"),
            "keyword_hits": best["hits"][:10],
        },
    }


@app.get("/api/story")
def story_api():
    metric_key = "story"
    symbol = _story_symbol(request.args.get("symbol", ""))
    if not symbol:
        return jsonify({"error": "geçerli symbol gerekli"}), 400

    cache_key = f"story:v034:{symbol}:{_STORY_LOOKBACK_DAYS}"
    cached = _cache_get(cache_key)
    if cached:
        _metric_add("cache_hit", metric_key)
        body, ctype = cached
        return _respond_bytes(
            body, content_type=ctype, cache_control="public, max-age=900, stale-while-revalidate=3600",
            metric_key=metric_key, extra_headers={"X-BIST-Cache": "HIT"},
        )

    _metric_add("cache_miss", metric_key)
    try:
        payload = _build_story_payload(symbol)
        body = _minify_json_bytes(payload)
        _cache_set(cache_key, _STORY_TTL, body, "application/json")
        return _respond_bytes(
            body, content_type="application/json", cache_control="public, max-age=900, stale-while-revalidate=3600",
            metric_key=metric_key, extra_headers={"X-BIST-Cache": "MISS"},
        )
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except requests.RequestException as exc:
        return jsonify({"error": f"KAP bağlantı hatası: {exc}"}), 502
    except Exception as exc:
        return jsonify({"error": f"Hikâye motoru hatası: {exc}"}), 500


@app.get("/api/metrics")
def metrics():
    with _metrics_lock:
        reqs = dict(_metrics["requests"])
        raw = dict(_metrics["bytes_out_raw"])
        wire = dict(_metrics["bytes_out_wire"])
        hits = dict(_metrics["cache_hit"])
        misses = dict(_metrics["cache_miss"])
        started_at = _metrics["started_at"]

    total_raw = sum(raw.values())
    total_wire = sum(wire.values())

    with _cache_lock:
        cache_items = len(_cache)

    return jsonify({
        "uptime_seconds": round(time.time() - started_at, 1),
        "market_open_approx": _istanbul_market_open(),
        "cache_items": cache_items,
        "requests": reqs,
        "cache_hit": hits,
        "cache_miss": misses,
        "bytes_out_raw": raw,
        "bytes_out_wire": wire,
        "total_raw_mb": round(total_raw / 1024 / 1024, 3),
        "total_wire_mb": round(total_wire / 1024 / 1024, 3),
        "compression_saved_pct": round(
            (1 - total_wire / total_raw) * 100, 1
        ) if total_raw else 0.0,
    })


if __name__ == "__main__":
    print()
    print("BIST Scanner HTML - bandwidth optimized proxy")
    print("Yahoo: compact JSON + gzip + cache")
    print("TradingView: gzip + short cache")
    print("Story Radar v0.3.3: /api/story?symbol=NETAS")
    print("Metrics: /api/metrics")
    print()

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8765)),
        debug=False,
        threaded=True,
    )
    
