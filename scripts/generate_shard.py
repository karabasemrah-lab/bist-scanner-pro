from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

VERIFIED_SYMBOLS_PATH = ROOT / "bist_verified.txt"


def balanced_slice(items: list[str], index: int, total: int) -> list[str]:
    size = math.ceil(len(items) / total)
    return items[index * size : min(len(items), (index + 1) * size)]


def load_verified_symbols() -> list[str]:
    """Tarama evrenini yalnızca bist_verified.txt dosyasından okur."""
    if not VERIFIED_SYMBOLS_PATH.exists():
        raise RuntimeError(
            f"Teyitli hisse listesi bulunamadı: {VERIFIED_SYMBOLS_PATH}"
        )

    symbols: list[str] = []
    seen: set[str] = set()

    lines = VERIFIED_SYMBOLS_PATH.read_text(encoding="utf-8-sig").splitlines()
    for line_no, raw_line in enumerate(lines, start=1):
        code = raw_line.strip().upper()

        if not code or code.startswith("#"):
            continue

        if code.endswith(".IS"):
            code = code[:-3]

        if not re.fullmatch(r"[A-Z0-9]{3,6}", code):
            print(
                f"UYARI: bist_verified.txt satır {line_no} geçersiz, atlandı: {raw_line!r}"
            )
            continue

        if code not in seen:
            seen.add(code)
            symbols.append(code)

    if len(symbols) < 300:
        raise RuntimeError(
            f"bist_verified.txt içinde yalnızca {len(symbols)} geçerli sembol var."
        )

    return symbols


def _valid_frames(result: object) -> dict:
    valid: dict = {}

    if not isinstance(result, dict):
        return valid

    for ticker, frame in result.items():
        try:
            if frame is not None and not frame.empty:
                valid[ticker] = frame
        except Exception:
            continue

    return valid


def download_batch_with_retry(
    batch: list[str],
    period: str = "2y",
    interval: str = "1d",
    attempts: int = 3,
) -> tuple[dict, list[str]]:
    downloaded: dict = {}
    remaining = list(dict.fromkeys(batch))
    messages: list[str] = []

    for attempt in range(1, attempts + 1):
        if not remaining:
            break

        try:
            result = server._download_batch(remaining, period, interval)
            downloaded.update(_valid_frames(result))
        except Exception as exc:
            messages.append(
                f"Toplu indirme denemesi {attempt}/{attempts} başarısız: {exc}"
            )

        remaining = [ticker for ticker in remaining if ticker not in downloaded]

        if remaining and attempt < attempts:
            time.sleep(attempt * 2)

    for ticker in list(remaining):
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                result = server._download_batch([ticker], period, interval)
                valid = _valid_frames(result)

                if ticker in valid:
                    downloaded[ticker] = valid[ticker]
                    break
            except Exception as exc:
                last_error = exc

            if attempt < attempts:
                time.sleep(attempt * 2)

        if ticker not in downloaded:
            if last_error is not None:
                messages.append(
                    f"{ticker}: {attempts} denemede alınamadı: {last_error}"
                )
            else:
                messages.append(
                    f"{ticker}: {attempts} denemede geçerli fiyat verisi bulunamadı."
                )

    return downloaded, messages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--total", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.total <= 0:
        raise ValueError("--total sıfırdan büyük olmalıdır.")

    if args.index < 0 or args.index >= args.total:
        raise ValueError(
            f"Geçersiz parça indeksi: {args.index}. "
            f"Beklenen aralık: 0-{args.total - 1}"
        )

    symbols = load_verified_symbols()
    selected = balanced_slice(symbols, args.index, args.total)

    if not selected:
        raise RuntimeError(f"Boş tarama parçası: {args.index}/{args.total}")

    print(
        f"Teyitli liste yüklendi: {len(symbols)} sembol. "
        f"Bu parçada {len(selected)} sembol taranacak."
    )

    tickers = [server.yahoo_ticker(symbol) for symbol in selected]
    batches = [tickers[i : i + 30] for i in range(0, len(tickers), 30)]

    config = {
        "donchianLength": 20,
        "volumeSpike": 1.5,
        "volumeSpikeValue": 1.5,
        "atrRatio": 1.0,
        "squeezeFactor": 0.70,
    }

    errors: list[str] = []

    benchmark_close = None
    try:
        benchmark_data, benchmark_errors = download_batch_with_retry(
            ["XU100.IS"],
            period="2y",
            interval="1d",
            attempts=3,
        )
        errors.extend(benchmark_errors)

        bench = benchmark_data.get("XU100.IS")
        if bench is not None and not bench.empty:
            benchmark_close = server._series(bench, "Close")
        else:
            errors.append("XU100 benchmark verisi alınamadı.")
    except Exception as exc:
        errors.append(f"XU100 benchmark alınamadı: {exc}")

    raw_by_ticker: dict = {}
    max_workers = max(1, min(2, len(batches)))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                download_batch_with_retry,
                batch,
                "2y",
                "1d",
                3,
            ): batch
            for batch in batches
        }

        for future in as_completed(futures):
            batch = futures[future]

            try:
                downloaded, retry_errors = future.result()
                raw_by_ticker.update(downloaded)
                errors.extend(retry_errors)

                missing = [
                    ticker for ticker in batch if ticker not in downloaded
                ]

                if missing:
                    errors.append(
                        f"{len(missing)} sembol için Yahoo verisi alınamadı: "
                        + ", ".join(missing[:12])
                    )

            except Exception as exc:
                errors.append(f"Veri partisi tamamen başarısız: {exc}")

    rows: list[dict] = []
    failed: list[str] = []
    insufficient_history: list[str] = []

    for symbol, ticker in zip(selected, tickers):
        print(f"İşleniyor: {symbol}", flush=True)
        frame = raw_by_ticker.get(ticker)

        if frame is None:
            failed.append(symbol)
            continue

        try:
            if frame.empty:
                failed.append(symbol)
                continue
        except Exception:
            failed.append(symbol)
            continue

        try:
            row = server._analyze(
                symbol,
                ticker,
                frame,
                config,
                benchmark_close,
            )

            if row:
                rows.append(row)
            else:
                insufficient_history.append(symbol)

            if len(errors) < 100:
                errors.append(
                f"{symbol}: yetersiz fiyat geçmişi / yeni hisse."
        )

        except Exception as exc:
            failed.append(symbol)
            if len(errors) < 100:
                errors.append(f"{symbol}: hesaplama hatası: {exc}")

    payload = {
        "schema": 2,
        "source": "bist_verified.txt",
        "verifiedUniverseCount": len(symbols),
        "shardIndex": args.index,
        "shardTotal": args.total,
        "requested": len(selected),
        "downloaded": len(raw_by_ticker),
        "valid": len(rows),
        "failed": len(failed),
        "insufficientHistoryCount": len(insufficient_history),
        "rows": rows,
        "failedSymbols": failed,
        "insufficientHistorySymbols": insufficient_history,
        "errors": errors[:100],
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(
            server._json_safe(payload),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    print(
    f"Parça {args.index + 1}/{args.total}: "
    f"{len(rows)}/{len(selected)} geçerli, "
    f"{len(insufficient_history)} yetersiz geçmiş, "
    f"{len(failed)} gerçek hata, "
    f"{len(raw_by_ticker)} geçerli Yahoo verisi indirildi"
)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())