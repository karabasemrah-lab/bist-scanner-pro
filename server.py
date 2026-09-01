# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import Flask, Response, jsonify, request, send_from_directory
import requests
import os
import json
import gzip
import time
import threading
from collections import OrderedDict, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__, static_folder=".", static_url_path="")

TV_URL = "https://scanner.tradingview.com/turkey/scan"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

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
    print("Metrics: /api/metrics")
    print()

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8765)),
        debug=False,
        threaded=True,
    )
    
