from __future__ import annotations

import os
import socket

import csv
import io
import json
import math
import re
import threading
import time
import uuid
import webbrowser
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
MASTER_PATH = ROOT / "symbols_tr.json"
MASTER_ALL_PATH = ROOT / "bist_all_master.json"
LAST_SCAN_PATH = DATA / "last_scan.json"
LAST_BACKTEST_PATH = DATA / "last_backtest.json"
AI_LEARNING_PATH = DATA / "ai_learning_history.json"
BACKTEST_JOBS = {}
BACKTEST_LOCK = threading.Lock()

BIST30 = ["AKBNK","ALARK","ASELS","ASTOR","BIMAS","EKGYO","ENKAI","EREGL","FROTO","GARAN","GUBRF","ISCTR","KCHOL","KONTR","TRALT","KRDMD","MGROS","ODAS","OYAKC","PETKM","PGSUS","SAHOL","SASA","SISE","TCELL","THYAO","TOASO","TUPRS","YKBNK","ZOREN"]
BIST50_EXTRA = ["AEFES","AGHOL","AKSEN","ARCLK","BRSAN","CCOLA","CIMSA","DOAS","ECILC","EGEEN","GESAN","HALKB","HEKTS","ISGYO","ISMEN","KCAER","MAVI","OTKAR","SKBNK","TTKOM"]
BIST100_EXTRA = ["ADEL","AKFGY","AKSA","AKSGY","ALBRK","ANSGR","AYDEM","BERA","BJKAS","BRYAT","BUCIM","CANTE","CEMTS","CLEBI","CWENE","DOHOL","EUPWR","GENIL","GLYHO","GWIND","TRENJ","KARSN","KLSER","KMPUR","KORDS","KZBGY","LOGO","MPARK","OBAMS","QUAGR","REEDR","SELEC","SMRTG","SOKM","TABGD","TAVHL","TKFEN","TMSN","TSKB","ULKER","VAKBN","VESTL","YEOTK","YYLGD","ZRGYO","AKCNS","AKYHO","ANHYT","BAGFS","KLGYO"]


def load_symbol_master() -> dict:
    try:
        return json.loads(MASTER_PATH.read_text(encoding="utf-8")).get("symbols", {})
    except Exception:
        return {}

SYMBOL_MASTER = load_symbol_master()
ALIASES = {alias: symbol for symbol, item in SYMBOL_MASTER.items() for alias in item.get("aliases", [])}

def canonical_symbol(symbol: str) -> str:
    return ALIASES.get(symbol, symbol)

def symbol_name(symbol: str) -> str:
    symbol = canonical_symbol(symbol)
    return SYMBOL_MASTER.get(symbol, {}).get("name", symbol)

def symbol_sector(symbol: str) -> str:
    symbol = canonical_symbol(symbol)
    return SYMBOL_MASTER.get(symbol, {}).get("sector") or "Diğer"

def yahoo_ticker(symbol: str) -> str:
    symbol = canonical_symbol(symbol)
    return SYMBOL_MASTER.get(symbol, {}).get("yahoo", f"{symbol}.IS")

def load_bist_all_master() -> tuple[list[str], str | None]:
    try:
        payload = json.loads(MASTER_ALL_PATH.read_text(encoding="utf-8"))
        symbols = [canonical_symbol(str(x).strip().upper()) for x in payload.get("symbols", [])]
        symbols = list(dict.fromkeys(x for x in symbols if re.fullmatch(r"[A-Z0-9]{4,5}", x)))
        if len(symbols) < 300:
            return [], "BIST Tüm ana sembol listesi eksik. Önce SEMBOL_LISTESINI_GUNCELLE.bat dosyasını çalıştırın."
        return symbols, f"Yerel BIST Tüm listesi: {len(symbols)} sembol, güncelleme {payload.get('updatedAt','bilinmiyor')}."
    except FileNotFoundError:
        return [], "BIST Tüm ana sembol listesi bulunamadı. Önce SEMBOL_LISTESINI_GUNCELLE.bat dosyasını çalıştırın."
    except Exception as exc:
        return [], f"BIST Tüm ana sembol listesi okunamadı: {exc}"

def symbols_for_universe(universe: str) -> tuple[list[str], str | None]:
    if universe == "30": return BIST30, None
    if universe == "50": return list(dict.fromkeys(BIST30 + BIST50_EXTRA)), None
    if universe == "100": return list(dict.fromkeys(BIST30 + BIST50_EXTRA + BIST100_EXTRA)), None
    if universe == "all": return load_bist_all_master()
    return BIST30, None

def _finite(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default

def _json_safe(value):
    if isinstance(value, float): return value if math.isfinite(value) else None
    if isinstance(value, dict): return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_json_safe(v) for v in value]
    return value

def _ticker_frame(raw, ticker: str):
    import pandas as pd
    if raw is None or raw.empty: return pd.DataFrame()
    if not isinstance(raw.columns, pd.MultiIndex): return raw.copy()
    if ticker in set(map(str, raw.columns.get_level_values(0))): return raw[ticker].copy()
    if ticker in set(map(str, raw.columns.get_level_values(1))): return raw.xs(ticker, axis=1, level=1).copy()
    return pd.DataFrame()

def _series(df, name: str):
    import pandas as pd
    value = df[name]
    if isinstance(value, pd.DataFrame): value = value.iloc[:, 0]
    return pd.to_numeric(value, errors="coerce")

def _download_batch(batch: list[str], period: str, interval: str):
    import yfinance as yf
    raw = yf.download(tickers=batch, period=period, interval=interval, group_by="ticker", auto_adjust=False, actions=False, progress=False, threads=min(8, len(batch)), timeout=35)
    return {ticker: frame for ticker in batch if not (frame := _ticker_frame(raw, ticker)).empty}

def _ema(series, length: int):
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def _positive_divergence(close, oscillator, lookback: int = 60) -> bool:
    """Basit ve teyitli pozitif uyumsuzluk: iki yerel dip karşılaştırılır."""
    import numpy as np
    c = close.tail(lookback).reset_index(drop=True)
    o = oscillator.tail(lookback).reset_index(drop=True)
    pivots = []
    for i in range(2, len(c) - 2):
        if c.iloc[i] <= c.iloc[i-2:i].min() and c.iloc[i] < c.iloc[i+1:i+3].min():
            if math.isfinite(float(o.iloc[i])):
                pivots.append(i)
    if len(pivots) < 2:
        return False
    p1, p2 = pivots[-2], pivots[-1]
    return 5 <= p2-p1 <= 45 and c.iloc[p2] < c.iloc[p1] and o.iloc[p2] > o.iloc[p1]


