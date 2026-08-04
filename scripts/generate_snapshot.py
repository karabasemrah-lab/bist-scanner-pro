from __future__ import annotations
import json, time, uuid
from pathlib import Path
import server

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'; DATA.mkdir(exist_ok=True)

def write(name,payload):
    (DATA/name).write_text(json.dumps(server._json_safe(payload),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

# BIST Tüm günlük tarama (tek işlem; esas workflow parçalı çalışır)
job_id=uuid.uuid4().hex
symbols,_=server.symbols_for_universe('all')
server.JOBS[job_id]={'id':job_id,'state':'running','total':len(symbols),'processed':0,'percent':0,'found':0,'failed':0,'message':'Cloud tarama'}
server.run_scan_job(job_id,'all',{'donchianLength':20,'volumeSpikeValue':1.5,'atrRatio':1.0,'squeezeFactor':0.70})
scan=server.load_last_scan(); write('last_scan.json',scan)
write('dashboard.json',server.load_scan_dashboard())
try: write('market_cards.json',server.load_market_cards(force=True))
except Exception as exc: write('market_cards.json',{'cards':[],'updatedAt':time.strftime('%Y-%m-%d %H:%M:%S'),'warning':str(exc)})
try: write('kap_notifications.json',server.fetch_kap_notifications(force=True,limit=100))
except Exception as exc: write('kap_notifications.json',{'rows':[],'updatedAt':time.strftime('%Y-%m-%d %H:%M:%S'),'warning':str(exc)})
write('last_backtest.json',server.load_last_backtest())
print('Snapshot hazır:',scan.get('updatedAt'),len(scan.get('rows',[])),'hisse')
