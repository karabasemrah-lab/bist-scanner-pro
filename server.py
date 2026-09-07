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
# Hikâye Radar v0.2 · KAP katalizör katmanı
# ---------------------------------------------------------------------
# Bu katman KAP'ın herkese açık web uçlarını yalnızca düşük yoğunlukta kullanır.
# Şirket listesi 24 saat, hisse bazlı hikâye sonucu 6 saat cache'lenir.
# Finansal tablo büyüme/marj puanı bu sürümde bilinçli olarak otomatik verilmez;
# yalnız gerçekten ölçülebilir KAP katalizörleri puanlanır.

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

def _build_story_payload(symbol: str):
    company = _kap_find_company(symbol)
    if not company:
        raise LookupError(f"{symbol} KAP işlem gören şirket listesinde bulunamadı")
    oid = str(company.get("mkkMemberOid") or "")
    if not oid:
        raise RuntimeError(f"{symbol} için KAP şirket OID bilgisi bulunamadı")
    disclosures = _kap_disclosures(symbol, oid, _STORY_LOOKBACK_DAYS)
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
            "scores": {"kap": 0},
            "story_type": "Diğer",
            "source": "KAP",
            "source_date": now.date().isoformat(),
            "summary": f"Son {_STORY_LOOKBACK_DAYS} günde otomatik sözlükte güçlü katalizör eşleşmesi bulunamadı.",
            "impact": "Ölçülebilir hikâye etkisi bulunamadı; bu sonuç 'hikâye yok' anlamına gelmez.",
            "explanation": f"{symbol}: {len(disclosures)} KAP bildirimi incelendi; güçlü katalizör eşleşmesi yok. Bilanço/kârlılık puanı bu backend sürümünde otomatik verilmez.",
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
        f"eşleşmeler: {evidence}.{neg_note} Bilanço ve nakit puanları bilinçli olarak değiştirilmedi."
    )
    return {
        "symbol": symbol,
        "scores": {"kap": best["score"]},
        "story_type": best["type"],
        "source": "KAP",
        "source_date": date_iso,
        "summary": summary[:1200],
        "impact": impact[:1000],
        "explanation": explanation[:1800],
        "kap_url": KAP_BASE + f"/tr/Bildirim/{d.get('disclosureIndex')}",
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

    cache_key = f"story:v02:{symbol}:{_STORY_LOOKBACK_DAYS}"
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
    print("Story Radar: /api/story?symbol=NETAS")
    print("Metrics: /api/metrics")
    print()

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8765)),
        debug=False,
        threaded=True,
    )
    
