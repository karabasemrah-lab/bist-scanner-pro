# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import Flask, Response, jsonify, request, send_from_directory
import requests

app = Flask(__name__, static_folder=".", static_url_path="")

TV_URL = "https://scanner.tradingview.com/turkey/scan"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
})

@app.get("/")
def home():
    return send_from_directory(".", "index.html")

@app.post("/api/tradingview")
def tradingview_proxy():
    try:
        r = SESSION.post(
            TV_URL,
            json=request.get_json(force=True),
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        return Response(
            r.content,
            status=r.status_code,
            content_type=r.headers.get("Content-Type", "application/json"),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

@app.get("/api/yahoo")
def yahoo_proxy():
    symbol = request.args.get("symbol", "").strip()
    range_ = request.args.get("range", "2y").strip()
    interval = request.args.get("interval", "1d").strip()

    if not symbol:
        return jsonify({"error": "symbol gerekli"}), 400

    try:
        r = SESSION.get(
            YAHOO_URL.format(symbol=symbol),
            params={
                "range": range_,
                "interval": interval,
                "includePrePost": "false",
                "events": "div,splits",
            },
            timeout=30,
        )
        return Response(
            r.content,
            status=r.status_code,
            content_type=r.headers.get("Content-Type", "application/json"),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

if __name__ == "__main__":
    print()
    print("BIST Scanner HTML v0.2")
    print("Tarayici adresi: http://127.0.0.1:8765")
    print("Kapatmak icin CTRL+C")
    print()
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)
