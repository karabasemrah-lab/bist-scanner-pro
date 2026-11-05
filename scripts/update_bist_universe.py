#!/usr/bin/env python3
"""KAP fiili-dolaşım tablosundan BIST sembol listesini güvenli şekilde günceller.

Yalnızca doğrulanabilir bir tam liste geldiğinde dosya değiştirilir.
Hata veya anormal kapsam değişiminde mevcut liste korunur (exit code != 0).
"""
import json
import os
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SOURCE = "https://kap.org.tr/tr/tumKalemler/kpy41_acc5_fiili_dolasimdaki_pay"
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "bist_all_master.json"
SYMBOL_RE = re.compile(r"[A-Z][A-Z0-9]{1,11}\Z")


class TableRows(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.tr = None
        self.cell = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.tr = []
        elif tag in ('td', 'th') and self.tr is not None and self.cell is None:
            self.cell = []
            self.depth = 1
        elif self.cell is not None:
            self.depth += 1

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.tr.append(' '.join(''.join(self.cell).split()))
            self.cell = None
            self.depth = 0
        elif tag == 'tr' and self.tr is not None:
            if self.tr:
                self.rows.append(self.tr)
            self.tr = None


def parse_symbols(page):
    parser = TableRows()
    parser.feed(page)
    values = set()
    for row in parser.rows:
        # KAP fiili dolaşım tablosu: Şirket | Borsa Kodu | 7 sayısal sütun
        if len(row) < 8:
            continue
        raw = row[1].strip().upper()
        # Borsa kodu hücresinde bazı hisseler tek tek veya virgülle bulunabilir.
        for symbol in re.split(r'[,;/\s]+', raw):
            if SYMBOL_RE.fullmatch(symbol) and len(symbol) <= 8:
                values.add(symbol)
    return sorted(values)


def update(page, target=TARGET):
    symbols = parse_symbols(page)
    if len(symbols) < 450 or len(symbols) > 1000:
        raise RuntimeError(f'Kaynak tam liste gibi görünmüyor: {len(symbols)} sembol; mevcut dosya korunuyor.')
    for symbol in ('THYAO', 'ASELS', 'GARAN', 'TUPRS', 'EREGL'):
        if symbol not in symbols:
            raise RuntimeError(f'Temel sembol {symbol} eksik; mevcut dosya korunuyor.')
    old = json.loads(target.read_text(encoding='utf-8'))
    previous = set(old.get('symbols', []))
    if len(previous) < 400:
        raise RuntimeError('Mevcut liste beklenmedik; otomatik güncelleme durduruldu.')
    overlap = len(previous.intersection(symbols)) / len(previous)
    if overlap < 0.90:
        raise RuntimeError(f'Kaynağın mevcut listeyle örtüşmesi düşük: %{overlap*100:.1f}; dosya korunuyor.')
    if set(symbols) == previous:
        print(f'Değişiklik yok: {len(symbols)} hisse. Tarih değiştirilmedi.')
        return False
    updated = {
        'schema': old.get('schema', 1),
        'source': SOURCE,
        'updatedAt': datetime.now(ZoneInfo('Europe/Istanbul')).strftime('%Y-%m-%d %H:%M:%S'),
        'count': len(symbols),
        'symbols': symbols,
    }
    target.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Güncellendi: {len(previous)} → {len(symbols)} sembol, yeni {len(set(symbols)-previous)}, silinen {len(previous-set(symbols))}')
    return True


def main():
    req = Request(SOURCE, headers={'User-Agent': 'Mozilla/5.0 BIST-Universe-Updater/1.0', 'Accept': 'text/html'})
    with urlopen(req, timeout=40) as response:
        page = response.read(5_000_000).decode('utf-8', errors='replace')
    update(page)


if __name__ == '__main__':
    main()
