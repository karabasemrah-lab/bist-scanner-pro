from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import update_symbols  # noqa: E402


def main() -> int:
    # KAP'tan doğrulanmış BIST Tüm listesini üretir. Mevcut liste varsa ve
    # güncelleme geçici olarak başarısız olursa son sağlam liste korunur.
    master = ROOT / "bist_all_master.json"
    try:
        code = update_symbols.main()
        if code != 0:
            raise RuntimeError(f"Sembol güncelleme kodu: {code}")
    except Exception as exc:
        if not master.exists():
            raise
        print(f"UYARI: KAP güncellemesi başarısız, mevcut liste kullanılacak: {exc}")

    payload = json.loads(master.read_text(encoding="utf-8"))
    symbols = payload.get("symbols", [])
    if len(symbols) < 300:
        raise RuntimeError(f"BIST Tüm listesi yetersiz: {len(symbols)} sembol")

    shard_count = int(os.environ.get("BIST_SHARD_COUNT", "4"))
    manifest = {
        "schema": 1,
        "universe": "all",
        "count": len(symbols),
        "shardCount": shard_count,
        "updatedAt": payload.get("updatedAt"),
        "source": payload.get("source"),
    }
    (ROOT / "shard_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"BIST Tüm hazır: {len(symbols)} sembol, {shard_count} parça")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
