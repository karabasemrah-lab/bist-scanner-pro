(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  async function getJson(url){
    const response = await fetch(url, {cache:'no-store'});
    const text = await response.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; }
    catch { throw new Error(`Sunucu geçersiz yanıt verdi (HTTP ${response.status}).`); }
    if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
    return data;
  }
  function statusBox(message, kind='info'){
    let box = $('globalRepairStatus');
    if(!box){
      box=document.createElement('div'); box.id='globalRepairStatus';
      box.style.cssText='position:fixed;right:18px;bottom:18px;z-index:99999;max-width:520px;padding:13px 16px;border-radius:12px;background:#17233f;color:#fff;border:1px solid #365082;box-shadow:0 12px 35px #0008;font:14px system-ui;display:none';
      document.body.appendChild(box);
    }
    box.style.display='block'; box.style.borderColor=kind==='error'?'#ff5d73':'#365082'; box.innerHTML=message;
    clearTimeout(box._timer); box._timer=setTimeout(()=>box.style.display='none',9000);
  }
  window.addEventListener('error', e => statusBox(`JavaScript hatası: ${esc(e.message)}<br><small>${esc(e.filename)}:${e.lineno}</small>`, 'error'));
  window.addEventListener('unhandledrejection', e => statusBox(`İşlem hatası: ${esc(e.reason?.message || e.reason || 'Bilinmeyen hata')}`, 'error'));

  async function health(){
    const d=await getJson('/api/health?_='+Date.now());
    const missing=Object.entries(d.dependencies||{}).filter(([,ok])=>!ok).map(([name])=>name);
    if(missing.length) throw new Error(`Eksik Python paketi: ${missing.join(', ')}. CANLI_VERI_KUR.bat dosyasını çalıştır.`);
    return d;
  }
  async function ensureScan(target){
    await health();
    let last=await getJson('/api/last-scan?_='+Date.now());
    if(Array.isArray(last.rows)&&last.rows.length){
      if(typeof window.__bistSetRows==='function') window.__bistSetRows(last.rows);
      return last.rows;
    }
    if(target) target.innerHTML='<div class="empty">BIST 100 taraması otomatik başlatılıyor…</div>';
    const job=await getJson('/api/scan/start?universe=100&donchianLength=20&volumeSpikeValue=1.5&atrRatio=1&squeezeFactor=.70&_='+Date.now());
    if(!job.id) throw new Error(job.message||'Tarama başlatılamadı.');
    while(true){
      await sleep(900);
      const st=await getJson('/api/scan/status?job='+encodeURIComponent(job.id)+'&_='+Date.now());
      if(target) target.innerHTML=`<div class="empty">Tarama: %${st.percent||0} · ${st.processed||0}/${st.total||0}</div>`;
      if(st.state==='error') throw new Error(st.message||'Tarama hatası');
      if(st.state==='done'){
        const rows=st.result?.rows||[];
        if(!rows.length) throw new Error(st.result?.warning||'Tarama tamamlandı ancak geçerli veri bulunamadı.');
        if(typeof window.__bistSetRows==='function') window.__bistSetRows(rows);
        if(typeof window.render==='function') window.render();
        return rows;
      }
    }
  }

  async function builder(){
    const prompt=$('builderPrompt')?.value.trim(); if(!prompt){statusBox('Önce stratejini yaz.','error');return;}
    const out=$('builderResult'), stat=$('builderStatus');
    if(stat){stat.classList.remove('hidden');stat.textContent='Komut çözümleniyor…';}
    try{
      const d=await getJson('/api/ai-builder/parse?prompt='+encodeURIComponent(prompt)+'&_='+Date.now());
      if(typeof window.renderBuilderResult==='function') window.renderBuilderResult(d);
      else if(out) out.innerHTML=`<div class="result-card"><h3>${esc(d.strategyName||'Strateji')}</h3><p>${Object.entries(d.conditions||{}).filter(([,v])=>v).map(([k])=>esc(k)).join(' · ')||'Koşul bulunamadı'}</p></div>`;
      if(stat) stat.textContent='Komut başarıyla çözümlendi.';
    }catch(e){if(stat)stat.textContent='Hata: '+e.message;statusBox('AI Builder: '+esc(e.message),'error');}
  }

  async function backtest(){
    const btn=$('runBacktest'), msg=$('btMessage');
    try{
      await health(); if(btn){btn.disabled=true;btn.textContent='Backtest sürüyor…';}
      if(msg){msg.textContent='Backtest başlatılıyor…';$('btProgress')?.classList.remove('hidden');}
      const val=(id,def)=>$(id)?.value||def, chk=id=>$(id)?.checked?'true':'false';
      const q=new URLSearchParams({universe:val('btUniverse','30'),period:val('btPeriod','5y'),donchian:val('btDonchian','20'),rsiMin:val('btRsi','55'),volumeMin:val('btVolume','1.5'),moneyFlowMin:val('btMoneyFlow','55'),squeezeMin:val('btSqueeze','60'),rsMin:val('btRs','0'),targetPct:val('btTarget','5'),stopPct:val('btStop','3'),holdingDays:val('btHolding','10'),maxSymbols:val('btMaxSymbols','30'),useBreakout:chk('btUseBreakout'),useRsi:chk('btUseRsi'),useVolume:chk('btUseVolume'),useEma:chk('btUseEma'),useSupertrend:chk('btUseSupertrend'),useMoneyFlow:chk('btUseMoneyFlow'),useSqueeze:chk('btUseSqueeze'),useRs:chk('btUseRs'),_:Date.now()});
      const job=await getJson('/api/backtest/start?'+q); if(!job.id)throw new Error(job.message||'Backtest başlatılamadı.');
      while(true){await sleep(900);const st=await getJson('/api/backtest/status?job='+encodeURIComponent(job.id)+'&_='+Date.now());if(msg)msg.textContent=st.message||`Backtest %${st.percent||0}`;if($('btPercent'))$('btPercent').textContent=`${st.percent||0}%`;if($('btBar'))$('btBar').style.width=`${st.percent||0}%`;if(st.state==='error')throw new Error(st.message||'Backtest hatası');if(st.state==='done'){if(typeof window.renderBacktest==='function')window.renderBacktest(st.result);statusBox('Backtest tamamlandı.');break;}}
    }catch(e){statusBox('Backtest: '+esc(e.message),'error');if(msg)msg.textContent='Hata: '+e.message;}finally{if(btn){btn.disabled=false;btn.textContent='Backtest Başlat';}}
  }

  async function kap(){
    const list=$('kapList'); if(list)list.innerHTML='<div class="empty">KAP bildirimleri getiriliyor…</div>';
    try{
      const d=await getJson('/api/kap/notifications?limit=100&force=1&_='+Date.now());
      if(typeof window.__bistSetKapRows==='function')window.__bistSetKapRows(d.rows||[]);
      if(typeof window.renderKap==='function')window.renderKap();
      if(!d.ok && list) list.innerHTML=`<div class="error-box">KAP sitesi veri erişimini reddetti veya bağlantı kurulamadı: ${esc(d.error||'Bilinmeyen hata')}<br><a href="https://www.kap.org.tr/tr/bildirim-sorgu" target="_blank">Resmî KAP sayfasını aç ↗</a></div>`;
      else if(list && !(d.rows||[]).length) list.innerHTML='<div class="empty">Bu sorguda bildirim bulunamadı.</div>';
    }catch(e){if(list)list.innerHTML=`<div class="error-box">KAP AI hatası: ${esc(e.message)}<br><a href="https://www.kap.org.tr/tr/bildirim-sorgu" target="_blank">Resmî KAP sayfasını aç ↗</a></div>`;}
  }

  async function screener(){
    const prompt=$('screenerAiPrompt')?.value.trim(); if(!prompt){statusBox('Screener AI komut kutusuna ne aradığını yaz.','error');return;}
    const target=$('screenerAiResults');
    try{await ensureScan(target);if(typeof window.runScreenerAi==='function')await window.runScreenerAi();else throw new Error('Screener AI fonksiyonu yüklenemedi.');}
    catch(e){if(target)target.innerHTML=`<div class="error-box">Screener AI: ${esc(e.message)}</div>`;statusBox('Screener AI: '+esc(e.message),'error');}
  }

  async function decision(){
    const target=$('decisionOpportunities');
    try{await ensureScan(target);if(typeof window.renderDecisionCenter==='function')window.renderDecisionCenter();else throw new Error('Decision Center fonksiyonu yüklenemedi.');}
    catch(e){if(target)target.innerHTML=`<div class="error-box">Decision Center: ${esc(e.message)}</div>`;statusBox('Decision Center: '+esc(e.message),'error');}
  }

  function bind(id, fn){const el=$(id);if(!el)return;el.addEventListener('click',e=>{e.preventDefault();e.stopImmediatePropagation();fn();},true);}
  document.addEventListener('DOMContentLoaded',()=>{
    bind('parseBuilder',builder); bind('runBacktest',backtest); bind('refreshKap',kap); bind('runScreenerAi',screener); bind('refreshDecision',decision);
    document.querySelectorAll('[data-ai-prompt]').forEach(el=>el.addEventListener('click',e=>{e.preventDefault();e.stopImmediatePropagation();if($('screenerAiPrompt'))$('screenerAiPrompt').value=el.dataset.aiPrompt||'';screener();},true));
    health().then(d=>statusBox(`Sistem hazır · v${esc(d.version)} · ${d.masterSymbols||0} BIST Tüm sembolü`)).catch(e=>statusBox(`Başlangıç kontrolü: ${esc(e.message)}`,'error'));
  });
})();
