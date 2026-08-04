from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)


def write(name: str, payload) -> None:
    (DATA / name).write_text(
        json.dumps(server._json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()

    shard_dir = Path(args.input)
    files = sorted(shard_dir.glob("shard-*.json"))
    if not files:
        raise RuntimeError(f"Tarama parçası bulunamadı: {shard_dir}")

    manifest = json.loads((ROOT / "shard_manifest.json").read_text(encoding="utf-8"))
    expected = int(manifest.get("shardCount", 0))
    if expected and len(files) != expected:
        raise RuntimeError(f"Eksik parça: beklenen {expected}, bulunan {len(files)}")

    rows: list[dict] = []
    failed: list[str] = []
    errors: list[str] = []
    requested = 0
    for file in files:
        part = json.loads(file.read_text(encoding="utf-8"))
        requested += int(part.get("requested", 0))
        rows.extend(part.get("rows", []))
        failed.extend(part.get("failedSymbols", []))
        errors.extend(part.get("errors", []))

    # Aynı sembol iki kez geldiyse en son kaydı tut.
    unique = {str(row.get("symbol")): row for row in rows if row.get("symbol")}
    rows = list(unique.values())
    enrichment = server.enrich_relative_sector_composite(rows)
    rows.sort(
        key=lambda item: (item.get("compositeScore", 0), item.get("rsScore", 0)),
        reverse=True,
    )

    warning_parts = []
    if failed:
        warning_parts.append(
            f"{len(failed)} sembol için geçerli Yahoo verisi alınamadı: "
            + ", ".join(failed[:12])
            + ("…" if len(failed) > 12 else "")
        )
    if errors:
        warning_parts.append(f"{len(errors)} veri/hesaplama uyarısı oluştu.")

    payload = {
        "schema": 2,
        "mode": "github-actions-sharded",
        "rows": rows,
        "sectorRanking": enrichment.get("sectorRanking", []),
        "radar": enrichment.get("radar", []),
        "failedSymbols": sorted(set(failed)),
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "universe": "all",
        "requested": requested or int(manifest.get("count", 0)),
        "completed": len(rows),
        "shardCount": len(files),
        "warning": " ".join(warning_parts) or None,
    }
    write("last_scan.json", payload)
    write("dashboard.json", server.load_scan_dashboard())

    try:
        write("market_cards.json", server.load_market_cards(force=True))
    except Exception as exc:
        write(
            "market_cards.json",
            {
                "cards": [],
                "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                "warning": str(exc),
            },
        )
    try:
        write("kap_notifications.json", server.fetch_kap_notifications(force=True, limit=100))
    except Exception as exc:
        write(
            "kap_notifications.json",
            {
                "rows": [],
                "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                "warning": str(exc),
            },
        )
    write("last_backtest.json", server.load_last_backtest())
    write(
        "build_info.json",
        {
            "universe": "BIST Tüm",
            "requested": payload["requested"],
            "completed": payload["completed"],
            "failed": len(payload["failedSymbols"]),
            "shardCount": len(files),
            "updatedAt": payload["updatedAt"],
        },
    )
    print(
        f"BIST Tüm snapshot hazır: {len(rows)}/{payload['requested']} hisse, "
        f"{len(files)} parça"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
