from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
required = [
    ROOT / ".github/workflows/pages.yml",
    ROOT / "scripts/prepare_universe.py",
    ROOT / "scripts/generate_shard.py",
    ROOT / "scripts/merge_snapshot.py",
    ROOT / "cloud-adapter.js",
    ROOT / "index.html",
]
missing = [str(p.relative_to(ROOT)) for p in required if not p.exists()]
if missing:
    raise SystemExit("Eksik dosyalar: " + ", ".join(missing))
for name in ["last_scan.json", "dashboard.json", "market_cards.json", "kap_notifications.json", "last_backtest.json", "build_info.json"]:
    json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
print("Cloud paket yapısı ve JSON dosyaları geçerli.")
