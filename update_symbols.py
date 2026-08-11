from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "bist_all_master.json"
SYMBOLS_TR = ROOT / "symbols_tr.json"
KAP_URL = "https://kap.org.tr/tr/tumKalemler/kpy41_acc5_fiili_dolasimdaki_pay"


def normalize_code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper().strip())


def main() -> int:
    try:
        import requests
        from bs4 import BeautifulSoup
    except Exception:
        print("Gerekli paketler eksik. Önce CANLI_VERI_KUR.bat dosyasını çalıştırın.")
        return 2

    print("KAP resmi fiili dolaşımdaki paylar tablosu indiriliyor...")
    response = requests.get(
        KAP_URL,
        timeout=60,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
        },
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    records: dict[str, str] = {}
    for row in soup.select("table tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue
        # Başlık satırlarını ve KAP tablo etiketlerini sembol gibi okumayı engelle.
        if any(cell.name == "th" for cell in cells[:2]):
            continue
        company = " ".join(cells[0].get_text(" ", strip=True).split())
        code_text = " ".join(cells[1].get_text(" ", strip=True).split())
        if company.casefold() in {"şirket", "sirket"} or code_text.casefold() in {"borsa kodu", "kod", "kodu"}:
            continue
        # Bazı şirketlerde birden fazla pay sınıfı olabilir.
        for raw_code in re.findall(r"(?<![A-Z0-9])([A-Z0-9]{4,5})(?![A-Z0-9])", code_text.upper()):
            code = normalize_code(raw_code)
            if re.fullmatch(r"[A-Z0-9]{4,5}", code):
                records.setdefault(code, company or code)

    if len(records) < 300:
        raise RuntimeError(f"KAP tablosundan yalnızca {len(records)} geçerli kod çıkarıldı; dosya yazılmadı.")

    aliases = {"KOZAL": "TRALT", "IPEKE": "TRENJ", "KOZAA": "TRMET"}
    for old, new in aliases.items():
        records.pop(old, None)
        records.setdefault(new, new)

    sorted_records = dict(sorted(records.items()))
    payload = {
        "schema": 1,
        "source": KAP_URL,
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(sorted_records),
        "symbols": list(sorted_records),
        "companies": sorted_records,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Merkezi isim sözlüğünü de genişlet; elle tanımlanan sektör/alias alanlarını koru.
    try:
        master = json.loads(SYMBOLS_TR.read_text(encoding="utf-8"))
    except Exception:
        master = {"version": payload["updatedAt"], "source": "KAP", "symbols": {}}
    symbol_map = master.setdefault("symbols", {})
    for code, name in sorted_records.items():
        item = symbol_map.setdefault(code, {})
        item.setdefault("name", name)
        item.setdefault("yahoo", f"{code}.IS")
    master["version"] = payload["updatedAt"]
    master["source"] = "KAP fiili dolaşımdaki paylar"
    SYMBOLS_TR.write_text(json.dumps(master, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Tamamlandı: {len(sorted_records)} sembol yerel ana listeye kaydedildi.")
    print(f"Dosya: {OUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Güncelleme başarısız: {exc}")
        raise
