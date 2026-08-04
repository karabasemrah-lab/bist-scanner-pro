(() => {
  const nativeFetch = window.fetch.bind(window);
  const jsonResponse = (data, status=200) => new Response(JSON.stringify(data), {status, headers:{'Content-Type':'application/json; charset=utf-8'}});
  const loadJson = async (path, fallback={}) => {
    try {
      const r = await nativeFetch(path + (path.includes('?')?'&':'?') + 'v=' + Date.now(), {cache:'no-store'});
      if (!r.ok) return fallback;
      return await r.json();
    } catch (_) { return fallback; }
  };
  const parsePrompt = (prompt='') => {
    const t = decodeURIComponent(prompt).toLocaleLowerCase('tr-TR');
    const conditions=[]; const thresholds={};
    if(/breakout|kırılım|zirve/.test(t)) conditions.push('breakout');
    if(/rsi/.test(t)){conditions.push('rsi'); const m=t.match(/rsi\D{0,12}(\d{2})/); thresholds.rsi=m?+m[1]:55;}
    if(/hacim/.test(t)){conditions.push('volume'); const m=t.match(/hacim\D{0,12}(\d+(?:[.,]\d+)?)/); thresholds.volume=m?+m[1].replace(',','.') : 1.5;}
    if(/ema|trend/.test(t)) conditions.push('emaTrend');
    if(/supertrend/.test(t)) conditions.push('supertrend');
    if(/para akışı|para girişi|birikim/.test(t)) conditions.push('moneyFlow');
    if(/sıkış/.test(t)) conditions.push('squeeze');
    if(/relative strength|\brs\b/.test(t)) conditions.push('rs');
    return {prompt:decodeURIComponent(prompt), conditions:[...new Set(conditions)], thresholds, explanation:'Komut çevrim içi statik kurallarla çözümlendi.'};
  };
  window.__BIST_CLOUD__ = true;
  window.fetch = async (input, init={}) => {
    const raw = typeof input === 'string' ? input : input.url;
    const u = new URL(raw, location.href);
    if (!u.pathname.includes('/api/')) return nativeFetch(input, init);
    const path = u.pathname;
    if (path.endsWith('/api/health')) return jsonResponse({ok:true, mode:'github-pages', message:'Çevrim içi günlük snapshot modu'});
    if (path.endsWith('/api/last-scan')) return jsonResponse(await loadJson('data/last_scan.json',{rows:[],updatedAt:null,warning:'İlk otomatik tarama henüz oluşmadı.'}));
    if (path.endsWith('/api/market-cards')) return jsonResponse(await loadJson('data/market_cards.json',{cards:[],updatedAt:null,warning:'Piyasa kartları güncellenmeyi bekliyor.'}));
    if (path.endsWith('/api/dashboard-scan')) return jsonResponse(await loadJson('data/dashboard.json',{hasScan:false,health:{},breadth:{},marketLists:{},updatedAt:null}));
    if (path.endsWith('/api/backtest/last')) return jsonResponse(await loadJson('data/last_backtest.json',{summary:null,symbols:[],updatedAt:null}));
    if (path.endsWith('/api/kap/notifications')) return jsonResponse(await loadJson('data/kap_notifications.json',{rows:[],updatedAt:null,warning:'KAP snapshot henüz oluşmadı.'}));
    if (path.endsWith('/api/ai-builder/parse')) return jsonResponse(parsePrompt(u.searchParams.get('prompt')||''));
    if (path.endsWith('/api/scan/start')) return jsonResponse({id:'cloud-snapshot',state:'done',percent:100,message:'GitHub Pages sürümü son otomatik taramayı kullanır.'});
    if (path.endsWith('/api/scan/status')) {
      const result=await loadJson('data/last_scan.json',{rows:[],updatedAt:null});
      return jsonResponse({id:'cloud-snapshot',state:'done',percent:100,message:'Günlük snapshot yüklendi.',result});
    }
    if (path.endsWith('/api/backtest/start')) return jsonResponse({id:'cloud-backtest',state:'done',percent:100,message:'GitHub Pages sürümünde son hazırlanmış backtest gösterilir.'});
    if (path.endsWith('/api/backtest/status')) {
      const result=await loadJson('data/last_backtest.json',{summary:null,symbols:[],updatedAt:null});
      return jsonResponse({id:'cloud-backtest',state:'done',percent:100,message:'Son backtest yüklendi.',result});
    }
    if (path.endsWith('/api/export.csv')) {
      const scan=await loadJson('data/last_scan.json',{rows:[]});
      const headers=['symbol','name','close','changePct','volumeRatio','rsi','score','compositeScore'];
      const csv=[headers.join(','),...(scan.rows||[]).map(r=>headers.map(h=>JSON.stringify(r[h]??'')).join(','))].join('\n');
      return new Response(csv,{status:200,headers:{'Content-Type':'text/csv; charset=utf-8','Content-Disposition':'attachment; filename="bist-scan.csv"'}});
    }
    return jsonResponse({error:'Bu işlem statik GitHub Pages sürümünde desteklenmiyor.'},501);
  };
})();
