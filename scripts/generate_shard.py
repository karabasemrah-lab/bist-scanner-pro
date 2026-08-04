from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def balanced_slice(items: list[str], index: int, total: int) -> list[str]:
    size = math.ceil(len(items) / total)
    return items[index * size : min(len(items), (index + 1) * size)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--total", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    symbols, note = server.symbols_for_universe("all")
    if len(symbols) < 300:
        raise RuntimeError(note or f"BIST Tüm listesi yetersiz: {len(symbols)}")
    selected = balanced_slice(symbols, args.index, args.total)
    if not selected:
        raise RuntimeError(f"Boş tarama parçası: {args.index}/{args.total}")

    tickers = [server.yahoo_ticker(s) for s in selected]
    batches = [tickers[i : i + 30] for i in range(0, len(tickers), 30)]
    config = {
        "donchianLength": 20,
        "volumeSpikeValue": 1.5,
        "atrRatio": 1.0,
        "squeezeFactor": 0.70,
    }

    benchmark_close = None
    try:
        import yfinance as yf

        bench = yf.download(
            "XU100.IS",
            period="2y",
            interval="1d",
            auto_adjust=False,
            actions=False,
            progress=False,
            threads=False,
            timeout=35,
        )
        if bench is not None and not bench.empty:
            benchmark_close = server._series(bench, "Close")
    except Exception as exc:
        print(f"UYARI: XU100 benchmark alınamadı: {exc}")

    raw_by_ticker: dict = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(3, len(batches))) as pool:
        futures = {
            pool.submit(server._download_batch, batch, "2y", "1d"): batch
            for batch in batches
        }
        for future in as_completed(futures):
            try:
                raw_by_ticker.update(future.result())
            except Exception as exc:
                errors.append(str(exc))

    rows: list[dict] = []
    failed: list[str] = []
    for symbol, ticker in zip(selected, tickers):
        try:
            row = server._analyze(
                symbol, ticker, raw_by_ticker.get(ticker), config, benchmark_close
            )
            if row:
                rows.append(row)
            else:
                failed.append(symbol)
        except Exception as exc:
            failed.append(symbol)
            if len(errors) < 20:
                errors.append(f"{symbol}: {exc}")

    payload = {
        "schema": 1,
        "shardIndex": args.index,
        "shardTotal": args.total,
        "requested": len(selected),
        "rows": rows,
        "failedSymbols": failed,
        "errors": errors[:20],
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(server._json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        f"Parça {args.index + 1}/{args.total}: {len(rows)}/{len(selected)} geçerli, "
        f"{len(failed)} başarısız"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