def _supertrend(high, low, close, period: int = 10, multiplier: float = 3.0):
    import pandas as pd
    prev = close.shift(1)
    tr = pd.concat([(high-low), (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    hl2 = (high + low) / 2
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr
    final_upper = upper.copy(); final_lower = lower.copy()
    trend = pd.Series(1, index=close.index, dtype='int64')
    for i in range(1, len(close)):
        if pd.isna(atr.iloc[i]):
            continue
        final_upper.iloc[i] = upper.iloc[i] if upper.iloc[i] < final_upper.iloc[i-1] or close.iloc[i-1] > final_upper.iloc[i-1] else final_upper.iloc[i-1]
        final_lower.iloc[i] = lower.iloc[i] if lower.iloc[i] > final_lower.iloc[i-1] or close.iloc[i-1] < final_lower.iloc[i-1] else final_lower.iloc[i-1]
        if trend.iloc[i-1] == -1 and close.iloc[i] > final_upper.iloc[i-1]: trend.iloc[i] = 1
        elif trend.iloc[i-1] == 1 and close.iloc[i] < final_lower.iloc[i-1]: trend.iloc[i] = -1
        else: trend.iloc[i] = trend.iloc[i-1]
    line = final_lower.where(trend == 1, final_upper)
    return line, trend




def _pivot_levels(high, low, close, atr_value: float, lookback: int = 180, pivot_span: int = 3):
    """Pivot tepe/dipleri ATR toleransıyla bölgelerde birleştirir."""
    highs = high.tail(lookback).reset_index(drop=True)
    lows = low.tail(lookback).reset_index(drop=True)
    last = float(close.iloc[-1])
    tolerance = max(last * 0.006, atr_value * 0.55 if atr_value > 0 else 0)

    raw_highs, raw_lows = [], []
    for i in range(pivot_span, len(highs) - pivot_span):
        h = float(highs.iloc[i]); l = float(lows.iloc[i])
        if h >= float(highs.iloc[i-pivot_span:i+pivot_span+1].max()): raw_highs.append((h, i))
        if l <= float(lows.iloc[i-pivot_span:i+pivot_span+1].min()): raw_lows.append((l, i))

    def cluster(points):
        groups=[]
        for price, idx in sorted(points, key=lambda x:x[1]):
            target=None
            for g in groups:
                if abs(price-g['price']) <= tolerance:
                    target=g; break
            if target is None:
                groups.append({'price':price,'touches':1,'last_idx':idx})
            else:
                target['price']=(target['price']*target['touches']+price)/(target['touches']+1)
                target['touches']+=1; target['last_idx']=max(target['last_idx'],idx)
        return groups

    hs=cluster(raw_highs); ls=cluster(raw_lows)
    supports=sorted([g for g in ls+hs if g['price'] < last*(1-0.001)], key=lambda g:g['price'], reverse=True)
    resistances=sorted([g for g in hs+ls if g['price'] > last*(1+0.001)], key=lambda g:g['price'])
    return supports[:2], resistances[:2], tolerance


def _clamp_score(value):
    return int(max(0, min(100, round(value))))




def _ai_breakout_model(close, high, low, volume, atr_series, rsi_series, ema20, ema50, ema200, macd_hist):
    """Açıklanabilir k-NN beta modeli. Benzer tarihsel teknik durumların 10 günlük sonucunu ölçer."""
    import numpy as np
    import pandas as pd
    prev = close.shift(1)
    tr = pd.concat([(high-low), (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
    avg_vol = volume.shift(1).rolling(20).mean()
    vol_ratio = (volume / avg_vol.replace(0, math.nan)).clip(0, 6)
    high20 = high.shift(1).rolling(20).max()
    dist_high = ((close / high20.replace(0, math.nan)) - 1).clip(-0.30, 0.15)
    candle_range = (high-low).replace(0, math.nan)
    strong_close = ((close-low)/candle_range).clip(0, 1).fillna(.5)
    tr_atr = (tr / atr_series.replace(0, math.nan)).clip(0, 5)
    ema_alignment = (((close/ema20)-1) + ((ema20/ema50)-1) + ((ema50/ema200)-1)).clip(-.5,.5)
    macd_norm = (macd_hist / close.replace(0, math.nan)).clip(-.1,.1)
    features = pd.DataFrame({
        'dist_high': dist_high/0.12, 'vol_ratio': (vol_ratio-1)/2, 'strong_close': (strong_close-.5)*2,
        'tr_atr': (tr_atr-1)/1.5, 'rsi': (rsi_series-50)/25, 'ema': ema_alignment/.12, 'macd': macd_norm/.02
    }).replace([np.inf,-np.inf], np.nan)
    current = features.iloc[-1]
    if current.isna().any() or len(features) < 300:
        return {'probability': 50, 'risk': 50, 'expectedReturn': 0.0, 'samples': 0, 'confidence': 0}
    candidates=[]
    # Son 12 bar sonuç için ayrılır; bugüne çok yakın örnekler veri sızıntısını önlemek için kullanılmaz.
    for i in range(210, len(close)-12):
        feat=features.iloc[i]
        if feat.isna().any():
            continue
        future=close.iloc[i+1:i+11]
        if len(future)<10 or close.iloc[i] <= 0:
            continue
        path=(future/close.iloc[i]-1)*100
        target_idx = next((j for j,v in enumerate(path.tolist()) if v>=5.0), None)
        stop_idx = next((j for j,v in enumerate(path.tolist()) if v<=-3.0), None)
        success = target_idx is not None and (stop_idx is None or target_idx < stop_idx)
        stop_hit = stop_idx is not None and (target_idx is None or stop_idx < target_idx)
        ret10=float(path.iloc[-1])
        dist=float(np.sqrt(((feat-current)**2).mean()))
        candidates.append((dist, 1 if success else 0, 1 if stop_hit else 0, ret10))
    if len(candidates)<20:
        return {'probability': 50, 'risk': 50, 'expectedReturn': 0.0, 'samples': len(candidates), 'confidence': min(35,len(candidates))}
    nearest=sorted(candidates,key=lambda x:x[0])[:min(50,len(candidates))]
    weights=np.array([1/(x[0]+0.12) for x in nearest],dtype=float)
    successes=np.array([x[1] for x in nearest],dtype=float)
    stops=np.array([x[2] for x in nearest],dtype=float)
    returns=np.array([x[3] for x in nearest],dtype=float)
    raw_prob=float(np.average(successes,weights=weights))*100
    # Küçük örneklemi %50'ye doğru daralt.
    shrink=min(1.0,len(nearest)/40)
    probability=50+(raw_prob-50)*shrink
    risk=float(np.average(stops,weights=weights))*100
    expected=float(np.average(returns,weights=weights))
    dispersion=float(np.average((successes-np.average(successes,weights=weights))**2,weights=weights))
    confidence=max(0,min(100, len(nearest)*2.0*(1-dispersion)))
    return {'probability': _clamp_score(probability), 'risk': _clamp_score(risk), 'expectedReturn': round(expected,2), 'samples': len(nearest), 'confidence': _clamp_score(confidence)}

def _analyze(symbol: str, ticker: str, df, config: dict | None = None, benchmark_close=None):
    import pandas as pd
    config = config or {}
    required = {"Open", "High", "Low", "Close", "Volume"}
    if df is None or df.empty or not required.issubset(set(map(str, df.columns))): return None
    clean = pd.concat({"close": _series(df,"Close"), "high": _series(df,"High"), "low": _series(df,"Low"), "volume": _series(df,"Volume")}, axis=1).dropna()
    if len(clean) < 220: return None
    close, high, low, volume = clean.close, clean.high, clean.low, clean.volume
    last_close, prev_close = float(close.iloc[-1]), float(close.iloc[-2])
    if not math.isfinite(last_close) or last_close <= 0: return None
    change = _finite((last_close / prev_close - 1) * 100)

    avg_vol = _finite(volume.shift(1).rolling(20).mean().iloc[-1])
    volume_ratio = _finite(volume.iloc[-1] / avg_vol) if avg_vol > 0 else 0.0
    volume_spike = volume_ratio >= float(config.get("volumeSpike", 1.5))

    delta = close.diff(); gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean(); loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rsi_series = 100 - 100 / (1 + gain / loss.replace(0, math.nan))
    rsi = _finite(rsi_series.iloc[-1], 50.0)

    prev = close.shift(1); tr = pd.concat([(high-low),(high-prev).abs(),(low-prev).abs()],axis=1).max(axis=1); atr_series = tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    atr_value = _finite(atr_series.iloc[-1])
    tr_atr = _finite(tr.iloc[-1] / atr_value) if atr_value > 0 else 0.0

    donchian_len = int(config.get("donchianLength", 20))
    resistance = _finite(high.shift(1).rolling(donchian_len).max().iloc[-1])
    breakout_pct = _finite((last_close/resistance-1)*100) if resistance > 0 else 0.0
    donchian_breakout = breakout_pct > 0

    candle_range = float(high.iloc[-1]-low.iloc[-1]); strong_close = (last_close-float(low.iloc[-1]))/candle_range if candle_range > 0 else .5

    ema20, ema50, ema200 = _ema(close,20), _ema(close,50), _ema(close,200)
    ema_trend = bool(last_close > ema20.iloc[-1] > ema50.iloc[-1] > ema200.iloc[-1])

    bb_mid = close.rolling(20).mean(); bb_std = close.rolling(20).std(ddof=0)
    bb_upper, bb_lower = bb_mid + 2*bb_std, bb_mid - 2*bb_std
    bb_width = _finite(((bb_upper-bb_lower)/bb_mid*100).iloc[-1])
    bb_width_avg = _finite(((bb_upper-bb_lower)/bb_mid*100).rolling(120).mean().iloc[-1])
    bollinger_squeeze = bool(bb_width > 0 and bb_width_avg > 0 and bb_width <= bb_width_avg * float(config.get("squeezeFactor", .70)))

    macd_line = _ema(close,12) - _ema(close,26); macd_signal = _ema(macd_line,9); macd_hist = macd_line-macd_signal
    macd_bullish = bool(macd_line.iloc[-1] > macd_signal.iloc[-1] and macd_hist.iloc[-1] > macd_hist.iloc[-2])

    st_line, st_dir = _supertrend(high,low,close,10,3.0)
    supertrend_buy = bool(st_dir.iloc[-1] == 1 and last_close >= st_line.iloc[-1])

    rsi_div = _positive_divergence(close, rsi_series)
    macd_div = _positive_divergence(close, macd_hist)
    positive_divergence = bool(rsi_div or macd_div)

    supports, resistances, level_tolerance = _pivot_levels(high, low, close, atr_value)
    support1 = supports[0]['price'] if supports else _finite(low.shift(1).rolling(60).min().iloc[-1])
    support2 = supports[1]['price'] if len(supports)>1 else 0.0
    resistance1 = resistances[0]['price'] if resistances else resistance
    resistance2 = resistances[1]['price'] if len(resistances)>1 else 0.0
    support_dist = _finite((last_close/support1-1)*100) if support1>0 else 0.0
    resistance_dist = _finite((resistance1/last_close-1)*100) if resistance1>0 else 0.0

    # Gelişmiş breakout skoru: yalnızca kırılım değil, kırılıma hazırlık kalitesini de ölçer.
    breakout_score = 0
    breakout_score += 22 if donchian_breakout else max(0, 18 - min(18, max(0, resistance_dist)*4))
    breakout_score += min(16, max(0, (volume_ratio-0.8)*12))
    breakout_score += max(0, min(12, (strong_close-0.45)*30))
    breakout_score += max(0, min(10, (tr_atr-0.7)*12))
    breakout_score += 12 if ema_trend else (7 if last_close>ema20.iloc[-1]>ema50.iloc[-1] else 0)
    breakout_score += 8 if macd_bullish else 0
    breakout_score += 6 if supertrend_buy else 0
    breakout_score += 5 if rsi>=55 else (2 if rsi>=50 else 0)
    breakout_score += 5 if positive_divergence else 0
    breakout_score = _clamp_score(breakout_score)

    atr_pct = _finite(atr_value/last_close*100) if last_close else 0
    atr_pct_avg = _finite((atr_series/close*100).rolling(120).mean().iloc[-1])
    range20 = _finite((high.rolling(20).max().iloc[-1]-low.rolling(20).min().iloc[-1])/last_close*100)
    range120_avg = _finite(((high.rolling(20).max()-low.rolling(20).min())/close*100).rolling(120).mean().iloc[-1])
    vol_dry = _finite(avg_vol / _finite(volume.shift(1).rolling(60).mean().iloc[-1], avg_vol)) if avg_vol>0 else 1
    ema_spread = _finite((max(ema20.iloc[-1],ema50.iloc[-1],ema200.iloc[-1])-min(ema20.iloc[-1],ema50.iloc[-1],ema200.iloc[-1]))/last_close*100)
    squeeze_score = 0
    squeeze_score += max(0,min(30,(1-bb_width/max(bb_width_avg,0.0001))*60)) if bb_width_avg>0 else 0
    squeeze_score += max(0,min(20,(1-atr_pct/max(atr_pct_avg,0.0001))*40)) if atr_pct_avg>0 else 0
    squeeze_score += max(0,min(20,(1-range20/max(range120_avg,0.0001))*40)) if range120_avg>0 else 0
    squeeze_score += max(0,min(15,(1-vol_dry)*30))
    squeeze_score += max(0,min(15,(3.0-ema_spread)*5))
    squeeze_score = _clamp_score(squeeze_score)

    # Para Akışı / Kurumsal Birikim skoru (fiyat-hacim tabanlı tahmin).
    hl_range = (high-low).replace(0, math.nan)
    money_flow_multiplier = (((close-low) - (high-close)) / hl_range).fillna(0.0)
    money_flow_volume = money_flow_multiplier * volume
    cmf20 = _finite(money_flow_volume.rolling(20).sum().iloc[-1] / volume.rolling(20).sum().iloc[-1]) if _finite(volume.rolling(20).sum().iloc[-1]) > 0 else 0.0

    direction = close.diff().fillna(0.0)
    signed_volume = volume.where(direction > 0, -volume.where(direction < 0, 0.0))
    obv = signed_volume.cumsum()
    obv_base = abs(_finite(obv.iloc[-21], 1.0)) or 1.0
    obv_slope20 = _finite((obv.iloc[-1]-obv.iloc[-21]) / obv_base * 100)

    adl = money_flow_volume.cumsum()
    adl_base = abs(_finite(adl.iloc[-21], 1.0)) or 1.0
    adl_slope20 = _finite((adl.iloc[-1]-adl.iloc[-21]) / adl_base * 100)

    up_volume = _finite(volume.where(direction > 0, 0.0).tail(20).sum())
    down_volume = _finite(volume.where(direction < 0, 0.0).tail(20).sum())
    up_down_volume_ratio = up_volume / down_volume if down_volume > 0 else (3.0 if up_volume > 0 else 1.0)
    pv_confirmation = _finite(close.pct_change().tail(20).corr(volume.pct_change().tail(20)), 0.0)

    money_flow_score = 0
    money_flow_score += max(0, min(30, 15 + cmf20 * 75))
    money_flow_score += max(0, min(25, 12.5 + obv_slope20 * 1.25))
    money_flow_score += max(0, min(20, 10 + adl_slope20))
    money_flow_score += max(0, min(15, (up_down_volume_ratio-0.5)*10))
    money_flow_score += max(0, min(10, 5 + pv_confirmation*10))
    money_flow_score = _clamp_score(money_flow_score)
    money_flow_positive = bool(money_flow_score >= 60 and cmf20 > 0)

    conditions = {
        "breakout": donchian_breakout,
        "volumeSpike": volume_spike,
        "bollingerSqueeze": bollinger_squeeze,
        "emaTrend": ema_trend,
        "rsiPositive": rsi >= 50,
        "macdBullish": macd_bullish,
        "atrExpansion": tr_atr >= float(config.get("atrRatio", 1.0)),
        "supertrendBuy": supertrend_buy,
        "positiveDivergence": positive_divergence,
        "moneyFlowPositive": money_flow_positive,
    }
    weights = {"breakout":18,"volumeSpike":14,"bollingerSqueeze":8,"emaTrend":15,"rsiPositive":8,"macdBullish":10,"atrExpansion":10,"supertrendBuy":10,"positiveDivergence":7}
    technical_score = sum(weights[k] for k,v in conditions.items() if v)
    technical_score += min(5, max(0, round(max(0, breakout_pct)*2)))
    technical_score = _clamp_score(technical_score)
    # XU100 göreceli performans ham değerleri. Nihai 0-100 RS puanı tarama evreninde yüzdelik sıralamayla atanır.
    rs_excess = {}
    benchmark_returns = {}
    if benchmark_close is not None and len(benchmark_close) > 0:
        aligned = pd.concat({"stock": close, "bench": benchmark_close}, axis=1).dropna()
        for days in (20, 60, 120, 252):
            if len(aligned) > days:
                stock_ret = _finite((aligned.stock.iloc[-1] / aligned.stock.iloc[-days-1] - 1) * 100)
                bench_ret = _finite((aligned.bench.iloc[-1] / aligned.bench.iloc[-days-1] - 1) * 100)
                rs_excess[str(days)] = stock_ret - bench_ret
                benchmark_returns[str(days)] = bench_ret
            else:
                rs_excess[str(days)] = 0.0
                benchmark_returns[str(days)] = 0.0
    else:
        rs_excess = {str(d): 0.0 for d in (20,60,120,252)}
        benchmark_returns = {str(d): 0.0 for d in (20,60,120,252)}

    score = _clamp_score(technical_score*.35 + breakout_score*.32 + squeeze_score*.13 + money_flow_score*.20)
    ai = _ai_breakout_model(close, high, low, volume, atr_series, rsi_series, ema20, ema50, ema200, macd_hist)

    modes = config.get("modes", {})
    passed = all(conditions.get(k, False) for k,v in modes.items() if v == "required")
    setup = "Breakout" if donchian_breakout and breakout_score>=65 else ("Sıkışma" if squeeze_score>=70 else ("Güçlü Trend" if ema_trend and supertrend_buy and macd_bullish else ("Pozitif Uyumsuzluk" if positive_divergence else ("İzleme" if score>=50 else "Zayıf"))))
    return {"symbol":symbol,"name":symbol_name(symbol),"sector":symbol_sector(symbol),"close":round(last_close,2),"changePct":round(change,2),"volumeRatio":round(volume_ratio,2),"volume":int(_finite(volume.iloc[-1])),"avgVolume20":int(avg_vol),"rsi":round(rsi,1),"trAtr":round(tr_atr,2),"atr":round(atr_value,2),"breakoutPct":round(breakout_pct,2),"bbWidth":round(bb_width,2),"score":score,"technicalScore":technical_score,"breakoutScore":breakout_score,"squeezeScore":squeeze_score,"moneyFlowScore":money_flow_score,"aiBreakoutProbability":ai["probability"],"aiRiskScore":ai["risk"],"aiExpectedReturn10d":ai["expectedReturn"],"aiSampleSize":ai["samples"],"aiModelConfidence":ai["confidence"],"rsExcess":{k:round(_finite(v),2) for k,v in rs_excess.items()},"benchmarkReturns":{k:round(_finite(v),2) for k,v in benchmark_returns.items()},"cmf20":round(cmf20,3),"obvSlope20":round(obv_slope20,2),"adlSlope20":round(adl_slope20,2),"upDownVolumeRatio":round(up_down_volume_ratio,2),"priceVolumeCorrelation":round(pv_confirmation,3),"setup":setup,"passed":passed,"conditions":conditions,"support1":round(_finite(support1),2),"support2":round(_finite(support2),2),"resistance1":round(_finite(resistance1),2),"resistance2":round(_finite(resistance2),2),"supportDistancePct":round(support_dist,2),"resistanceDistancePct":round(resistance_dist,2),"supportTouches":supports[0]['touches'] if supports else 0,"resistanceTouches":resistances[0]['touches'] if resistances else 0,"levelTolerance":round(_finite(level_tolerance),2),"ema20":round(_finite(ema20.iloc[-1]),2),"ema50":round(_finite(ema50.iloc[-1]),2),"ema200":round(_finite(ema200.iloc[-1]),2),"macd":round(_finite(macd_line.iloc[-1]),3),"macdSignal":round(_finite(macd_signal.iloc[-1]),3),"supertrend":round(_finite(st_line.iloc[-1]),2),"market":"BIST","dataDate":clean.index[-1].strftime("%Y-%m-%d") if hasattr(clean.index[-1],"strftime") else str(clean.index[-1])}

def _percentile_scores(values: list[float]) -> list[int]:
    if not values:
        return []
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [50] * len(values)
    denom = max(1, len(values)-1)
    for rank, idx in enumerate(order):
        out[idx] = int(round(rank / denom * 99 + 1))
    return out

def enrich_relative_sector_composite(rows: list[dict]) -> dict:
    """RS yüzdelikleri, sektör sıralaması, composite ve radar üretir."""
    if not rows:
        return {"sectorRanking": [], "radar": []}
    periods = ("20","60","120","252")
    period_scores = {}
    for period in periods:
        vals = [_finite((r.get("rsExcess") or {}).get(period)) for r in rows]
        period_scores[period] = _percentile_scores(vals)
    weights = {"20": .15, "60": .25, "120": .30, "252": .30}
    for i, r in enumerate(rows):
        for period in periods:
            r[f"rs{period}"] = period_scores[period][i]
        r["rsScore"] = _clamp_score(sum(r[f"rs{p}"] * weights[p] for p in periods))
        r["rsTrend"] = "Yükseliyor" if r["rs20"] >= r["rs60"] >= max(1, r["rs120"]-8) else ("Zayıflıyor" if r["rs20"]+8 < r["rs60"] else "Dengeli")

    sectors = {}
    for r in rows:
        sectors.setdefault(r.get("sector") or "Diğer", []).append(r)
    sector_rows = []
    for sector, members in sectors.items():
        avg_rs = sum(int(x.get("rsScore",50)) for x in members)/len(members)
        avg_change = sum(_finite(x.get("changePct")) for x in members)/len(members)
        avg_money = sum(int(x.get("moneyFlowScore",50)) for x in members)/len(members)
        sector_score = _clamp_score(avg_rs*.60 + max(0,min(100,50+avg_change*8))*.20 + avg_money*.20)
        sector_rows.append({"sector":sector,"count":len(members),"rsScore":round(avg_rs),"changePct":round(avg_change,2),"moneyFlowScore":round(avg_money),"score":sector_score})
    sector_rows.sort(key=lambda x:x["score"], reverse=True)
    sector_rank = {x["sector"]: i+1 for i,x in enumerate(sector_rows)}
    sector_score_map = {x["sector"]: x["score"] for x in sector_rows}
    for r in rows:
        r["sectorRank"] = sector_rank.get(r.get("sector") or "Diğer", len(sector_rows))
        r["sectorScore"] = sector_score_map.get(r.get("sector") or "Diğer", 50)
        r["compositeScore"] = _clamp_score(
            int(r.get("technicalScore",0))*.22 + int(r.get("breakoutScore",0))*.20 +
            int(r.get("squeezeScore",0))*.10 + int(r.get("moneyFlowScore",0))*.16 +
            int(r.get("rsScore",50))*.19 + int(r.get("sectorScore",50))*.08 +
            int(r.get("aiBreakoutProbability",50))*.05
        )
        r["score"] = r["compositeScore"]
        r["stars"] = 5 if r["compositeScore"] >= 90 else 4 if r["compositeScore"] >= 80 else 3 if r["compositeScore"] >= 70 else 2 if r["compositeScore"] >= 55 else 1
    rows.sort(key=lambda x:(x.get("compositeScore",0),x.get("rsScore",0)), reverse=True)
    radar = [r for r in rows if r.get("compositeScore",0)>=75 and r.get("rsScore",0)>=65 and r.get("moneyFlowScore",0)>=55 and r.get("aiBreakoutProbability",50)>=55][:15]
    return {"sectorRanking": sector_rows, "radar": radar}

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
LAST_ROWS: list[dict] = []
MARKET_CARDS_CACHE: dict = {"at": 0.0, "data": None}
DASHBOARD_CACHE: dict = {"at": 0.0, "data": None}


def _set_job(job_id: str, **updates):
    with JOBS_LOCK:
        JOBS[job_id].update(updates)

def run_scan_job(job_id: str, universe: str, config: dict | None = None):
    global LAST_ROWS
    try:
        import pandas as pd  # noqa
        import yfinance as yf  # noqa
    except Exception:
        _set_job(job_id, state="error", message="Canlı veri bileşenleri kurulu değil. CANLI_VERI_KUR.bat dosyasını çalıştırın.")
        return
    symbols, universe_note = symbols_for_universe(universe)
    if not symbols:
        _set_job(job_id, state="error", message=universe_note or "Sembol bulunamadı.")
        return
    symbols = [canonical_symbol(s) for s in symbols]
    tickers = [yahoo_ticker(s) for s in symbols]
    batches = [tickers[i:i+35] for i in range(0,len(tickers),35)]
    benchmark_close = None
    try:
        import yfinance as yf
        bench = yf.download("XU100.IS", period="2y", interval="1d", auto_adjust=False, actions=False, progress=False, threads=False, timeout=35)
        if bench is not None and not bench.empty:
            benchmark_close = _series(bench, "Close")
    except Exception:
        benchmark_close = None
    _set_job(job_id, total=len(symbols), phase="download", message="Yahoo Finance verileri indiriliyor…")
    raw_by_ticker = {}; download_errors=[]; completed_batches=0
    with ThreadPoolExecutor(max_workers=min(4,len(batches))) as pool:
        futures = {pool.submit(_download_batch,b,"2y","1d"): b for b in batches}
        for future in as_completed(futures):
            try: raw_by_ticker.update(future.result())
            except Exception as exc: download_errors.append(str(exc))
            completed_batches += 1
            estimated = min(len(symbols), round(completed_batches/len(batches)*len(symbols)*0.45))
            _set_job(job_id, processed=estimated, percent=round(estimated/len(symbols)*100), message=f"Veri partileri indiriliyor: {completed_batches}/{len(batches)}")
    rows=[]; failed=[]
    _set_job(job_id, phase="analyze", message="Teknik göstergeler hesaplanıyor…")
    for index,(symbol,ticker) in enumerate(zip(symbols,tickers),start=1):
        try:
            row=_analyze(symbol,ticker,raw_by_ticker.get(ticker), config, benchmark_close)
            if row: rows.append(row)
            else: failed.append(symbol)
        except Exception: failed.append(symbol)
        processed=max(round(len(symbols)*.45), round(len(symbols)*.45 + index/len(symbols)*len(symbols)*.55))
        if index % 5 == 0 or index == len(symbols):
            _set_job(job_id, processed=min(processed,len(symbols)), percent=min(100,round(processed/len(symbols)*100)), found=len(rows), failed=len(failed), message=f"Analiz ediliyor: {index}/{len(symbols)}")
    enrichment = enrich_relative_sector_composite(rows)
    warnings=[]
    if universe_note: warnings.append(universe_note)
    if download_errors: warnings.append(f"{len(download_errors)} veri partisi indirilemedi.")
    if failed: warnings.append(f"{len(failed)} sembol için geçerli veri alınamadı: {', '.join(failed[:8])}" + ("…" if len(failed)>8 else ""))
    payload={"rows":rows,"sectorRanking":enrichment.get("sectorRanking",[]),"radar":enrichment.get("radar",[]),"failedSymbols":failed,"updatedAt":time.strftime("%Y-%m-%d %H:%M:%S"),"universe":universe,"requested":len(symbols),"warning":" ".join(warnings) or None}
    LAST_ROWS=rows
    LAST_SCAN_PATH.write_text(json.dumps(_json_safe(payload),ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    _set_job(job_id,state="done",processed=len(symbols),percent=100,found=len(rows),failed=len(failed),message="Tarama tamamlandı.",result=payload)



def _market_assets() -> list[tuple[str, str, str, str]]:
    return [
        ("XU100", "BIST 100", "XU100.IS", "Endeks"),
        ("XU030", "BIST 30", "XU030.IS", "Endeks"),
        ("XBANK", "BIST Banka", "XBANK.IS", "Endeks"),
        ("XUSIN", "BIST Sınai", "XUSIN.IS", "Endeks"),
        ("USDTRY", "Dolar / TL", "TRY=X", "Döviz"),
        ("EURTRY", "Euro / TL", "EURTRY=X", "Döviz"),
        ("XAUUSD", "Altın Ons / USD", "GC=F", "Emtia"),
        ("XAGUSD", "Gümüş / USD", "SI=F", "Emtia"),
        ("BRENT", "Brent Petrol / USD", "BZ=F", "Enerji"),
        ("WTI", "WTI Petrol / USD", "CL=F", "Enerji"),
        ("NATGAS", "Doğal Gaz / USD", "NG=F", "Enerji"),
        ("COPPER", "Bakır / USD", "HG=F", "Metal"),
        ("PLATIN", "Platin / USD", "PL=F", "Metal"),
        ("PALLAD", "Paladyum / USD", "PA=F", "Metal"),
        ("WHEAT", "Buğday / USD", "ZW=F", "Tarım"),
        ("CORN", "Mısır / USD", "ZC=F", "Tarım"),
        ("BTCUSD", "Bitcoin / USD", "BTC-USD", "Kripto"),
    ]

def _card_from_frame(code: str, name: str, category: str, frame) -> dict | None:
    if frame is None or frame.empty or "Close" not in frame.columns:
        return None
    close = _series(frame, "Close").dropna()
    if len(close) < 2:
        return None
    value = _finite(close.iloc[-1])
    change = _finite((close.iloc[-1] / close.iloc[-2] - 1) * 100)
    spark = [round(_finite(v), 4) for v in close.tail(32).tolist()]
    return {"code": code, "name": name, "category": category, "value": round(value, 2), "changePct": round(change, 2), "spark": spark}

def load_market_cards(force: bool = False) -> dict:
    """Taramadan tamamen bağımsız canlı endeks/döviz/emtia/kripto kartları."""
    now = time.time()
    if not force and MARKET_CARDS_CACHE.get("data") and now - float(MARKET_CARDS_CACHE.get("at", 0)) < 900:
        return MARKET_CARDS_CACHE["data"]
    assets = _market_assets()
    cards: list[dict] = []
    warnings: list[str] = []
    try:
        import yfinance as yf
        tickers = [x[2] for x in assets]
        raw = yf.download(tickers=tickers, period="3mo", interval="1d", group_by="ticker", auto_adjust=False, actions=False, progress=False, threads=True, timeout=30)
        found = set()
        for code, name, ticker, category in assets:
            card = _card_from_frame(code, name, category, _ticker_frame(raw, ticker))
            if card:
                cards.append(card); found.add(ticker)
        # Toplu indirmede eksik kalan kartları tek tek dene. Bir sorun tüm kartları düşürmesin.
        for code, name, ticker, category in assets:
            if ticker in found:
                continue
            try:
                frame = yf.download(ticker, period="3mo", interval="1d", auto_adjust=False, actions=False, progress=False, threads=False, timeout=15)
                card = _card_from_frame(code, name, category, frame)
                if card:
                    cards.append(card)
                else:
                    warnings.append(f"{code} verisi alınamadı")
            except Exception:
                warnings.append(f"{code} verisi alınamadı")
    except Exception as exc:
        cached = MARKET_CARDS_CACHE.get("data")
        if cached:
            return {**cached, "warning": f"Canlı kartlar yenilenemedi; önbellek gösteriliyor: {exc}", "fromCache": True}
        return {"cards": [], "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"), "warning": str(exc), "fromCache": False}
    payload = {"cards": cards, "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"), "warning": "; ".join(warnings[:6]) or None, "fromCache": False}
    if cards:
        MARKET_CARDS_CACHE.update({"at": now, "data": payload})
    return payload

def load_scan_dashboard() -> dict:
    """Yalnızca son taramadan türetilen breadth, ısı haritası ve sağlık verileri."""
    scan = load_last_scan()
    rows = scan.get("rows", []) if isinstance(scan, dict) else []
    updated = scan.get("updatedAt") if isinstance(scan, dict) else None
    return {
        "hasScan": bool(rows),
        "health": market_health_from_rows(rows),
        "breadth": market_breadth_from_rows(rows),
        "marketLists": market_lists_from_rows(rows),
        "updatedAt": updated or "Henüz tarama yapılmadı",
    }

def load_market_dashboard(force: bool = False) -> dict:
    """Geriye dönük uyumluluk için birleşik dashboard yanıtı."""
    cards = load_market_cards(force=force)
    scan = load_scan_dashboard()
    return {**scan, **cards}

def market_breadth_from_rows(rows: list[dict]) -> dict:
    """Son taramadan temel piyasa genişliği ölçülerini üretir."""
    total = len(rows)
    if not total:
        return {"total": 0, "advancing": 0, "declining": 0, "unchanged": 0, "advancePct": 0, "aboveEmaPct": 0, "volumeSpikePct": 0, "strongPct": 0}
    advancing = sum(1 for r in rows if _finite(r.get("changePct")) > 0)
    declining = sum(1 for r in rows if _finite(r.get("changePct")) < 0)
    unchanged = total - advancing - declining
    above_ema = sum(1 for r in rows if (r.get("conditions") or {}).get("emaTrend"))
    volume_spike = sum(1 for r in rows if (r.get("conditions") or {}).get("volumeSpike"))
    strong = sum(1 for r in rows if int(r.get("score", 0)) >= 70)
    return {
        "total": total, "advancing": advancing, "declining": declining, "unchanged": unchanged,
        "advancePct": round(advancing / total * 100),
        "aboveEmaPct": round(above_ema / total * 100),
        "volumeSpikePct": round(volume_spike / total * 100),
        "strongPct": round(strong / total * 100),
    }

def market_lists_from_rows(rows: list[dict]) -> dict:
    """Son taramadan yükselen, düşen, hacimli ve sıcaklık haritası verileri."""
    clean = [r for r in rows if isinstance(r, dict) and r.get("symbol")]
    advancers = sorted(clean, key=lambda r: _finite(r.get("changePct")), reverse=True)[:20]
    decliners = sorted(clean, key=lambda r: _finite(r.get("changePct")))[:20]
    volume_leaders = sorted(clean, key=lambda r: (_finite(r.get("volumeRatio")), _finite(r.get("volume"))), reverse=True)[:20]
    heatmap = sorted(clean, key=lambda r: (int(r.get("score", 0)), abs(_finite(r.get("changePct")))), reverse=True)[:80]
    def slim(r):
        return {
            "symbol": r.get("symbol"), "name": r.get("name"),
            "changePct": round(_finite(r.get("changePct")), 2),
            "volumeRatio": round(_finite(r.get("volumeRatio")), 2),
            "volume": int(_finite(r.get("volume"))),
            "score": int(r.get("score", 0)), "setup": r.get("setup", "")
        }
    return {
        "advancers": [slim(r) for r in advancers if _finite(r.get("changePct")) > 0],
        "decliners": [slim(r) for r in decliners if _finite(r.get("changePct")) < 0],
        "volumeLeaders": [slim(r) for r in volume_leaders],
        "heatmap": [slim(r) for r in heatmap],
    }

def market_health_from_rows(rows: list[dict]) -> dict:
    total = len(rows)
    if not total:
        return {"score": 0, "label": "Tarama bekleniyor", "positivePct": 0, "trendPct": 0, "breakouts": 0, "distribution": 0, "reasons": []}
    positive = sum(1 for r in rows if _finite(r.get("changePct")) > 0)
    trend = sum(1 for r in rows if (r.get("conditions") or {}).get("emaTrend"))
    breakouts = sum(1 for r in rows if (r.get("conditions") or {}).get("breakout"))
    high_scores = sum(1 for r in rows if int(r.get("score", 0)) >= 70)
    positive_pct = round(positive / total * 100)
    trend_pct = round(trend / total * 100)
    score = int(max(0, min(100, round(positive_pct * .30 + trend_pct * .35 + min(100, breakouts / total * 500) * .15 + min(100, high_scores / total * 200) * .20))))
    label = "Güçlü yükseliş" if score >= 75 else "Olumlu" if score >= 60 else "Temkinli" if score >= 45 else "Zayıf"
    return {
        "score": score, "label": label, "positivePct": positive_pct, "trendPct": trend_pct, "breakouts": breakouts, "distribution": total-positive,
        "reasons": [
            {"name": "Pozitif kapanış oranı", "value": positive_pct, "impact": round((positive_pct-50)*.30)},
            {"name": "EMA trend uyumu", "value": trend_pct, "impact": round((trend_pct-35)*.35)},
            {"name": "Breakout sayısı", "value": breakouts, "impact": min(15, breakouts)},
            {"name": "70+ skor hisseler", "value": high_scores, "impact": min(20, high_scores)},
        ]
    }


def _rsi(series, length: int = 14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/length, adjust=False, min_periods=length).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/length, adjust=False, min_periods=length).mean()
    rs = gain / loss.replace(0, math.nan)
    return 100 - (100 / (1 + rs))

def _backtest_symbol(symbol: str, frame, cfg: dict, benchmark_frame=None):
    import numpy as np
    import pandas as pd
    if frame is None or frame.empty or len(frame) < 280:
        return None
    df=frame.copy().dropna(subset=['Close','High','Low','Volume'])
    if len(df)<280: return None
    close=_series(df,'Close'); high=_series(df,'High'); low=_series(df,'Low'); volume=_series(df,'Volume')
    don=int(cfg.get('donchian',20)); rsi_min=float(cfg.get('rsiMin',55)); vol_min=float(cfg.get('volumeMin',1.5))
    money_min=float(cfg.get('moneyFlowMin',55)); squeeze_min=float(cfg.get('squeezeMin',60)); rs_min=float(cfg.get('rsMin',0))
    target=float(cfg.get('targetPct',5))/100; stop=float(cfg.get('stopPct',3))/100; hold=int(cfg.get('holdingDays',10))

    enabled={k: str(cfg.get(k,'true')).lower() in ('1','true','yes','on') for k in ['useBreakout','useRsi','useVolume','useEma','useSupertrend','useMoneyFlow','useSqueeze','useRs']}

    rsi=_rsi(close,14)
    resistance=high.shift(1).rolling(don).max()
    avg_vol=volume.shift(1).rolling(20).mean(); vol_ratio=volume/avg_vol.replace(0,math.nan)
    ema20=close.ewm(span=20,adjust=False).mean(); ema50=close.ewm(span=50,adjust=False).mean(); ema200=close.ewm(span=200,adjust=False).mean()
    st_line,st_dir=_supertrend(high,low,close,10,3.0)

    typical=(high+low+close)/3
    mf_mult=((close-low)-(high-close))/(high-low).replace(0,math.nan)
    mf_vol=mf_mult*volume
    cmf20=mf_vol.rolling(20).sum()/volume.rolling(20).sum().replace(0,math.nan)
    obv=(np.sign(close.diff()).fillna(0)*volume).cumsum()
    obv_slope=(obv-obv.shift(20))/obv.shift(20).abs().replace(0,math.nan)*100
    money_score=(50+cmf20*80+obv_slope.clip(-20,20)).clip(0,100)

    mid=close.rolling(20).mean(); std=close.rolling(20).std(ddof=0)
    bb_width=((mid+2*std)-(mid-2*std))/mid.replace(0,math.nan)*100
    bb_avg=bb_width.rolling(120,min_periods=40).mean()
    tr=pd.concat([(high-low),(high-close.shift()).abs(),(low-close.shift()).abs()],axis=1).max(axis=1)
    atr=tr.rolling(14).mean(); atr_pct=atr/close.replace(0,math.nan)*100; atr_avg=atr_pct.rolling(120,min_periods=40).mean()
    range20=(high.rolling(20).max()-low.rolling(20).min())/close.replace(0,math.nan)*100
    range_avg=range20.rolling(120,min_periods=40).mean()
    vol_dry=volume.rolling(10).mean()/volume.rolling(50).mean().replace(0,math.nan)
    ema_spread=(pd.concat([ema20,ema50,ema200],axis=1).max(axis=1)-pd.concat([ema20,ema50,ema200],axis=1).min(axis=1))/close.replace(0,math.nan)*100
    squeeze_score=(
        ((1-bb_width/bb_avg.replace(0,math.nan))*60).clip(0,30).fillna(0)+
        ((1-atr_pct/atr_avg.replace(0,math.nan))*40).clip(0,20).fillna(0)+
        ((1-range20/range_avg.replace(0,math.nan))*40).clip(0,20).fillna(0)+
        ((1-vol_dry)*30).clip(0,15).fillna(0)+
        ((3-ema_spread)*5).clip(0,15).fillna(0)
    ).clip(0,100)

    rs_excess=pd.Series(index=close.index,dtype=float)
    if benchmark_frame is not None and not benchmark_frame.empty:
        bclose=_series(benchmark_frame,'Close').reindex(close.index).ffill()
        rs_excess=(close.pct_change(60)-bclose.pct_change(60))*100

    conditions={
        'useBreakout': close>resistance,
        'useRsi': rsi>=rsi_min,
        'useVolume': vol_ratio>=vol_min,
        'useEma': (close>ema50)&(ema20>ema50)&(ema50>ema200),
        'useSupertrend': (st_dir==1)&(close>=st_line),
        'useMoneyFlow': money_score>=money_min,
        'useSqueeze': squeeze_score>=squeeze_min,
        'useRs': rs_excess>=rs_min,
    }
    signal=pd.Series(True,index=close.index)
    for key,on in enabled.items():
        if on: signal &= conditions[key].fillna(False)

    entries=[]; last_exit=-1
    idxs=np.flatnonzero(signal.fillna(False).to_numpy())
    for i in idxs:
        if i<=last_exit or i+1>=len(close): continue
        entry_i=i+1; exit_i=min(entry_i+hold-1,len(close)-1)
        entry=float(close.iloc[entry_i]); future_high=high.iloc[entry_i:exit_i+1]; future_low=low.iloc[entry_i:exit_i+1]
        hit_target=None; hit_stop=None
        for j,(h,l) in enumerate(zip(future_high,future_low)):
            if hit_stop is None and float(l)<=entry*(1-stop): hit_stop=j
            if hit_target is None and float(h)>=entry*(1+target): hit_target=j
            if hit_target is not None or hit_stop is not None: break
        success=hit_target is not None and (hit_stop is None or hit_target<hit_stop)
        if success: realized=target*100; final_i=entry_i+hit_target
        elif hit_stop is not None: realized=-stop*100; final_i=entry_i+hit_stop
        else: realized=(float(close.iloc[exit_i])/entry-1)*100; final_i=exit_i
        mae=(float(future_low.min())/entry-1)*100; mfe=(float(future_high.max())/entry-1)*100
        entries.append({'date':str(df.index[entry_i].date()),'entry':round(entry,4),'returnPct':round(realized,2),'success':bool(success),'maePct':round(mae,2),'mfePct':round(mfe,2)})
        last_exit=final_i
    if not entries: return {'symbol':symbol,'name':symbol_name(symbol),'signals':0,'wins':0,'losses':0,'winRate':0,'avgReturn':0,'maxDrawdown':0,'avgMfe':0,'trades':[]}
    wins=sum(1 for x in entries if x['success']); returns=[x['returnPct'] for x in entries]
    return {'symbol':symbol,'name':symbol_name(symbol),'signals':len(entries),'wins':wins,'losses':len(entries)-wins,'winRate':round(wins/len(entries)*100,1),'avgReturn':round(sum(returns)/len(returns),2),'maxDrawdown':round(min(x['maePct'] for x in entries),2),'avgMfe':round(sum(x['mfePct'] for x in entries)/len(entries),2),'trades':entries[-100:]}

def run_backtest_job(job_id: str, universe: str, cfg: dict):
    symbols,warning=symbols_for_universe(universe)
    max_symbols=int(cfg.get('maxSymbols',100)); symbols=symbols[:max_symbols]
    period=str(cfg.get('period','10y'))
    with BACKTEST_LOCK: BACKTEST_JOBS[job_id].update({'total':len(symbols),'message':'Geçmiş fiyatlar indiriliyor…','warning':warning})
    results=[]; failed=[]
    try:
        benchmark_frames=_download_batch(['XU100.IS'],period,'1d')
        benchmark_frame=benchmark_frames.get('XU100.IS')
    except Exception:
        benchmark_frame=None
    batches=[symbols[i:i+20] for i in range(0,len(symbols),20)]
    processed=0
    for batch in batches:
        tickers=[yahoo_ticker(x) for x in batch]
        try: frames=_download_batch(tickers,period,'1d')
        except Exception: frames={}
        for symbol,ticker in zip(batch,tickers):
            try:
                item=_backtest_symbol(symbol,frames.get(ticker),cfg,benchmark_frame)
                if item is None: failed.append(symbol)
                else: results.append(item)
            except Exception: failed.append(symbol)
            processed+=1
            with BACKTEST_LOCK: BACKTEST_JOBS[job_id].update({'processed':processed,'percent':round(processed/max(len(symbols),1)*100),'message':f'{processed}/{len(symbols)} hisse test edildi'})
    active=[r for r in results if r['signals']>0]
    total=sum(r['signals'] for r in active); wins=sum(r['wins'] for r in active); losses=total-wins
    weighted=sum(r['avgReturn']*r['signals'] for r in active)/total if total else 0
    enabled_names=[]
    for key,label in [('useBreakout','Breakout'),('useRsi','RSI'),('useVolume','Hacim'),('useEma','EMA Trend'),('useSupertrend','SuperTrend'),('useMoneyFlow','Para Akışı'),('useSqueeze','Sıkışma'),('useRs','Relative Strength')]:
        if str(cfg.get(key,'true')).lower() in ('1','true','yes','on'): enabled_names.append(label)
    strategy_name=' + '.join(enabled_names) if enabled_names else 'Koşulsuz'
    summary={'period':period,'strategy':strategy_name,'symbolsTested':len(results),'totalSignals':total,'wins':wins,'losses':losses,'winRate':round(wins/total*100,1) if total else 0,'avgReturn':round(weighted,2),'maxDrawdown':round(min((r['maxDrawdown'] for r in active),default=0),2),'failed':failed,'config':cfg}
    payload={'summary':summary,'symbols':sorted(active,key=lambda r:(r['winRate'],r['signals']),reverse=True),'updatedAt':time.strftime('%Y-%m-%d %H:%M:%S')}
    LAST_BACKTEST_PATH.write_text(json.dumps(_json_safe(payload),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    try:
        history=json.loads(AI_LEARNING_PATH.read_text(encoding='utf-8')) if AI_LEARNING_PATH.exists() else []
        history.append({'updatedAt':payload['updatedAt'],'strategy':strategy_name,'config':cfg,'totalSignals':total,'winRate':summary['winRate'],'avgReturn':summary['avgReturn'],'maxDrawdown':summary['maxDrawdown']})
        AI_LEARNING_PATH.write_text(json.dumps(_json_safe(history[-500:]),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    except Exception:
        pass
    with BACKTEST_LOCK: BACKTEST_JOBS[job_id].update({'state':'done','percent':100,'processed':len(symbols),'message':'Backtest tamamlandı','result':payload})

def start_backtest(universe: str, cfg: dict):
    job_id=uuid.uuid4().hex
    with BACKTEST_LOCK: BACKTEST_JOBS[job_id]={'id':job_id,'state':'running','total':0,'processed':0,'percent':0,'message':'Backtest hazırlanıyor…'}
    threading.Thread(target=run_backtest_job,args=(job_id,universe,cfg),daemon=True).start()
    return BACKTEST_JOBS[job_id]

def load_last_backtest():
    try: return json.loads(LAST_BACKTEST_PATH.read_text(encoding='utf-8'))
    except Exception: return {'summary':None,'symbols':[],'updatedAt':None}

def start_scan(universe: str, config: dict | None = None) -> dict:
    job_id=uuid.uuid4().hex
    symbols,_=symbols_for_universe(universe)
    with JOBS_LOCK:
        JOBS[job_id]={"id":job_id,"state":"running","phase":"prepare","total":len(symbols),"processed":0,"percent":0,"found":0,"failed":0,"message":"Tarama hazırlanıyor…","createdAt":time.time()}
    threading.Thread(target=run_scan_job,args=(job_id,universe,config),daemon=True).start()
    return JOBS[job_id]

def load_last_scan():
    try: return json.loads(LAST_SCAN_PATH.read_text(encoding="utf-8"))
    except Exception: return {"rows":[],"updatedAt":None,"warning":None,"requested":0}

def xlsx_bytes(rows: list[dict]) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment
    except Exception as exc:
        raise RuntimeError("Excel çıktısı için openpyxl kurulu değil. CANLI_VERI_KUR.bat dosyasını tekrar çalıştırın.") from exc
    wb=Workbook(); ws=wb.active; ws.title="Tarama Sonuçları"
    headers=["Hisse","Şirket","Fiyat","Değişim %","Hacim Oranı","RSI","TR/ATR","Kırılım %","Skor","Durum","Veri Tarihi"]
    ws.append(headers)
    for cell in ws[1]: cell.font=Font(bold=True); cell.alignment=Alignment(horizontal="center")
    for r in rows: ws.append([r.get("symbol"),r.get("name"),r.get("close"),r.get("changePct"),r.get("volumeRatio"),r.get("rsi"),r.get("trAtr"),r.get("breakoutPct"),r.get("score"),r.get("setup"),r.get("dataDate")])
    widths=[12,42,12,12,14,10,10,12,10,14,14]
    for i,w in enumerate(widths,1): ws.column_dimensions[chr(64+i)].width=w
    out=io.BytesIO(); wb.save(out); return out.getvalue()


KAP_CACHE_FILE=ROOT/"kap_notifications_cache.json"
KAP_CACHE_LOCK=threading.Lock()

def _kap_normalize(text):
    import re
    t=(text or "").strip()
    return re.sub(r"\s+"," ",t)

def classify_kap_notification(title, summary=""):
    text=(f"{title} {summary}").casefold()
    positive={
        "Yeni İş / Sözleşme": ["yeni iş", "sözleşme", "ihale", "sipariş", "anlaşma", "proje kazan"],
        "Finansal Sonuç": ["finansal rapor", "finansal tablo", "faaliyet raporu", "kar payı", "temettü"],
        "Yatırım / Kapasite": ["yatırım", "kapasite art", "üretime baş", "fabrika", "tesis"],
        "Geri Alım": ["pay geri alım", "geri alım programı"],
        "Kredi Derecelendirme": ["kredi derecelendirme", "rating"],
    }
    negative={
        "Dava / Ceza": ["dava", "ceza", "soruşturma", "yaptırım"],
        "Faaliyet Kesintisi": ["üretime ara", "faaliyet dur", "yangın", "kaza", "grev"],
        "Sermaye Sulanması": ["bedelli sermaye", "tahsisli sermaye"],
        "Borç / Temerrüt": ["temerrüt", "borç yapılandır", "ödeme güçlüğü"],
    }
    category="Genel Açıklama"; score=50; reasons=[]
    for cat,words in positive.items():
        hits=[w for w in words if w in text]
        if hits:
            category=cat; score=max(score,68+min(24,len(hits)*6)); reasons.extend(hits)
    for cat,words in negative.items():
        hits=[w for w in words if w in text]
        if hits:
            category=cat; score=min(score,32-min(20,len(hits)*5)); reasons.extend(hits)
    if "özel durum açıklaması" in text and category=="Genel Açıklama": score=52
    sentiment="Olumlu" if score>=65 else "Olumsuz" if score<=35 else "Nötr"
    stars=max(1,min(5,round(score/20)))
    return {"category":category,"impactScore":int(score),"sentiment":sentiment,"stars":stars,"reasons":reasons[:4]}

def fetch_kap_notifications(force=False, limit=80):
    now=time.time()
    with KAP_CACHE_LOCK:
        if KAP_CACHE_FILE.exists() and not force:
            try:
                cached=json.loads(KAP_CACHE_FILE.read_text(encoding="utf-8"))
                if now-float(cached.get("timestamp",0))<600: return cached
            except Exception: pass
    headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123 Safari/537.36","Accept-Language":"tr-TR,tr;q=0.9"}
    urls=["https://www.kap.org.tr/tr/", "https://www.kap.org.tr/tr/bildirim-sorgu"]
    rows=[]; last_error=""
    for url in urls:
        try:
            response=requests.get(url,headers=headers,timeout=20); response.raise_for_status()
            soup=BeautifulSoup(response.text,"lxml")
            seen=set()
            for a in soup.select('a[href*="/tr/Bildirim/"], a[href*="/tr/bildirim/"]'):
                href=a.get("href","")
                if href.startswith("/"): href="https://www.kap.org.tr"+href
                if not href or href in seen: continue
                seen.add(href)
                title=_kap_normalize(a.get_text(" ",strip=True))
                parent=a.find_parent(["tr","li","div","article"])
                block=_kap_normalize(parent.get_text(" ",strip=True) if parent else title)
                if len(title)<4: title=block[:180]
                symbol=""
                import re
                candidates=re.findall(r"(?<![A-Z0-9])[A-Z]{3,6}(?![A-Z0-9])",block)
                ignore={"KAP","BIST","TURK","GENEL","OZEL","AÇIKLAMA","AŞ","TL","USD","EUR"}
                for c in candidates:
                    if c not in ignore and c+".IS" in SYMBOLS_BY_YAHOO:
                        symbol=c; break
                date_match=re.search(r"\b(\d{2}[./]\d{2}[./]\d{4})\b",block)
                time_match=re.search(r"\b(\d{2}:\d{2}(?::\d{2})?)\b",block)
                analysis=classify_kap_notification(title,block)
                rows.append({"symbol":symbol,"title":title[:260],"summary":block[:500],"date":date_match.group(1) if date_match else "","time":time_match.group(1) if time_match else "","url":href,**analysis})
                if len(rows)>=limit: break
            if rows: break
        except Exception as exc: last_error=str(exc)
    payload={"ok":bool(rows),"updated":datetime.now().strftime("%d.%m.%Y %H:%M"),"timestamp":now,"count":len(rows),"rows":rows,"source":"KAP","error":last_error if not rows else "","disclaimer":"Etki skoru başlık ve özet metnindeki açıklanabilir anahtar kelimelerden üretilir; yatırım tavsiyesi değildir."}
    if rows:
        try: KAP_CACHE_FILE.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
        except Exception: pass
    elif KAP_CACHE_FILE.exists():
        try:
            cached=json.loads(KAP_CACHE_FILE.read_text(encoding="utf-8")); cached["stale"]=True; cached["error"]=last_error; return cached
        except Exception: pass
    return payload

def parse_ai_builder_prompt(prompt: str) -> dict:
    import re
    raw=(prompt or '').strip()
    text=raw.casefold().replace('ı','i').replace('ş','s').replace('ğ','g').replace('ü','u').replace('ö','o').replace('ç','c')
    conditions={k:False for k in ['breakout','rsi','volume','ema','supertrend','moneyFlow','squeeze','rs','macd','atr','divergence']}
    thresholds={'donchian':20,'rsiMin':55.0,'volumeMin':1.5,'moneyFlowMin':55.0,'squeezeMin':60.0,'rsMin':0.0,'atrMin':1.0}
    notes=[]
    def number_near(words, default, patterns=None):
        pats=patterns or []
        for pat in pats:
            m=re.search(pat,text)
            if m:
                try:return float(m.group(1).replace(',','.'))
                except:pass
        for w in words:
            m=re.search(rf'{w}[^0-9]{{0,18}}([0-9]+(?:[.,][0-9]+)?)',text)
            if m:
                try:return float(m.group(1).replace(',','.'))
                except:pass
        return default
    if any(x in text for x in ['breakout','kirilim','zirvesini kir','direnc kir','donchian']): conditions['breakout']=True
    if 'rsi' in text: conditions['rsi']=True; thresholds['rsiMin']=number_near(['rsi'],55)
    if any(x in text for x in ['hacim','volume']):
        conditions['volume']=True
        thresholds['volumeMin']=number_near(['hacim','volume'],1.5,[r'hacim[^0-9]{0,18}([0-9]+(?:[.,][0-9]+)?)\s*(?:kat|x)',r'([0-9]+(?:[.,][0-9]+)?)\s*(?:kat|x)[^\n]{0,12}hacim'])
    if any(x in text for x in ['ema','hareketli ortalama']): conditions['ema']=True
    if 'supertrend' in text: conditions['supertrend']=True
    if any(x in text for x in ['para akisi','para girisi','kurumsal birikim','cmf','obv']): conditions['moneyFlow']=True; thresholds['moneyFlowMin']=number_near(['para akisi','para girisi','birikim'],55)
    if any(x in text for x in ['sikisma','daralma','vcp','bollinger dar']): conditions['squeeze']=True; thresholds['squeezeMin']=number_near(['sikisma','daralma'],60)
    if any(x in text for x in ['relative strength','goreceli guc',' rs ','xu100\'e gore','xu100 e gore']): conditions['rs']=True; thresholds['rsMin']=number_near(['rs','goreceli guc'],0)
    if 'macd' in text: conditions['macd']=True
    if any(x in text for x in ['atr','volatilite genisleme']): conditions['atr']=True; thresholds['atrMin']=number_near(['atr'],1.0)
    if any(x in text for x in ['pozitif uyumsuzluk','bullish divergence','uyumsuzluk']): conditions['divergence']=True
    d=number_near(['son'],20,[r'son\s+([0-9]+)\s*(?:gun|bar|mum)',r'donchian[^0-9]{0,10}([0-9]+)'])
    if conditions['breakout']: thresholds['donchian']=int(max(5,min(250,d)))
    intent='backtest' if any(x in text for x in ['backtest','gecmiste test','test et']) else 'scan'
    active=[k for k,v in conditions.items() if v]
    if not active: notes.append('Desteklenen teknik koşul bulunamadı. RSI, hacim, EMA, breakout, SuperTrend, para akışı, sıkışma veya RS ifadelerinden birini kullan.')
    if 'ema50' in text or 'ema 50' in text: notes.append('Mevcut motor EMA koşulunu EMA20 > EMA50 > EMA200 trend uyumu olarak uygular.')
    if 'altinda' in text or 'dusuk' in text: notes.append('Bu beta sürüm ağırlıklı olarak minimum/pozitif koşulları destekler; ters yönlü koşullar henüz eklenmedi.')
    labels={'breakout':'Breakout','rsi':'RSI','volume':'Hacim','ema':'EMA Trend','supertrend':'SuperTrend','moneyFlow':'Para Akışı','squeeze':'Sıkışma','rs':'RS','macd':'MACD','atr':'ATR','divergence':'Pozitif Uyumsuzluk'}
    return {'ok':bool(active),'prompt':raw,'intent':intent,'conditions':conditions,'thresholds':thresholds,'strategyName':' + '.join(labels[k] for k in active) or 'Tanımsız Strateji','notes':notes}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(WEB),**kwargs)
    def do_GET(self):
        parsed=urlparse(self.path); query=parse_qs(parsed.query)
        if parsed.path=="/api/health":
            deps={}
            for mod in ("pandas","yfinance","openpyxl","requests","bs4","lxml"):
                try:
                    __import__(mod); deps[mod]=True
                except Exception: deps[mod]=False
            try:
                master_count=len(json.loads(MASTER_ALL_PATH.read_text(encoding="utf-8")).get("symbols",[])) if MASTER_ALL_PATH.exists() else 0
            except Exception: master_count=0
            return self._json({"ok":all(deps.values()),"version":"2.9.5-beta","dependencies":deps,"masterSymbols":master_count})
        if parsed.path=="/api/kap/notifications": return self._json(fetch_kap_notifications(force=query.get("force",["0"])[0]=="1",limit=int(query.get("limit",["80"])[0])))
        if parsed.path=="/api/ai-builder/parse": return self._json(parse_ai_builder_prompt(query.get("prompt",[""])[0]))
        if parsed.path=="/api/market-cards": return self._json(load_market_cards(force=query.get("force",["0"])[0]=="1"))
        if parsed.path=="/api/dashboard-scan": return self._json(load_scan_dashboard())
        if parsed.path=="/api/dashboard": return self._json(load_market_dashboard(force=query.get("force",["0"])[0]=="1"))
        if parsed.path=="/api/scan/start":
            enabled_keys=["breakout","volumeSpike","bollingerSqueeze","emaTrend","rsiPositive","macdBullish","atrExpansion","supertrendBuy","positiveDivergence"]
            modes={k: query.get(k,["score"])[0] for k in enabled_keys}
            modes={k:(v if v in {"off","score","required"} else "score") for k,v in modes.items()}
            config={"modes":modes,"donchianLength":int(query.get("donchianLength",["20"])[0]),"volumeSpike":float(query.get("volumeSpikeValue",["1.5"])[0]),"atrRatio":float(query.get("atrRatio",["1.0"])[0]),"squeezeFactor":float(query.get("squeezeFactor",["0.70"])[0])}
            return self._json(start_scan(query.get("universe",["30"])[0],config))
        if parsed.path=="/api/scan/status":
            job_id=query.get("job",[""])[0]
            with JOBS_LOCK: job=JOBS.get(job_id)
            return self._json(job or {"state":"error","message":"Tarama işi bulunamadı."})
        if parsed.path=="/api/backtest/start":
            def qbool(name, default=False):
                raw=query.get(name,["true" if default else "false"])[0]
                return str(raw).strip().lower() in {"1","true","yes","on"}
            cfg={"period":query.get("period",["10y"])[0],"donchian":int(query.get("donchian",["20"])[0]),"rsiMin":float(query.get("rsiMin",["55"])[0]),"volumeMin":float(query.get("volumeMin",["1.5"])[0]),"moneyFlowMin":float(query.get("moneyFlowMin",["55"])[0]),"squeezeMin":float(query.get("squeezeMin",["60"])[0]),"rsMin":float(query.get("rsMin",["0"])[0]),"targetPct":float(query.get("targetPct",["5"])[0]),"stopPct":float(query.get("stopPct",["3"])[0]),"holdingDays":int(query.get("holdingDays",["10"])[0]),"maxSymbols":int(query.get("maxSymbols",["100"])[0]),"useBreakout":qbool("useBreakout",True),"useRsi":qbool("useRsi",True),"useVolume":qbool("useVolume",True),"useEma":qbool("useEma"),"useSupertrend":qbool("useSupertrend"),"useMoneyFlow":qbool("useMoneyFlow"),"useSqueeze":qbool("useSqueeze"),"useRs":qbool("useRs")}
            return self._json(start_backtest(query.get("universe",["30"])[0],cfg))
        if parsed.path=="/api/backtest/status":
            job_id=query.get("job",[""])[0]
            with BACKTEST_LOCK: job=BACKTEST_JOBS.get(job_id)
            return self._json(job or {"state":"error","message":"Backtest işi bulunamadı."})
        if parsed.path=="/api/backtest/last": return self._json(load_last_backtest())
        if parsed.path=="/api/last-scan": return self._json(load_last_scan())
        if parsed.path=="/api/export.csv": return self._csv(load_last_scan().get("rows",[]))
        if parsed.path=="/api/export.xlsx":
            try: return self._binary(xlsx_bytes(load_last_scan().get("rows",[])),"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","bist-tarama-sonuclari.xlsx")
            except Exception as exc: return self._json({"error":str(exc)},status=500)
        return super().do_GET()
    def _json(self,payload,status=200):
        body=json.dumps(_json_safe(payload),ensure_ascii=False,allow_nan=False).encode("utf-8"); self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def _csv(self,rows):
        out=io.StringIO(); writer=csv.writer(out,delimiter=';'); writer.writerow(["Hisse","Şirket","Fiyat","Değişim %","Hacim Oranı","RSI","TR/ATR","Kırılım %","Skor","Durum","Veri Tarihi"])
        for r in rows: writer.writerow([r.get("symbol"),r.get("name"),r.get("close"),r.get("changePct"),r.get("volumeRatio"),r.get("rsi"),r.get("trAtr"),r.get("breakoutPct"),r.get("score"),r.get("setup"),r.get("dataDate")])
        body=('\ufeff'+out.getvalue()).encode('utf-8'); self._binary(body,'text/csv; charset=utf-8','bist-tarama-sonuclari.csv')
    def _binary(self,body,ctype,filename):
        self.send_response(200); self.send_header("Content-Type",ctype); self.send_header("Content-Disposition",f'attachment; filename="{filename}"'); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def end_headers(self):
        if self.path.endswith((".js",".css",".html","/")): self.send_header("Cache-Control","no-store, no-cache, must-revalidate")
        super().end_headers()
    def log_message(self,fmt,*args): pass

def _local_ip():
    try:
        sock=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip=sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"

def main():
    host=os.environ.get("BIST_HOST", "127.0.0.1")
    port=int(os.environ.get("BIST_PORT", "8765"))
    try: server=ThreadingHTTPServer((host,port),Handler)
    except OSError:
        print(f"{port} numaralı port kullanımda. Önce eski BIST Scanner penceresini kapatın."); input("Çıkmak için Enter..."); return
    local_url=f"http://127.0.0.1:{port}"
    mobile_url=f"http://{_local_ip()}:{port}"
    if host in ("127.0.0.1", "localhost"):
        threading.Timer(1,lambda:webbrowser.open(local_url)).start()
    print(f"BIST Scanner Pro Android Beta çalışıyor: {local_url}")
    if host == "0.0.0.0":
        print(f"Telefon bağlantısı: {mobile_url}")
        print("Telefon ve bilgisayar aynı Wi-Fi ağında olmalıdır.")
    print("Kapatmak için bu pencereyi kapatın veya Ctrl+C basın.")
    try: server.serve_forever()
    except KeyboardInterrupt: pass

if __name__=="__main__": main()
