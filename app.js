let allRows=[];
let activeJob=null;
let failedSymbols=[];
let insufficientHistorySymbols=[];
const $=id=>document.getElementById(id);
function safeJsonParse(text,fallback){try{return JSON.parse(text)}catch(_){return fallback}}
function storageGet(key,fallback){try{return safeJsonParse(localStorage.getItem(key)||'',fallback)}catch(_){return fallback}}
function storageSet(key,value){try{localStorage.setItem(key,JSON.stringify(value));return true}catch(_){return false}}
async function fetchJson(url,options={}){const res=await fetch(url,options);const text=await res.text();let data;try{data=text?JSON.parse(text):{}}catch(_){throw new Error(`Sunucudan geçersiz yanıt alındı (${res.status}).`)}if(!res.ok)throw new Error(data.message||data.error||`HTTP ${res.status}`);return data}
const watchlist=new Set(safeJsonParse(localStorage.getItem('bistScannerWatchlist')||'[]',[]));
const titles={dashboard:'Dashboard',scanner:'Tarama',watchlist:'Watchlist',breakout:'Breakout',minervini:'Minervini',relative:'Relative Strength',ai:'AI Breakout',backtest:'Backtest',builder:'AI Builder',alarms:'Alarm Merkezi',portfolio:'Portföy',kap:'KAP AI',screenerai:'Screener AI',decision:'AI Decision Center',settings:'Ayarlar'};
const filterIds=['fBreakout','fVolume','fBollinger','fEma','fRsi','fMacd','fAtr','fSupertrend','fDivergence'];
const settingIds=['universe','minScore','minVol','setup','donchianLength','fMoneyFlow','volumeSpikeValue','atrRatio','squeezeFactor',...filterIds];
const columns=['star','symbol','name','close','changePct','volumeRatio','rsi','trAtr','breakoutPct','breakoutScore','squeezeScore','moneyFlowScore','rsScore','sector','aiBreakoutProbability','aiRiskScore','compositeScore','support1','resistance1','score','setup'];
const defaultMarketCards=['XU100','XU030','XBANK','XUSIN','USDTRY','EURTRY','XAUUSD','BTCUSD','BRENT'];
let marketCardCatalog=[];
let visibleMarketCards=new Set(safeJsonParse(localStorage.getItem('bistVisibleMarketCards')||'null',null)||defaultMarketCards);
let visibleColumns=new Set(safeJsonParse(localStorage.getItem('bistVisibleColumns')||JSON.stringify(columns),columns));
const presets={loose:{minScore:35,minVol:.7,fMoneyFlow:'score',fBreakout:'score',fVolume:'score',fBollinger:'score',fEma:'score',fRsi:'score',fMacd:'score',fAtr:'off',fSupertrend:'score',fDivergence:'score'},normal:{minScore:55,minVol:1,fBreakout:'score',fMoneyFlow:'score',fVolume:'required',fBollinger:'score',fEma:'required',fRsi:'score',fMacd:'score',fAtr:'score',fSupertrend:'score',fDivergence:'score'},strict:{minScore:70,minVol:1.5,fMoneyFlow:'score',fBreakout:'required',fVolume:'required',fBollinger:'off',fEma:'required',fRsi:'required',fMacd:'required',fAtr:'required',fSupertrend:'required',fDivergence:'off'}};
function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}
function n(v,d=2){const x=Number(v);return Number.isFinite(x)?x.toFixed(d):'-'}
function loadSettings(){const s=safeJsonParse(localStorage.getItem('bistScannerSettings')||'{}',{});settingIds.forEach(k=>{if(s[k]===undefined||!$(k))return;$(k).value=s[k]})}
function saveSettings(){const s={};settingIds.forEach(k=>{if(!$(k))return;s[k]=$(k).value});localStorage.setItem('bistScannerSettings',JSON.stringify(s));$('save').textContent='Kaydedildi';setTimeout(()=>$('save').textContent='Ayarları Kaydet',1000)}
function persistWatch(){localStorage.setItem('bistScannerWatchlist',JSON.stringify([...watchlist]));$('watchCount').textContent=watchlist.size}
function toggleWatch(symbol,event){event?.stopPropagation();watchlist.has(symbol)?watchlist.delete(symbol):watchlist.add(symbol);persistWatch();render();renderWatch()}
window.toggleWatch=toggleWatch;
function cell(col,html){return visibleColumns.has(col)?`<td data-col="${col}">${html}</td>`:''}
function rowHtml(r,full=true){return `<tr onclick="showDetail('${esc(r.symbol)}')">${cell('star',`<button class="star ${watchlist.has(r.symbol)?'on':''}" onclick="toggleWatch('${esc(r.symbol)}',event)">★</button>`)}${cell('symbol',`<b>${esc(r.symbol)}</b>`)}${full?cell('name',esc(r.name)):''}${cell('close',n(r.close))}${cell('changePct',`<span class="${r.changePct>=0?'positive':'negative'}">${n(r.changePct)}</span>`)}${cell('volumeRatio',n(r.volumeRatio))}${full?cell('rsi',n(r.rsi,1))+cell('trAtr',n(r.trAtr))+cell('breakoutPct',`<span class="${r.breakoutPct>=0?'positive':'negative'}">${n(r.breakoutPct)}</span>`)+cell('breakoutScore',`<span class="score">${r.breakoutScore??'-'}</span>`)+cell('squeezeScore',`<span class="score">${r.squeezeScore??'-'}</span>`)+cell('moneyFlowScore',`<span class="score">${r.moneyFlowScore??'-'}</span>`)+cell('rsScore',`<span class="score rs-score">${r.rsScore??'-'}</span>`)+cell('sector',`<span class="sector-chip">${esc(r.sector||'Diğer')}</span>`)+cell('aiBreakoutProbability',`<span class="score ai-score">%${r.aiBreakoutProbability??'-'}</span>`)+cell('aiRiskScore',`<span class="score">${r.aiRiskScore??'-'}</span>`)+cell('compositeScore',`<span class="score composite">${r.compositeScore??r.score??'-'}</span>`)+cell('support1',n(r.support1))+cell('resistance1',n(r.resistance1)):''}${cell('score',`<span class="score">${r.score}</span>`)}${cell('setup',`<span class="pill">${esc(r.setup)}</span>`)}</tr>`}
function filteredRows(){
  const minScore = +$('minScore').value;
  const minVol = +$('minVol').value;
  const setup = $('setup').value;
  const q = $('search').value.trim().toLocaleLowerCase('tr');

  const filterMap = {
    fBreakout: 'breakout',
    fVolume: 'volumeSpike',
    fBollinger: 'bollingerSqueeze',
    fEma: 'emaTrend',
    fRsi: 'rsiPositive',
    fMacd: 'macdBullish',
    fAtr: 'atrExpansion',
    fSupertrend: 'supertrendBuy',
    fDivergence: 'positiveDivergence',
    fMoneyFlow: 'moneyFlowPositive'
  };

  return allRows.filter(r => {
    if (r.score < minScore) return false;
    if (r.volumeRatio < minVol) return false;

    if (setup !== 'all' && r.setup !== setup) {
      return false;
    }

    if (
      q &&
      !r.symbol.toLocaleLowerCase('tr').includes(q) &&
      !r.name.toLocaleLowerCase('tr').includes(q)
    ){
      return false;
    }

    const conditions = r.conditions || {};

    for (const [selectId, conditionName] of Object.entries(filterMap)) {
      const mode = $(selectId)?.value;

      if (mode === 'required' && conditions[conditionName] !== true) {
        return false;
      }
    }

    return true;
  });
}
function render(){applyColumnVisibility();const rows=filteredRows();$('rows').innerHTML=rows.map(r=>rowHtml(r,true)).join('');$('count').textContent=rows.length;$('breakouts').textContent=rows.filter(r=>r.setup==='Breakout').length;$('avgScore').textContent=rows.length?Math.round(rows.reduce((a,b)=>a+b.score,0)/rows.length):0;$('positiveCount').textContent=rows.filter(r=>r.changePct>0).length;$('resultLabel').textContent=`${rows.length} hisse`;renderDashboard(rows);renderIntelligence(allRows);renderWatch();renderHealthFromRows(allRows);renderPortfolio();renderDecisionCenter()}
function renderDashboard(rows){const top=[...rows].sort((a,b)=>b.score-a.score).slice(0,7);$('topRows').innerHTML=top.map(r=>`<tr onclick="showDetail('${esc(r.symbol)}')"><td><b>${esc(r.symbol)}</b></td><td class="${r.changePct>=0?'positive':'negative'}">${n(r.changePct)}%</td><td>${n(r.volumeRatio)}x</td><td class="score">${r.score}</td><td><span class="pill">${esc(r.setup)}</span></td><td><button class="star ${watchlist.has(r.symbol)?'on':''}" onclick="toggleWatch('${esc(r.symbol)}',event)">★</button></td></tr>`).join('');const groups=['Breakout','Güçlü Trend','Pozitif Uyumsuzluk','İzleme','Zayıf'];const total=Math.max(rows.length,1);$('distribution').innerHTML=groups.map(g=>{const count=rows.filter(r=>r.setup===g).length;return `<div class="dist-row"><div><span>${g}</span><b>${count}</b></div><div class="bar"><i style="width:${count/total*100}%"></i></div></div>`}).join('')}
function sectorSummary(rows){
  const groups={};(rows||[]).forEach(r=>(groups[r.sector||'Diğer']??=[]).push(r));
  return Object.entries(groups).map(([sector,items])=>{const avg=k=>items.reduce((a,r)=>a+(Number(r[k])||0),0)/items.length;const change=avg('changePct'),rs=avg('rsScore'),money=avg('moneyFlowScore');return {sector,count:items.length,changePct:change,rsScore:rs,moneyFlowScore:money,score:Math.round(rs*.6+Math.max(0,Math.min(100,50+change*8))*.2+money*.2)}}).sort((a,b)=>b.score-a.score);
}
function renderIntelligence(rows){
  rows=Array.isArray(rows)?rows:[];
  const radar=rows.filter(r=>(r.compositeScore??r.score)>=75&&(r.rsScore??0)>=65&&(r.moneyFlowScore??0)>=55).sort((a,b)=>(b.compositeScore??b.score)-(a.compositeScore??a.score)).slice(0,15);
  if($('radarCount'))$('radarCount').textContent=`${radar.length} aday`;
  if($('radarCards'))$('radarCards').innerHTML=radar.length?radar.map(r=>`<button class="radar-card" onclick="showDetail('${esc(r.symbol)}')"><div><b>${esc(r.symbol)}</b><span>${'★'.repeat(r.stars||Math.max(1,Math.round((r.compositeScore||r.score)/20)))}</span></div><strong>${r.compositeScore??r.score}</strong><small>RS ${r.rsScore??'-'} · Para ${r.moneyFlowScore??'-'} · ${esc(r.sector||'Diğer')}</small></button>`).join(''):'<div class="empty">Radar koşullarına uyan aday yok.</div>';
  const sectors=sectorSummary(rows);
  if($('sectorLeaders'))$('sectorLeaders').innerHTML=sectors.slice(0,8).map((x,i)=>`<div class="sector-row"><b>${i+1}</b><span>${esc(x.sector)}<small>${x.count} hisse · ${x.changePct>=0?'+':''}${n(x.changePct)}%</small></span><strong>${x.score}</strong></div>`).join('')||'<div class="empty">Tarama bekleniyor</div>';
  if($('rsLeaders'))$('rsLeaders').innerHTML=[...rows].sort((a,b)=>(b.rsScore||0)-(a.rsScore||0)).slice(0,30).map((r,i)=>`<div class="rank-row" onclick="showDetail('${esc(r.symbol)}')"><b>${i+1}</b><span>${esc(r.symbol)}<small>${esc(r.name||'')} · ${esc(r.sector||'Diğer')}</small></span><em>${r.rsScore??'-'}</em><i>${esc(r.rsTrend||'')}</i></div>`).join('')||'<div class="empty">Tarama bekleniyor</div>';
  if($('sectorRanking'))$('sectorRanking').innerHTML=sectors.map((x,i)=>`<div class="rank-row"><b>${i+1}</b><span>${esc(x.sector)}<small>${x.count} hisse · RS ${Math.round(x.rsScore)}</small></span><em>${x.score}</em><i>${x.changePct>=0?'+':''}${n(x.changePct)}%</i></div>`).join('')||'<div class="empty">Tarama bekleniyor</div>';
  if($('aiLeaders')){const ai=[...rows].sort((a,b)=>(b.aiBreakoutProbability??0)-(a.aiBreakoutProbability??0)).slice(0,25);$('aiLeaders').innerHTML=ai.map(r=>`<tr onclick="showDetail('${esc(r.symbol)}')"><td><b>${esc(r.symbol)}</b></td><td><span class="score ai-score">%${r.aiBreakoutProbability??'-'}</span></td><td>${r.aiRiskScore??'-'}</td><td class="${(r.aiExpectedReturn10d??0)>=0?'positive':'negative'}">${n(r.aiExpectedReturn10d)}%</td><td>${r.aiModelConfidence??'-'}</td><td>${r.aiSampleSize??0}</td></tr>`).join('')||'<tr><td colspan="6">Tarama bekleniyor</td></tr>';}
}
function renderWatch(){const rows=allRows.filter(r=>watchlist.has(r.symbol));$('watchRows').innerHTML=rows.map(r=>rowHtml(r,false)).join('');$('watchEmpty').classList.toggle('hidden',rows.length>0);$('watchCount').textContent=watchlist.size}
function showView(view){const target=$(`${view}View`);if(!target){console.error('Görünüm bulunamadı:',view);return}document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));target.classList.add('active');document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===view));$('pageTitle').textContent=titles[view]||'BIST Scanner Pro';window.scrollTo({top:0,behavior:'smooth'})}
function setScanState(active){[$('scan'),$('scanTop')].forEach(b=>b.disabled=active);$('scan').textContent=active?'Tarama sürüyor...':'Taramayı Başlat';$('scanTop').textContent=active?'Taranıyor...':'Taramayı Yenile'}
function updateProgress(job){
  console.log("UPDATE PROGRESS JOB:", job);

  const data = job.result || job;

  const total =
    Number(data.total) ||
    Number(data.requested) ||
    Number(job.total) ||
    0;

  const rows = Array.isArray(data.rows)
    ? data.rows
    : [];

  const insufficientCount =
    Array.isArray(data.insufficientHistorySymbols)
      ? data.insufficientHistorySymbols.length
      : Number(data.insufficientHistoryCount || 0);

  const realFailed =
    Array.isArray(data.failedSymbols)
      ? data.failedSymbols.length
      : Number(data.failed || 0);

  const isDone =
    job.state === 'done' ||
    data.state === 'done' ||
    Number(job.percent) >= 100 ||
    Number(data.percent) >= 100;

  const processedRaw =
    Number(data.processed) ||
    Number(data.completed) ||
    Number(job.processed) ||
    0;

  const processed = isDone
    ? total
    : processedRaw;

  const found = rows.length;

  const percent = isDone
    ? 100
    : (
        job.percent ??
        data.percent ??
        (total ? Math.round(processed / total * 100) : 0)
      );

  const remaining = isDone
    ? 0
    : Math.max(0, total - processed);

  $('progressPanel').classList.remove('hidden');

  $('progressMessage').textContent =
    job.message ||
    data.message ||
    (isDone ? 'Tarama tamamlandı.' : 'Tarama sürüyor...');

  $('progressCounts').textContent =
    `${processed} / ${total}`;

  $('progressPercent').textContent =
    `${percent}%`;

  $('progressBar').style.width =
    `${percent}%`;

  $('progressFound').textContent =
    found;

  $('progressFailed').textContent =
    realFailed;

  $('progressRemaining').textContent =
    remaining;
}
function scanParams(){
  const map={
    fBreakout:'breakout',
    fVolume:'volumeSpike',
    fBollinger:'bollingerSqueeze',
    fEma:'emaTrend',
    fRsi:'rsiPositive',
    fMacd:'macdBullish',
    fAtr:'atrExpansion',
    fSupertrend:'supertrendBuy',
    fDivergence:'positiveDivergence',
    fMoneyFlow:'moneyFlowPositive'
  };
const p = new URLSearchParams({
    universe: $('universe').value,
    donchianLength: $('donchianLength').value,
    volumeSpikeValue: $('volumeSpikeValue').value,
    atrRatio: $('atrRatio').value,
    squeezeFactor: $('squeezeFactor').value,
    _: Date.now()
});

Object.entries(map).forEach(([id, key]) => {
    p.set(key, $(id).value);
});

return p.toString();
}
async function scan(){
  if(activeJob)return;

  setScanState(true);
  $('warning').classList.add('hidden');
  $('progressPanel').classList.remove('hidden');

  try{
    const job=await fetchJson(
      `/api/scan/start?${scanParams()}`,
      {cache:'no-store'}
    );

    if(!job||!job.id){
      throw new Error(job?.message||'Tarama işi başlatılamadı.');
    }

    activeJob=job.id;
    updateProgress(job);

    while(activeJob){
      await new Promise(r=>setTimeout(r,700));

      const state=await fetchJson(
        `/api/scan/status?job=${encodeURIComponent(activeJob)}&_=${Date.now()}`,
        {cache:'no-store'}
      );

      updateProgress(state);

      if(state.state==='done'){
        const data=state.result||{};

        allRows=Array.isArray(data.rows)
          ? data.rows
          : [];

        failedSymbols=Array.isArray(data.failedSymbols)
          ? data.failedSymbols
          : [];

        insufficientHistorySymbols=Array.isArray(data.insufficientHistorySymbols)
          ? data.insufficientHistorySymbols
          : [];

        $('updated').textContent=
          data.updatedAt?.slice(11,16)||'-';

        $('warning').textContent=
          data.warning||
          `Tarama tamamlandı: ${allRows.length}/${data.requested||0} hisse. Son fiyat tarihi: ${allRows[0]?.dataDate||'-'}`;

        $('warning').classList.remove('hidden');

        render();
        evaluateAlarms(allRows);
        await loadDashboard(true);

        activeJob=null;

      }else if(state.state==='error'){
        throw new Error(
          state.message||'Tarama tamamlanamadı'
        );
      }
    }

  }catch(e){
    activeJob=null;
    $('warning').textContent=
      `Tarama başlatılamadı: ${e.message}`;
    $('warning').classList.remove('hidden');

  }finally{
  setScanState(false);
}
}

async function loadLast(){
  try{
    const data=await fetchJson('/api/last-scan',{cache:'no-store'});

    if(Array.isArray(data.rows) && data.rows.length){
      allRows=data.rows;

      failedSymbols=Array.isArray(data.failedSymbols)
        ? data.failedSymbols
        : [];

      insufficientHistorySymbols=Array.isArray(data.insufficientHistorySymbols)
        ? data.insufficientHistorySymbols
        : [];

      $('updated').textContent=data.updatedAt?.slice(11,16)||'-';

      render();
    }
  }catch(e){}
}
function yes(v){return v?'✓':'—'}
let detailCurrentIndex=-1;
function showDetail(symbol){const r=allRows.find(x=>x.symbol===symbol);if(!r)return;detailCurrentIndex=allRows.findIndex(x=>x.symbol===symbol);$('detailSymbol').textContent=r.symbol;$('detailName').textContent=r.name;$('detailScore').textContent=r.score;const c=r.conditions||{};$('detailGrid').innerHTML=[['Fiyat',n(r.close)],['Değişim',`${n(r.changePct)}%`],['Teknik skor',r.technicalScore??'-'],['Breakout skoru',r.breakoutScore??'-'],['Sıkışma skoru',r.squeezeScore??'-'],['Composite skor',r.compositeScore??r.score??'-'],['AI breakout olasılığı',`%${r.aiBreakoutProbability??'-'}`],['AI risk skoru',r.aiRiskScore??'-'],['Beklenen 10G getiri',`${n(r.aiExpectedReturn10d)}%`],['Model güveni',r.aiModelConfidence??'-'],['Model örnek sayısı',r.aiSampleSize??0],['Relative Strength',r.rsScore??'-'],['RS trendi',r.rsTrend??'-'],['RS 20G',r.rs20??'-'],['RS 60G',r.rs60??'-'],['RS 120G',r.rs120??'-'],['RS 252G',r.rs252??'-'],['Sektör',r.sector??'Diğer'],['Sektör skoru',r.sectorScore??'-'],['Sektör sırası',r.sectorRank??'-'],['Para akışı skoru',r.moneyFlowScore??'-'],['CMF 20',n(r.cmf20,3)],['OBV eğimi 20G',`${n(r.obvSlope20)}%`],['A/D eğimi 20G',`${n(r.adlSlope20)}%`],['Yükseliş/Düşüş hacmi',`${n(r.upDownVolumeRatio)}x`],['Fiyat-hacim korelasyonu',n(r.priceVolumeCorrelation,3)],['Yakın destek',n(r.support1)],['Desteğe uzaklık',`${n(r.supportDistancePct)}%`],['İkinci destek',n(r.support2)],['Yakın direnç',n(r.resistance1)],['Dirence uzaklık',`${n(r.resistanceDistancePct)}%`],['İkinci direnç',n(r.resistance2)],['Destek temas',r.supportTouches??0],['Direnç temas',r.resistanceTouches??0],['Hacim oranı',`${n(r.volumeRatio)}x`],['RSI',n(r.rsi,1)],['TR / ATR',n(r.trAtr)],['Kırılım',`${n(r.breakoutPct)}%`],['BB genişliği',`${n(r.bbWidth)}%`],['EMA20',n(r.ema20)],['EMA50',n(r.ema50)],['EMA200',n(r.ema200)],['MACD',n(r.macd,3)],['SuperTrend',n(r.supertrend)],['Breakout',yes(c.breakout)],['Hacim patlaması',yes(c.volumeSpike)],['Bollinger sıkışması',yes(c.bollingerSqueeze)],['EMA trend',yes(c.emaTrend)],['MACD pozitif',yes(c.macdBullish)],['SuperTrend AL',yes(c.supertrendBuy)],['Pozitif uyumsuzluk',yes(c.positiveDivergence)],['Para akışı pozitif',yes(c.moneyFlowPositive)],['Durum',r.setup],['Veri tarihi',r.dataDate||'-']].map(([k,v])=>`<div><small>${esc(k)}</small><b>${esc(v)}</b></div>`).join('');if($('detailPosition'))$('detailPosition').textContent=`${detailCurrentIndex+1} / ${allRows.length}`;openModal('detailModal')}
function showAdjacentDetail(delta){if(!allRows.length)return;const next=(detailCurrentIndex+delta+allRows.length)%allRows.length;showDetail(allRows[next].symbol)}
window.showDetail=showDetail;

function applyPreset(name){const p=presets[name];if(!p)return;Object.entries(p).forEach(([k,v])=>{if($(k))$(k).value=v});render()}
function profileData(){const d={};settingIds.forEach(k=>{if($(k))d[k]=$(k).value});return d}
function refreshProfiles(){const all=safeJsonParse(localStorage.getItem('bistProfiles')||'{}',{});$('savedProfiles').innerHTML='<option value="">Seçiniz</option>'+Object.keys(all).map(x=>`<option>${esc(x)}</option>`).join('')}
function saveNamedProfile(){const name=prompt('Profil adı:');if(!name)return;const all=safeJsonParse(localStorage.getItem('bistProfiles')||'{}',{});all[name]=profileData();localStorage.setItem('bistProfiles',JSON.stringify(all));refreshProfiles();$('savedProfiles').value=name}
function loadNamedProfile(name){const all=safeJsonParse(localStorage.getItem('bistProfiles')||'{}',{});const p=all[name];if(!p)return;Object.entries(p).forEach(([k,v])=>{if($(k))$(k).value=v});render()}
function deleteNamedProfile(){const name=$('savedProfiles').value;if(!name)return;const all=safeJsonParse(localStorage.getItem('bistProfiles')||'{}',{});delete all[name];localStorage.setItem('bistProfiles',JSON.stringify(all));refreshProfiles()}
function buildColumnPanel(){$('columnPanel').innerHTML=columns.map(c=>`<label><input type="checkbox" data-column="${c}" ${visibleColumns.has(c)?'checked':''}> ${document.querySelector(`#resultTable th[data-col="${c}"]`)?.textContent||c}</label>`).join('');$('columnPanel').querySelectorAll('input').forEach(i=>i.addEventListener('change',()=>{i.checked?visibleColumns.add(i.dataset.column):visibleColumns.delete(i.dataset.column);localStorage.setItem('bistVisibleColumns',JSON.stringify([...visibleColumns]));render()}))}
function applyColumnVisibility(){document.querySelectorAll('#resultTable [data-col]').forEach(el=>el.classList.toggle('col-hidden',!visibleColumns.has(el.dataset.col)))}
function showFailed(){
  const insufficientCount = insufficientHistorySymbols.length;
  const realErrorCount = failedSymbols.length;

  $('failedSummary').textContent =
    `${insufficientCount} sembol yetersiz fiyat geçmişi nedeniyle tam analiz edilemedi.` +
    (realErrorCount
      ? ` ${realErrorCount} sembolde gerçek veri/hesaplama hatası var.`
      : '');

  let html = '';

  if(insufficientCount){
    html += `
      <div>
        <b>Yetersiz geçmiş / yeni hisse (${insufficientCount})</b>
        <div>
          ${insufficientHistorySymbols
            .map(s => `<span>${esc(s)}</span>`)
            .join('')}
        </div>
      </div>
    `;
  }

  if(realErrorCount){
    html += `
      <div>
        <b>Gerçek hata (${realErrorCount})</b>
        <div>
          ${failedSymbols
            .map(s => `<span>${esc(s)}</span>`)
            .join('')}
        </div>
      </div>
    `;
  }

  $('failedList').innerHTML =
    html || 'Yetersiz geçmiş veya gerçek hata bulunan sembol yok.';

  openModal('failedModal');
}

function sparkPath(values,w=180,h=90,pad=5){
  if(!Array.isArray(values)||values.length<2)return '';
  const min=Math.min(...values),max=Math.max(...values),range=max-min||1;
  return values.map((v,i)=>`${i?'L':'M'} ${(pad+i*(w-2*pad)/(values.length-1)).toFixed(1)} ${(h-pad-(v-min)/range*(h-2*pad)).toFixed(1)}`).join(' ')
}
function saveMarketCardSelection(){localStorage.setItem('bistVisibleMarketCards',JSON.stringify([...visibleMarketCards]))}
function buildMarketCardSettings(){
  const box=$('marketCardSettings');if(!box)return;
  const groups={};marketCardCatalog.forEach(c=>(groups[c.category||'Diğer']??=[]).push(c));
  box.innerHTML=Object.entries(groups).map(([group,cards])=>`<fieldset><legend>${esc(group)}</legend>${cards.map(c=>`<label><input type="checkbox" data-market-card="${esc(c.code)}" ${visibleMarketCards.has(c.code)?'checked':''}> <span><b>${esc(c.code)}</b><small>${esc(c.name)}</small></span></label>`).join('')}</fieldset>`).join('');
  box.querySelectorAll('input[data-market-card]').forEach(i=>i.addEventListener('change',()=>{i.checked?visibleMarketCards.add(i.dataset.marketCard):visibleMarketCards.delete(i.dataset.marketCard);saveMarketCardSelection();renderMarketCards(marketCardCatalog)}));
}
function renderMarketCards(cards){
  const box=$('marketCards');marketCardCatalog=Array.isArray(cards)?cards:[];buildMarketCardSettings();
  const shown=marketCardCatalog.filter(c=>visibleMarketCards.has(c.code));
  if(!shown.length){box.innerHTML='<div class="market-loading">Gösterilecek piyasa kartı seçilmedi. Ayarlar → Piyasa Kartları bölümünden seçim yapın.</div>';return}
  box.innerHTML=shown.map(c=>{const up=Number(c.changePct)>=0,path=sparkPath(c.spark);const lastX=180-5;const area=path?`${path} L ${lastX} 90 L 5 90 Z`:'';return `<article class="market-card"><div><h4>${esc(c.code)}</h4><small>${esc(c.name)}</small><strong>${n(c.value)}</strong><div class="market-change ${up?'up':'down'}">${up?'↗':'↘'} ${n(c.changePct)}%</div></div><svg class="spark-svg" viewBox="0 0 180 90" preserveAspectRatio="none"><path class="spark-fill" d="${area}" fill="${up?'#34d399':'#fb7185'}"></path><path d="${path}" fill="none" stroke="${up?'#34d399':'#fb7185'}" stroke-width="2.5" vector-effect="non-scaling-stroke"></path></svg></article>`}).join('')
}
function renderHealth(health){
  health=health||{};const score=Number(health.score)||0;
  $('marketScore').textContent=score;$('healthGauge').style.setProperty('--score',score);
  $('healthLabel').textContent=health.label||'Tarama bekleniyor';$('positivePct').textContent=`${health.positivePct||0}%`;$('trendPct').textContent=`${health.trendPct||0}%`;$('healthBreakouts').textContent=health.breakouts||0;
  $('scoreReasons').innerHTML=(health.reasons||[]).map(r=>`<div class="reason-row"><span>${esc(r.name)}</span><b>${esc(r.value)}</b><strong class="reason-impact ${(r.impact||0)>=0?'up':'down'}">${(r.impact||0)>=0?'+':''}${r.impact||0}</strong></div>`).join('');
  renderExposureChart(allRows)
}
function renderHealthFromRows(rows){
  const total=rows?.length||0;if(!total){renderHealth({score:0,label:'Tarama bekleniyor',positivePct:0,trendPct:0,breakouts:0,reasons:[]});return}
  const positive=rows.filter(r=>Number(r.changePct)>0).length,trend=rows.filter(r=>r.conditions?.emaTrend).length,breakouts=rows.filter(r=>r.conditions?.breakout).length,high=rows.filter(r=>Number(r.score)>=70).length;
  const positivePct=Math.round(positive/total*100),trendPct=Math.round(trend/total*100);const score=Math.max(0,Math.min(100,Math.round(positivePct*.30+trendPct*.35+Math.min(100,breakouts/total*500)*.15+Math.min(100,high/total*200)*.20)));
  renderHealth({score,label:score>=75?'Güçlü yükseliş':score>=60?'Olumlu':score>=45?'Temkinli':'Zayıf',positivePct,trendPct,breakouts,reasons:[{name:'Pozitif kapanış oranı',value:`${positivePct}%`,impact:Math.round((positivePct-50)*.3)},{name:'EMA trend uyumu',value:`${trendPct}%`,impact:Math.round((trendPct-35)*.35)},{name:'Breakout sayısı',value:breakouts,impact:Math.min(15,breakouts)},{name:'70+ skor hisseler',value:high,impact:Math.min(20,high)}]})
}
function renderExposureChart(rows){
  const svg=$('exposureChart');if(!svg)return;const scores=[...(rows||[])].sort((a,b)=>a.score-b.score).map(r=>Number(r.score)||0);if(scores.length<2){svg.innerHTML='';return}
  const sample=[];for(let i=0;i<Math.min(60,scores.length);i++){const idx=Math.round(i*(scores.length-1)/(Math.min(60,scores.length)-1));sample.push(scores[idx])}
  const path=sample.map((v,i)=>`${i?'L':'M'} ${(i*700/(sample.length-1)).toFixed(1)} ${(220-v*2).toFixed(1)}`).join(' ');const area=`${path} L 700 230 L 0 230 Z`;
  svg.innerHTML=`<path d="${area}" filfilterModesl="rgba(245,158,11,.18)"></path><path d="${path}" fill="none" stroke="#f59e0b" stroke-width="3" vector-effect="non-scaling-stroke"></path>`
}
function renderBreadth(b){
  b=b||{};$('breadthAD').textContent=`${b.advancing||0} / ${b.declining||0}`;$('breadthAdvancePct').textContent=`%${b.advancePct||0} yükselen`;$('breadthEma').textContent=`%${b.aboveEmaPct||0}`;$('breadthVolume').textContent=`%${b.volumeSpikePct||0}`;$('breadthStrong').textContent=`%${b.strongPct||0}`;
}

function moverRows(items,type){
  if(!Array.isArray(items) || !items.length){
    return '<div class="empty">Tarama sonucu bulunamadı</div>';
  }

  return items.slice(0,20).map(r => {
    const change = Number(r.changePct) || 0;
    const cls = change >= 0 ? 'up' : 'down';

    return `
      <div class="mover-row" onclick="showDetail('${esc(r.symbol)}')">
        <b>${esc(r.symbol)}</b>
        <small>${n(r.close)}</small>
        <span class="mover-value ${cls}">
          ${change >= 0 ? '+' : ''}${n(change)}%
        </span>
      </div>
    `;
  }).join('');
}

function heatClass(change){
  const c=Number(change)||0;
  if(c>=4)return'heat-positive-3';
  if(c>=2)return'heat-positive-2';
  if(c>.15)return'heat-positive-1';
  if(c<=-4)return'heat-negative-3';
  if(c<=-2)return'heat-negative-2';
  if(c<-.15)return'heat-negative-1';
  return'heat-neutral';
}

function renderMarketLists(lists){
  lists = lists || {};

  const advancers = lists.advancers || [];
  const decliners = lists.decliners || [];


  if (all.length) {
    advancers = all
      .filter(r => Number(r[periodKey]) > 0)
      .sort((a,b) => Number(b[periodKey]) - Number(a[periodKey]))
      .slice(0,20);

    decliners = all
      .filter(r => Number(r[periodKey]) < 0)
      .sort((a,b) => Number(a[periodKey]) - Number(b[periodKey]))
      .slice(0,20);
  }

  $('advancersList').innerHTML = moverRows(advancers,'change');
  $('declinersList').innerHTML = moverRows(decliners,'change');
  $('volumeLeadersList').innerHTML = moverRows(lists.volumeLeaders || [],'volume');

  
  const heatRows = Array.isArray(lists.heatmap) ? lists.heatmap : [];

$('heatmapUniverse').textContent =
  heatRows.length ? `${heatRows.length} hisse` : 'Son tarama';

$('marketHeatmap').innerHTML = heatRows.length
  ? heatRows.map(r => {
      const size = Math.max(
        1,
        Math.min(3, Math.round((Number(r.score) || 0) / 35))
      );

      return `<div class="heat-tile ${heatClass(r.changePct)}"
        style="--heat-size:${size}"
        onclick="showDetail('${esc(r.symbol)}')"
        title="${esc(r.name || r.symbol)} | Skor ${r.score || 0} | Hacim ${n(r.volumeRatio)}x">
        <b>${esc(r.symbol)}</b>
        <span>${Number(r.changePct) >= 0 ? '+' : ''}${n(r.changePct)}%</span>
        <small>Skor ${r.score || 0}</small>
      </div>`;
    }).join('')
  : 'Sıcaklık haritası için BIST 100 veya BIST Tüm taraması yapın.';
}
async function loadMarketCards(force=false){
  const box=$('marketCards');
  if(box&&!marketCardCatalog.length)box.innerHTML='<div class="market-loading">Canlı piyasa kartları yükleniyor…</div>';
  try{
    const data=await fetchJson(`/api/market-cards?force=${force?1:0}&_=${Date.now()}`,{cache:'no-store'});
    renderMarketCards(data.cards||[]);
    $('dashboardUpdated').textContent=data.updatedAt||'-';
    if(data.warning&&box){const note=document.createElement('div');note.className='dashboard-note';note.textContent=data.warning;box.appendChild(note)}
  }catch(e){
    if(box)box.innerHTML=`<div class="error-box">Canlı piyasa kartları alınamadı: ${esc(e.message)} <button id="retryMarketCards" class="secondary">Tekrar dene</button></div>`;
    setTimeout(()=>{const b=$('retryMarketCards');if(b)b.onclick=()=>loadMarketCards(true)},0);
  }
}

async function loadMarketMovers(force=false){
  try{
    const data = await fetchJson(
      `/api/market-movers?force=${force ? 1 : 0}&_=${Date.now()}`,
      {cache:'no-store'}
    );

    if(!data || data.ok === false){
      throw new Error(data?.error || 'Piyasa verileri alınamadı.');
    }

    // Yükselenler
    $('advancersList').innerHTML =
      moverRows(data.advancers || [], 'change');

    // Düşenler
    $('declinersList').innerHTML =
      moverRows(data.decliners || [], 'change');

    // Hacimliler
    $('volumeLeadersList').innerHTML =
      moverRows(data.volumeLeaders || [], 'volume');


    // Hisse sıcaklık haritası - maksimum 40 hisse
    const heat = Array.isArray(data.heatmap)
      ? data.heatmap.slice(0,40)
      : [];

    $('heatmapUniverse').textContent =
      heat.length ? `${heat.length} hisse` : '-';

    $('marketHeatmap').innerHTML = heat.length
      ? heat.map(r => `
          <div
            class="heat-tile ${heatClass(r.changePct)}"
            onclick="showDetail('${esc(r.symbol)}')"
            title="${esc(r.symbol)} | ${n(r.changePct)}%"
          >
            <b>${esc(r.symbol)}</b>
            <span>
              ${Number(r.changePct) >= 0 ? '+' : ''}${n(r.changePct)}%
            </span>
          </div>
        `).join('')
      : '<div class="empty">Hisse verisi bulunamadı.</div>';


    // Sektör sıcaklık haritası
    const sectors = Array.isArray(data.sectorHeatmap)
      ? data.sectorHeatmap
      : [];

    $('sectorHeatmapCount').textContent =
      sectors.length ? `${sectors.length} sektör` : '-';

    $('sectorHeatmap').innerHTML = sectors.length
      ? sectors.map(r => `
          <div
            class="heat-tile ${heatClass(r.changePct)}"
            title="${esc(r.sector)} | ${n(r.changePct)}% | ${r.count || 0} hisse"
          >
            <b>${esc(r.sector)}</b>
            <span>
              ${Number(r.changePct) >= 0 ? '+' : ''}${n(r.changePct)}%
            </span>
            <small>${r.count || 0} hisse</small>
          </div>
        `).join('')
      : '<div class="empty">Sektör verisi bulunamadı.</div>';

  }catch(e){
    console.error('Market movers:', e);

    $('advancersList').innerHTML =
      '<div class="empty">Piyasa verisi alınamadı.</div>';

    $('declinersList').innerHTML =
      '<div class="empty">Piyasa verisi alınamadı.</div>';

    $('volumeLeadersList').innerHTML =
      '<div class="empty">Piyasa verisi alınamadı.</div>';

    $('marketHeatmap').innerHTML =
      '<div class="empty">Hisse sıcaklık verisi alınamadı.</div>';

    $('sectorHeatmap').innerHTML =
      '<div class="empty">Sektör sıcaklık verisi alınamadı.</div>';
  }
}

async function loadScanDashboard(){
  try{
    const data=await fetchJson(`/api/dashboard-scan?_=${Date.now()}`,{cache:'no-store'});
    renderBreadth(data.breadth||{});renderMarketLists(data.marketLists||{});
    $('healthDate').textContent=data.updatedAt||'-';
    if(!allRows.length)renderHealth(data.health||{});
    if(!data.hasScan){
      const heat=$('marketHeatmap');if(heat)heat.innerHTML='<div class="empty">Piyasa genişliği, yükselenler/düşenler ve ısı haritası için tarama yapın. Piyasa kartları taramadan bağımsızdır.</div>';
    }
  }catch(e){
    renderBreadth({});renderMarketLists({});
    const heat=$('marketHeatmap');if(heat)heat.innerHTML=`<div class="error-box">Son tarama verileri alınamadı: ${esc(e.message)}</div>`;
  }
}
async function loadDashboard(force=false){
  await Promise.allSettled([
    loadMarketCards(force),
    loadMarketMovers(force),
    loadScanDashboard()
  ]);

  const lastUpdated = $('dashboardLastUpdated');

  if(lastUpdated){
    const now = new Date();

    lastUpdated.textContent =
      `Son güncelleme: ${now.toLocaleDateString('tr-TR')} ${now.toLocaleTimeString('tr-TR', {
        hour: '2-digit',
        minute: '2-digit'
      })}`;
  }
}

function download(path){window.location.href=path}
document.querySelectorAll('.nav-item').forEach(b=>b.addEventListener('click',async()=>{const view=b.dataset.view;showView(view);if(view==='dashboard')await loadDashboard(false);if(view==='backtest')await loadLastBacktest();if(view==='kap'&&!kapRows.length)await loadKap(false);if(view==='decision'){if(!allRows.length)await loadLast();renderDecisionCenter()}}));
document.querySelectorAll('[data-goto]').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.setup)$('setup').value=b.dataset.setup;showView(b.dataset.goto);render()}));
['minScore','minVol','setup','search'].forEach(id=>$(id).addEventListener('input',render));

function openModal(id){const m=$(id);if(!m)return;m.classList.remove('hidden');document.body.classList.add('modal-open');const card=m.querySelector('.modal-card');if(card)card.scrollTop=0}
function closeModal(id){const m=$(id);if(!m)return;m.classList.add('hidden');if(document.querySelectorAll('.modal:not(.hidden)').length===0)document.body.classList.remove('modal-open')}
function closeAllModals(){document.querySelectorAll('.modal:not(.hidden)').forEach(m=>m.classList.add('hidden'));document.body.classList.remove('modal-open')}
$('scan').addEventListener('click',scan);$('scanTop').addEventListener('click',scan);$('save').addEventListener('click',saveSettings);$('clearSettings').addEventListener('click',()=>{localStorage.removeItem('bistScannerSettings');location.reload()});$('exportCsv').addEventListener('click',()=>download('/api/export.csv'));$('exportExcel').addEventListener('click',()=>download('/api/export.xlsx'));$('profile').addEventListener('change',e=>applyPreset(e.target.value));$('saveProfile').addEventListener('click',saveNamedProfile);$('savedProfiles').addEventListener('change',e=>loadNamedProfile(e.target.value));$('deleteProfile').addEventListener('click',deleteNamedProfile);$('columnButton').addEventListener('click',()=>$('columnPanel').classList.toggle('hidden'));$('failedButton')?.addEventListener('click',showFailed);$('closeFailed')?.addEventListener('click',()=>closeModal('failedModal'));$('failedModal')?.addEventListener('click',e=>{if(e.target===$('failedModal'))closeModal('failedModal')});$('closeDetail')?.addEventListener('click',()=>closeModal('detailModal'));$('detailModal')?.addEventListener('click',e=>{if(e.target===$('detailModal'))closeModal('detailModal')});$('detailPrev')?.addEventListener('click',()=>showAdjacentDetail(-1));$('detailNext')?.addEventListener('click',()=>showAdjacentDetail(1));document.addEventListener('keydown',e=>{const detailOpen=!$('detailModal')?.classList.contains('hidden');if(e.key==='Escape'){closeAllModals();return}if(detailOpen&&e.key==='ArrowLeft')showAdjacentDetail(-1);if(detailOpen&&e.key==='ArrowRight')showAdjacentDetail(1)});
$('selectAllCards').addEventListener('click',()=>{visibleMarketCards=new Set(marketCardCatalog.map(c=>c.code));saveMarketCardSelection();renderMarketCards(marketCardCatalog)});
$('defaultCards').addEventListener('click',()=>{visibleMarketCards=new Set(defaultMarketCards);saveMarketCardSelection();renderMarketCards(marketCardCatalog)});
loadSettings();persistWatch();buildColumnPanel();refreshProfiles();Promise.allSettled([loadLast(),loadDashboard()]);

document.addEventListener('keydown',e=>{if(e.key==='Escape')closeAllModals()});


let activeBacktestJob=null;
function renderBacktest(data){
  const s=data?.summary||{};
  $('btSignals').textContent=s.totalSignals||0;$('btWins').textContent=s.wins||0;$('btLosses').textContent=s.losses||0;
  $('btWinRate').textContent=`${n(s.winRate,1)}%`;$('btAvgReturn').textContent=`${n(s.avgReturn)}%`;$('btMaxDd').textContent=`${n(s.maxDrawdown)}%`;
  $('btUpdated').textContent=data?.updatedAt?`Son test: ${data.updatedAt} · ${s.strategy||''}`:'Henüz çalıştırılmadı';
  const rows=Array.isArray(data?.symbols)?data.symbols:[];
  $('btRows').innerHTML=rows.map(r=>`<tr><td><b>${esc(r.symbol)}</b><small>${esc(r.name||'')}</small></td><td>${r.signals}</td><td class="positive">${r.wins}</td><td class="negative">${r.losses}</td><td><span class="score">%${n(r.winRate,1)}</span></td><td class="${r.avgReturn>=0?'positive':'negative'}">${n(r.avgReturn)}%</td><td class="negative">${n(r.maxDrawdown)}%</td><td>${n(r.avgMfe)}%</td></tr>`).join('')||'<tr><td colspan="8">Backtest sonucu yok.</td></tr>';
}
async function loadLastBacktest(){try{renderBacktest(await fetchJson(`/api/backtest/last?_=${Date.now()}`,{cache:'no-store'}))}catch(e){$('btUpdated').textContent='Backtest verisi alınamadı: '+e.message}}
async function runBacktest(){
  const q=new URLSearchParams({universe:$('btUniverse').value,period:$('btPeriod').value,donchian:$('btDonchian').value,rsiMin:$('btRsi').value,volumeMin:$('btVolume').value,moneyFlowMin:$('btMoneyFlow').value,squeezeMin:$('btSqueeze').value,rsMin:$('btRs').value,targetPct:$('btTarget').value,stopPct:$('btStop').value,holdingDays:$('btHold').value,maxSymbols:$('btMaxSymbols').value,useBreakout:$('btUseBreakout').checked,useRsi:$('btUseRsi').checked,useVolume:$('btUseVolume').checked,useEma:$('btUseEma').checked,useSupertrend:$('btUseSupertrend').checked,useMoneyFlow:$('btUseMoneyFlow').checked,useSqueeze:$('btUseSqueeze').checked,useRs:$('btUseRs').checked});
  $('runBacktest').disabled=true;$('runBacktest').textContent='Backtest sürüyor…';$('btProgress').classList.remove('hidden');$('btMessage').textContent='Backtest işi başlatılıyor…';$('btPercent').textContent='0%';$('btBar').style.width='0%';
  try{const job=await fetchJson(`/api/backtest/start?${q}`,{cache:'no-store'});if(!job?.id)throw new Error(job?.message||'Backtest işi başlatılamadı.');activeBacktestJob=job.id;pollBacktest()}catch(e){$('runBacktest').disabled=false;$('runBacktest').textContent='Backtest Başlat';alert('Backtest başlatılamadı: '+e.message)}
}
async function pollBacktest(){if(!activeBacktestJob)return;try{const j=await fetchJson(`/api/backtest/status?job=${activeBacktestJob}&_=${Date.now()}`,{cache:'no-store'});$('btPercent').textContent=`${j.percent||0}%`;$('btBar').style.width=`${j.percent||0}%`;$('btMessage').textContent=j.message||'Çalışıyor…';if(j.state==='done'){renderBacktest(j.result);$('btMessage').textContent='Backtest tamamlandı';activeBacktestJob=null;$('runBacktest').disabled=false;$('runBacktest').textContent='Backtest Başlat';return}if(j.state==='error'){throw new Error(j.message||'Backtest hatası')}setTimeout(pollBacktest,800)}catch(e){activeBacktestJob=null;$('runBacktest').disabled=false;$('runBacktest').textContent='Backtest Başlat';alert(e.message)}}
if($('runBacktest'))$('runBacktest').addEventListener('click',runBacktest);
loadLastBacktest();


var parsedBuilderConfig=null;
function builderConditionLabel(key){return ({breakout:'Breakout',rsi:'RSI',volume:'Hacim',ema:'EMA trendi',supertrend:'SuperTrend',moneyFlow:'Para Akışı',squeeze:'Sıkışma',rs:'Relative Strength',macd:'MACD',atr:'ATR genişlemesi',divergence:'Pozitif uyumsuzluk'})[key]||key}
function renderBuilderResult(data){
  parsedBuilderConfig=data;
  const conditions=data.conditions||{}; const thresholds=data.thresholds||{};
  const active=Object.entries(conditions).filter(([,v])=>v).map(([k])=>builderConditionLabel(k));
  const thresholdLabels={donchian:'Donchian',rsiMin:'Minimum RSI',volumeMin:'Minimum hacim',moneyFlowMin:'Minimum para akışı',squeezeMin:'Minimum sıkışma',rsMin:'Minimum RS farkı',atrMin:'Minimum TR/ATR'};
  const chips=[...active.map(x=>`<span class="builder-chip active">${esc(x)}</span>`),...Object.entries(thresholds).map(([k,v])=>`<span class="builder-chip"><b>${esc(thresholdLabels[k]||k)}</b> ${esc(v)}</span>`)];
  $('builderSummary').innerHTML=`<div class="builder-result-head"><strong>${esc(data.strategyName||'Özel Strateji')}</strong><span>${data.intent==='backtest'?'Backtest':'Tarama'}</span></div><div class="builder-chip-grid">${chips.join('')||'<span>Koşul bulunamadı</span>'}</div>${data.notes?.length?`<div class="builder-notes">${data.notes.map(x=>`<div>• ${esc(x)}</div>`).join('')}</div>`:''}`;
  $('applyBuilderScan').disabled=!data.ok;$('applyBuilderBacktest').disabled=!data.ok;
  $('builderStatus').textContent=data.ok?'Komut başarıyla çözümlendi. Uygulamak istediğin hedefi seç.':'Komutta uygulanabilir teknik koşul bulunamadı.';$('builderStatus').classList.remove('hidden');
}
async function parseBuilder(){const prompt=$('builderPrompt').value.trim();if(!prompt){alert('Önce stratejini yaz.');return}$('parseBuilder').disabled=true;$('parseBuilder').textContent='Çözümleniyor…';$('builderStatus').textContent='Komut analiz ediliyor…';$('builderStatus').classList.remove('hidden');try{const data=await fetchJson(`/api/ai-builder/parse?prompt=${encodeURIComponent(prompt)}&_=${Date.now()}`,{cache:'no-store'});renderBuilderResult(data)}catch(e){$('builderStatus').textContent='Komut çözümlenemedi: '+e.message;$('builderStatus').classList.remove('hidden')}finally{$('parseBuilder').disabled=false;$('parseBuilder').textContent='Komutu Çözümle'}}
function applyBuilderToScan(){if(!parsedBuilderConfig)return;const c=parsedBuilderConfig.conditions||{},t=parsedBuilderConfig.thresholds||{};const map={breakout:'fBreakout',volume:'fVolume',ema:'fEma',rsi:'fRsi',macd:'fMacd',atr:'fAtr',supertrend:'fSupertrend',divergence:'fDivergence'};Object.values(map).forEach(id=>{if($(id))$(id).value='off'});Object.entries(map).forEach(([k,id])=>{if(c[k]&&$(id))$(id).value='required'});if(t.donchian&&$('donchianLength'))$('donchianLength').value=t.donchian;if(t.volumeMin&&$('volumeSpikeValue'))$('volumeSpikeValue').value=t.volumeMin;if(t.atrMin&&$('atrRatio'))$('atrRatio').value=t.atrMin;showView('scanner');$('warning').textContent='AI Builder ayarları taramaya uygulandı. Taramayı Başlat düğmesine bas.';$('warning').classList.remove('hidden');}
function applyBuilderToBacktest(){
  if(!parsedBuilderConfig) return;

  const c = parsedBuilderConfig.conditions || {};
  const t = parsedBuilderConfig.thresholds || {};

  showView('backtest');

  const ids = {
    breakout: 'btUseBreakout',
    rsi: 'btUseRsi',
    volume: 'btUseVolume',
    ema: 'btUseEma',
    supertrend: 'btUseSupertrend',
    moneyFlow: 'btUseMoneyFlow',
    squeeze: 'btUseSqueeze',
    rs: 'btUseRs'
  };
  // AI Builder hazır komut butonları
document.querySelectorAll('#builderView button').forEach(btn => {
  const text = btn.textContent.trim();

  const isExample =
    text.startsWith('RSI 55 üzeri') ||
    text.startsWith('Son 20 günün zirvesini') ||
    text.startsWith('Sıkışma skoru 70 üzeri');

  if(!isExample) return;

  btn.addEventListener('click', () => {
    const prompt = $('builderPrompt');
    if(!prompt) return;

    prompt.value = text;
    prompt.focus();
  });
});

  // Önce bütün Backtest koşullarını kapat
  Object.values(ids).forEach(id => {
    const el = $(id);
    if(el) el.checked = false;
  });

  // AI Builder'ın seçtiklerini aç
  Object.entries(ids).forEach(([key,id]) => {
    const el = $(id);
    if(el) el.checked = c[key] === true;
  });

  // Eşikleri aynen aktar
  if($('btDonchian') && t.donchian !== undefined)
    $('btDonchian').value = t.donchian;

  if($('btRsi') && t.rsiMin !== undefined)
    $('btRsi').value = t.rsiMin;

  if($('btVolume') && t.volumeMin !== undefined)
    $('btVolume').value = String(t.volumeMin).replace('.', ',');

  if($('btMoneyFlow') && t.moneyFlowMin !== undefined)
    $('btMoneyFlow').value = t.moneyFlowMin;

  if($('btSqueeze') && t.squeezeMin !== undefined)
    $('btSqueeze').value = t.squeezeMin;

  if($('btRs') && t.rsMin !== undefined)
    $('btRs').value = t.rsMin;

  console.log('AI Builder → Backtest:', {
    conditions: c,
    thresholds: t
  });
}


// v2.8 Screener AI
var lastScreenerAiPrompt='';
var lastScreenerAiSpec=null;
function trNorm(s){return String(s||'').toLocaleLowerCase('tr').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/ı/g,'i').replace(/ş/g,'s').replace(/ğ/g,'g').replace(/ü/g,'u').replace(/ö/g,'o').replace(/ç/g,'c')}
function screenerAiSpec(prompt){
  const t=trNorm(prompt); const spec={prompt,criteria:[],sector:null,min:{},max:{},flags:{},limit:30};
  const has=(...xs)=>xs.some(x=>t.includes(x));
  if(has('patlamaya hazir','breakout ihtimali','breakout yapma ihtimali')){Object.assign(spec.min,{breakoutScore:70,squeezeScore:60,rsScore:65,moneyFlowScore:55,compositeScore:70});spec.flags.emaTrend=true;spec.criteria.push('Breakout ≥70','Sıkışma ≥60','RS ≥65','Para Akışı ≥55','Composite ≥70','Pozitif EMA trendi')}
  if(has('breakout aday','breakout ara','direnc kir')){spec.min.breakoutScore=Math.max(spec.min.breakoutScore||0,70);spec.criteria.push('Breakout skoru ≥70')}
  if(has('en guclu','guclu hisseler','bugun sadece bakmam')){Object.assign(spec.min,{compositeScore:80,rsScore:70,moneyFlowScore:55});spec.criteria.push('Composite ≥80','RS ≥70','Para Akışı ≥55')}
  if(has('sikisan','sikisma','daralan','vcp')){spec.min.squeezeScore=75;spec.criteria.push('Sıkışma skoru ≥75')}
  if(has('para girisi','para akisi','kurumsal birikim')){spec.min.moneyFlowScore=70;spec.criteria.push('Para Akışı ≥70')}
  if(has('hacim patlamasi','hacmi artan','hacim artisi','hacim iki kat','hacim 2 kat')){spec.min.volumeRatio=has('iki kat','2 kat')?2:1.5;spec.criteria.push(`Hacim oranı ≥${spec.min.volumeRatio}x`)}
  if(has('momentum')){spec.min.rsi=55;spec.flags.macdBullish=true;spec.criteria.push('RSI ≥55','MACD pozitif')}
  if(has('guclu trend','trend hisseleri')){spec.flags.emaTrend=true;spec.min.compositeScore=Math.max(spec.min.compositeScore||0,65);spec.criteria.push('Pozitif EMA trendi','Composite ≥65')}
  if(has('rs si yuksek','rs yuksek','relative strength')){spec.min.rsScore=75;spec.criteria.push('RS ≥75')}
  if(has('henuz breakout yapmamis','breakout yapmamis')){spec.max.breakoutScore=69;spec.min.squeezeScore=Math.max(spec.min.squeezeScore||0,60);spec.criteria.push('Breakout <70','Sıkışma ≥60')}
  const sec=[['banka','Banka'],['sanayi','Sanayi'],['savunma','Savunma'],['enerji','Enerji'],['ulasim','Ulaştırma'],['holding','Holding']].find(([k])=>t.includes(k));if(sec){spec.sector=sec[1];spec.criteria.push(`Sektör: ${sec[1]}`)}
  const rsi=t.match(/rsi\s*(?:si)?\s*(\d+)/);if(rsi){spec.min.rsi=+rsi[1];spec.criteria.push(`RSI ≥${rsi[1]}`)}
  if(!spec.criteria.length){Object.assign(spec.min,{compositeScore:70,rsScore:60});spec.criteria.push('Composite ≥70','RS ≥60')}
  return spec;
}
function screenerAiMatch(r,s){
  for(const [k,v] of Object.entries(s.min))if((Number(r[k])||0)<v)return false;
  for(const [k,v] of Object.entries(s.max))if((Number(r[k])||0)>v)return false;
  if(s.sector&&!trNorm(r.sector).includes(trNorm(s.sector)))return false;
  if(s.flags.emaTrend&&!r.conditions?.emaTrend)return false;
  if(s.flags.macdBullish&&!r.conditions?.macdBullish)return false;
  return true;
}
function screenerAiConfidence(r,s){
  const base=(Number(r.compositeScore)||Number(r.score)||0)*.42+(Number(r.aiBreakoutProbability)||0)*.28+(Number(r.rsScore)||0)*.12+(Number(r.moneyFlowScore)||0)*.10+(Number(r.breakoutScore)||0)*.08;
  let fit=0, count=0;for(const [k,v] of Object.entries(s.min)){count++;fit+=Math.min(1,(Number(r[k])||0)/Math.max(v,1))}if(s.flags.emaTrend){count++;fit+=r.conditions?.emaTrend?1:0}if(s.flags.macdBullish){count++;fit+=r.conditions?.macdBullish?1:0}
  return Math.max(0,Math.min(99,Math.round(base*.75+(count?fit/count*100:70)*.25)));
}
function screenerAiExplain(r,s){
  const why=[]; const risk=[];
  if((r.squeezeScore||0)>=75)why.push('Güçlü sıkışma');
  if((r.breakoutScore||0)>=80)why.push('Yüksek breakout skoru'); else if((r.distanceToResistancePct??99)<=3)why.push('Dirence çok yakın');
  if((r.moneyFlowScore||0)>=70)why.push('Para akışı pozitif');
  if((r.rsScore||0)>=75)why.push(`RS ${r.rsScore}`);
  if((r.compositeScore||0)>=80)why.push(`Composite ${r.compositeScore}`);
  if((r.volumeRatio||0)>=1.5)why.push(`Hacim ${n(r.volumeRatio)}x`); else risk.push('Hacim teyidi zayıf');
  if((r.distanceToResistancePct??0)>0)risk.push(`Dirence ${n(r.distanceToResistancePct)}% uzaklık`);
  if((r.aiRiskScore||0)>=65)risk.push(`Model risk skoru ${r.aiRiskScore}`);
  return {why:why.slice(0,5),risk:risk.slice(0,3)};
}
function renderScreenerAi(spec){
  const rows=allRows.filter(r=>screenerAiMatch(r,spec)).map(r=>({...r,_aiConfidence:screenerAiConfidence(r,spec),_aiText:screenerAiExplain(r,spec)})).sort((a,b)=>b._aiConfidence-a._aiConfidence).slice(0,spec.limit);
  $('screenerAiCriteria').innerHTML=spec.criteria.map(x=>`<span>${esc(x)}</span>`).join('');
  $('screenerAiResultLabel').textContent=`“${spec.prompt}” · ${rows.length} sonuç`;
  $('screenerAiResults').innerHTML=rows.length?rows.map(r=>`<article class="ai-result-card" onclick="showDetail('${esc(r.symbol)}')"><div class="ai-result-head"><div><b>${esc(r.symbol)}</b><span>${'★'.repeat(Math.max(1,Math.round(r._aiConfidence/20)))}</span><small>${esc(r.name||'')} · ${esc(r.sector||'Diğer')}</small></div><strong>%${r._aiConfidence}<small>AI güveni</small></strong></div><div class="ai-result-scores"><span>Composite <b>${r.compositeScore??r.score}</b></span><span>Breakout <b>${r.breakoutScore??'-'}</b></span><span>Sıkışma <b>${r.squeezeScore??'-'}</b></span><span>RS <b>${r.rsScore??'-'}</b></span><span>Para <b>${r.moneyFlowScore??'-'}</b></span></div><div class="ai-result-reasons"><div><h4>Seçilme nedenleri</h4>${r._aiText.why.map(x=>`<p>✓ ${esc(x)}</p>`).join('')||'<p>✓ Genel teknik uyum</p>'}</div><div class="risk"><h4>Risk</h4>${r._aiText.risk.map(x=>`<p>• ${esc(x)}</p>`).join('')||'<p>• Belirgin ek risk sinyali yok</p>'}</div></div></article>`).join(''):'<div class="empty">Bu komuta uyan hisse bulunamadı. Daha geniş bir ifade deneyebilirsin.</div>';
  $('screenerAiInterpretation').innerHTML=`<b>AI yorumu:</b> ${spec.criteria.map(esc).join(' · ')} <button id="openAiResults">Sonuçları aç (${rows.length})</button>`;$('screenerAiInterpretation').classList.remove('hidden');$('openAiResults').onclick=()=>showView('screenerai');
  showView('screenerai');
}
async function ensureScanData(messageTarget){
  if(allRows.length)return true;
  if(messageTarget)messageTarget.innerHTML='<div class="empty">Tarama otomatik başlatılıyor…</div>';
  if(activeJob){while(activeJob)await new Promise(r=>setTimeout(r,600));}
  
  if(!allRows.length)await loadLast();
  if(!allRows.length)throw new Error('Tarama sonucu oluşmadı. Tarama ekranında veri kaynağı ve sembol listesini kontrol et.');
  return true;
}
async function runScreenerAi(){
  const prompt=$('screenerAiPrompt').value.trim();if(!prompt){alert('Ne aradığını yaz.');return}
  lastScreenerAiPrompt=prompt;lastScreenerAiSpec=screenerAiSpec(prompt);$('runScreenerAi').disabled=true;$('runScreenerAi').textContent='Analiz ediliyor…';
  showView('screenerai');$('screenerAiResultLabel').textContent='Tarama verisi hazırlanıyor…';
  try{await ensureScanData($('screenerAiResults'));renderScreenerAi(lastScreenerAiSpec)}catch(e){$('screenerAiResultLabel').textContent='Screener AI çalıştırılamadı';$('screenerAiResults').innerHTML=`<div class="error-box">${esc(e.message)} <button class="secondary" onclick="showView('scanner')">Tarama ekranını aç</button></div>`}finally{$('runScreenerAi').disabled=false;$('runScreenerAi').textContent='AI ile Tara'}
}
$('runScreenerAi')?.addEventListener('click',runScreenerAi);$('screenerAiPrompt')?.addEventListener('keydown',e=>{if(e.key==='Enter')runScreenerAi()});$('rerunScreenerAi')?.addEventListener('click',()=>{if(lastScreenerAiSpec)renderScreenerAi(lastScreenerAiSpec)});document.querySelectorAll('[data-ai-prompt]').forEach(b=>b.addEventListener('click',()=>{$('screenerAiPrompt').value=b.dataset.aiPrompt;runScreenerAi()}));

// v2.5 Alarm Merkezi
let alarms=safeJsonParse(localStorage.getItem('bistAlarms')||'[]',[]);
let alarmTriggers=storageGet('bistAlarmTriggers',[]);if(!Array.isArray(alarmTriggers))alarmTriggers=[];
function alarmId(){return 'a'+Date.now().toString(36)+Math.random().toString(36).slice(2,7)}
function saveAlarmState(){localStorage.setItem('bistAlarms',JSON.stringify(alarms));localStorage.setItem('bistAlarmTriggers',JSON.stringify(alarmTriggers));renderAlarms()}
function alarmConditionText(a){const names={setupBreakout:'Breakout oluştu',priceAbove:`Fiyat > ${a.value}`,priceBelow:`Fiyat < ${a.value}`,changeAbove:`Değişim > %${a.value}`,volumeAbove:`Hacim > ${a.value}x`,rsiAbove:`RSI > ${a.value}`,rsiBelow:`RSI < ${a.value}`,emaTrend:'EMA trend uyumu',supertrendBuy:'SuperTrend AL',breakoutScoreAbove:`Breakout skoru > ${a.value}`,squeezeScoreAbove:`Sıkışma skoru > ${a.value}`,moneyFlowAbove:`Para akışı > ${a.value}`,rsAbove:`RS > ${a.value}`,compositeAbove:`Composite > ${a.value}`,aiAbove:`AI olasılığı > %${a.value}`};return names[a.condition]||a.condition}
function conditionNeedsValue(c){return !['setupBreakout','emaTrend','supertrendBuy'].includes(c)}
function updateAlarmValueVisibility(){if(!$('alarmCondition'))return;const needed=conditionNeedsValue($('alarmCondition').value);$('alarmValueLabel').classList.toggle('hidden',!needed)}
function renderAlarms(){if(!$('alarmList'))return;$('alarmNavCount').textContent=alarms.filter(a=>a.enabled).length;$('alarmCountText').textContent=`${alarms.length} alarm`;$('triggerCountText').textContent=`${alarmTriggers.length} bildirim`;$('alarmList').innerHTML=alarms.length?alarms.map(a=>`<div class="alarm-item ${a.enabled?'':'off'}"><div><h4>${esc(a.name)}</h4><small>${esc(a.symbol)} · ${esc(alarmConditionText(a))}</small><small>${a.channel==='browser'?'Masaüstü + uygulama':'Uygulama içi'} · ${a.enabled?'Aktif':'Kapalı'}</small></div><div class="alarm-actions"><button class="secondary" onclick="toggleAlarmEnabled('${a.id}')">${a.enabled?'Durdur':'Başlat'}</button><button class="secondary" onclick="deleteAlarm('${a.id}')">Sil</button></div></div>`).join(''):'<div class="empty">Henüz alarm oluşturulmadı.</div>';$('triggerList').innerHTML=alarmTriggers.length?[...alarmTriggers].reverse().map(t=>`<div class="trigger-item"><div><h4>${esc(t.symbol)} · ${esc(t.alarmName)}</h4><small>${esc(t.message)}</small><small>${esc(t.time)}</small></div><strong>${esc(t.value)}</strong></div>`).join(''):'<div class="empty">Henüz tetiklenen alarm yok.</div>'}
function saveAlarm(){const symbol=($('alarmSymbol').value||'TÜM').trim().toLocaleUpperCase('tr-TR');const condition=$('alarmCondition').value;const value=Number($('alarmValue').value);const name=$('alarmName').value.trim()||`${symbol} ${alarmConditionText({condition,value})}`;if(conditionNeedsValue(condition)&&!Number.isFinite(value)){alert('Geçerli bir eşik gir.');return}alarms.push({id:alarmId(),name,symbol:symbol||'TÜM',condition,value:conditionNeedsValue(condition)?value:null,channel:$('alarmChannel').value,enabled:$('alarmEnabled').checked,createdAt:new Date().toLocaleString('tr-TR')});$('alarmName').value='';saveAlarmState()}
function toggleAlarmEnabled(id){const a=alarms.find(x=>x.id===id);if(a){a.enabled=!a.enabled;saveAlarmState()}}
function deleteAlarm(id){alarms=alarms.filter(x=>x.id!==id);saveAlarmState()}
window.toggleAlarmEnabled=toggleAlarmEnabled;window.deleteAlarm=deleteAlarm;
function alarmMatch(a,r){const c=r.conditions||{},v=Number(a.value);switch(a.condition){case'setupBreakout':return r.setup==='Breakout'||!!c.breakout;case'priceAbove':return Number(r.close)>v;case'priceBelow':return Number(r.close)<v;case'changeAbove':return Number(r.changePct)>v;case'volumeAbove':return Number(r.volumeRatio)>v;case'rsiAbove':return Number(r.rsi)>v;case'rsiBelow':return Number(r.rsi)<v;case'emaTrend':return !!c.emaTrend;case'supertrendBuy':return !!c.supertrendBuy;case'breakoutScoreAbove':return Number(r.breakoutScore)>v;case'squeezeScoreAbove':return Number(r.squeezeScore)>v;case'moneyFlowAbove':return Number(r.moneyFlowScore)>v;case'rsAbove':return Number(r.rsScore)>v;case'compositeAbove':return Number(r.compositeScore??r.score)>v;case'aiAbove':return Number(r.aiBreakoutProbability)>v;default:return false}}
function alarmCurrentValue(a,r){const map={priceAbove:r.close,priceBelow:r.close,changeAbove:`%${n(r.changePct)}`,volumeAbove:`${n(r.volumeRatio)}x`,rsiAbove:n(r.rsi,1),rsiBelow:n(r.rsi,1),breakoutScoreAbove:r.breakoutScore,squeezeScoreAbove:r.squeezeScore,moneyFlowAbove:r.moneyFlowScore,rsAbove:r.rsScore,compositeAbove:r.compositeScore??r.score,aiAbove:`%${r.aiBreakoutProbability}`};return map[a.condition]??'Tetiklendi'}
function evaluateAlarms(rows,manual=false){if(!Array.isArray(rows)||!rows.length){if(manual)alert('Önce tarama yapmalısın.');return}let added=0;for(const a of alarms.filter(x=>x.enabled)){for(const r of rows){if(a.symbol!=='TÜM'&&a.symbol!=='TUM'&&a.symbol!==String(r.symbol).toLocaleUpperCase('tr-TR'))continue;if(!alarmMatch(a,r))continue;const key=`${a.id}|${r.symbol}|${r.dataDate||new Date().toISOString().slice(0,10)}`;if(alarmTriggers.some(t=>t.key===key))continue;const message=`${r.symbol}: ${alarmConditionText(a)} koşulu gerçekleşti.`;const t={key,alarmId:a.id,alarmName:a.name,symbol:r.symbol,message,value:String(alarmCurrentValue(a,r)),time:new Date().toLocaleString('tr-TR')};alarmTriggers.push(t);added++;if(a.channel==='browser'&&'Notification'in window&&Notification.permission==='granted')new Notification(`BIST Scanner · ${r.symbol}`,{body:message});}}if(alarmTriggers.length>300)alarmTriggers=alarmTriggers.slice(-300);saveAlarmState();if(manual)alert(added?`${added} yeni alarm tetiklendi.`:'Yeni tetiklenen alarm yok.')}
async function requestNotify(){if(!('Notification'in window)){alert('Bu tarayıcı masaüstü bildirimlerini desteklemiyor.');return}const p=await Notification.requestPermission();alert(p==='granted'?'Bildirim izni verildi.':'Bildirim izni verilmedi.')}
if($('alarmCondition'))$('alarmCondition').addEventListener('change',updateAlarmValueVisibility);if($('saveAlarm'))$('saveAlarm').addEventListener('click',saveAlarm);if($('requestNotify'))$('requestNotify').addEventListener('click',requestNotify);if($('testAlarms'))$('testAlarms').addEventListener('click',()=>evaluateAlarms(allRows,true));if($('clearTriggers'))$('clearTriggers').addEventListener('click',()=>{alarmTriggers=[];saveAlarmState()});updateAlarmValueVisibility();renderAlarms();


// v2.6 Portfolio Management
let portfolio=safeJsonParse(localStorage.getItem('bistPortfolio')||'[]',[]);
function money(v){const x=Number(v);return Number.isFinite(x)?x.toLocaleString('tr-TR',{style:'currency',currency:'TRY',maximumFractionDigits:2}):'-'}
function portfolioPrice(symbol){const r=allRows.find(x=>String(x.symbol).toUpperCase()===String(symbol).toUpperCase());return r&&Number.isFinite(Number(r.close))?Number(r.close):null}
function savePortfolio(){localStorage.setItem('bistPortfolio',JSON.stringify(portfolio));renderPortfolio()}
function addPortfolio(){

  const symbol = ($('pfSymbol')?.value || '').trim().toUpperCase();
  const lots = Number($('pfLots')?.value);
  const cost = Number($('pfCost')?.value);
  const stop = Number($('pfStop')?.value || 0);
  const target = Number($('pfTarget')?.value || 0);
  const note = ($('pfNote')?.value || '').trim();

  if(!/^[A-Z0-9]{3,8}$/.test(symbol)){
    alert('Geçerli bir BIST kodu gir.');
    return;
  }

  if(!(lots > 0) || !(cost > 0)){
    alert('Lot ve maliyet sıfırdan büyük olmalı.');
    return;
  }

  if(editingPortfolioId){

    const p = portfolio.find(x => x.id === editingPortfolioId);

    if(p){
      p.symbol = symbol;
      p.lots = lots;
      p.cost = cost;
      p.stop = stop > 0 ? stop : null;
      p.target = target > 0 ? target : null;
      p.note = note;
      p.updatedAt = new Date().toISOString();
    }

    editingPortfolioId = null;

  } else {

    portfolio.push({
      id: `pf_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      symbol,
      lots,
      cost,
      stop: stop > 0 ? stop : null,
      target: target > 0 ? target : null,
      note,
      createdAt: new Date().toISOString()
    });

  }

  ['pfSymbol','pfCost','pfStop','pfTarget','pfNote'].forEach(id => {
    if($(id)) $(id).value = '';
  });

  if($('pfLots')) $('pfLots').value = '100';

  const btn = $('pfAdd');
  if(btn) btn.textContent = 'Pozisyon Ekle';

  savePortfolio();
}
function deletePortfolio(id){portfolio=portfolio.filter(x=>x.id!==id);savePortfolio()}

let editingPortfolioId = null;

function editPortfolio(id){

  const p = portfolio.find(x => x.id === id);
  if(!p) return;

  editingPortfolioId = id;

  $('pfSymbol').value = p.symbol;
  $('pfLots').value = p.lots;
  $('pfCost').value = p.cost;
  $('pfStop').value = p.stop || '';
  $('pfTarget').value = p.target || '';
  $('pfNote').value = p.note || '';

  const btn = $('pfAdd');
  if(btn) btn.textContent = 'Pozisyonu Güncelle';

  showView('portfolio');
}
function renderPortfolio(){
  if(!$('pfRows'))return;
  let totalCost=0,currentValue=0,totalRisk=0,priced=0;
  const rows=portfolio.map(p=>{
    const lots=Number(p.lots)||0,cost=Number(p.cost)||0,price=portfolioPrice(p.symbol),base=lots*cost;totalCost+=base;
    let value=null,pnl=null,pnlPct=null,risk=null,targetProfit=null;
    if(price!==null){priced++;value=lots*price;pnl=value-base;pnlPct=base?100*pnl/base:0;currentValue+=value;if(p.stop)risk=Math.max(0,(price-Number(p.stop))*lots);if(p.target)targetProfit=(Number(p.target)-price)*lots}
    if(risk!==null)totalRisk+=risk;
    return `<tr><td><b>${esc(p.symbol)}</b></td><td>${n(lots,2)}</td><td>${money(cost)}</td><td>${price===null?'<span class="muted">Tarama yok</span>':money(price)}</td><td>${value===null?'-':money(value)}</td><td class="${(pnl||0)>=0?'positive':'negative'}">${pnl===null?'-':money(pnl)}</td><td class="${(pnlPct||0)>=0?'positive':'negative'}">${pnlPct===null?'-':n(pnlPct)+'%'}</td><td>${p.stop?money(p.stop):'-'}</td><td>${risk===null?'-':money(risk)}</td><td>${p.target?money(p.target):'-'}</td><td class="${(targetProfit||0)>=0?'positive':'negative'}">${targetProfit===null?'-':money(targetProfit)}</td><td>${esc(p.note||'')}</td><td><div class="portfolio-actions"><button class="secondary small" onclick="editPortfolio('${p.id}')">Düzenle</button><button class="secondary small" onclick="deletePortfolio('${p.id}')">Sil</button></div></td></tr>`;
  });
  $('pfRows').innerHTML=rows.join('');$('pfEmpty').classList.toggle('hidden',portfolio.length>0);
  const pnl=priced?currentValue-totalCost:0,pct=totalCost?100*pnl/totalCost:0;
  $('pfPositionCount').textContent=portfolio.length;$('pfTotalCost').textContent=money(totalCost);$('pfCurrentValue').textContent=priced?money(currentValue):'Fiyat bekleniyor';$('pfPnl').textContent=priced?money(pnl):'-';$('pfPnl').className=pnl>=0?'positive':'negative';$('pfPnlPct').textContent=priced?`${n(pct)}%`:'-';$('pfPnlPct').className=pct>=0?'positive':'negative';$('pfRisk').textContent=money(totalRisk);$('pfUpdated').textContent=priced?`${priced}/${portfolio.length} pozisyon fiyatlandı`:'Önce tarama çalıştır';
}
window.deletePortfolio=deletePortfolio;window.editPortfolio=editPortfolio;
if($('pfAdd'))$('pfAdd').addEventListener('click',addPortfolio);
if($('pfRefresh'))$('pfRefresh').addEventListener('click',()=>{renderPortfolio();alert(allRows.length?'Portföy son tarama fiyatlarıyla güncellendi.':'Önce tarama çalıştırmalısın.');});
if($('pfClear'))$('pfClear').addEventListener('click',()=>{if(confirm('Tüm portföy kayıtları silinsin mi?')){portfolio=[];savePortfolio()}});
renderPortfolio();


var kapRows=[];
function renderKap(){
  if(!$('kapList'))return;
  const q=($('kapSearch')?.value||'').toLocaleLowerCase('tr'); const sentiment=$('kapSentiment')?.value||'all';
  const rows=kapRows.filter(r=>(sentiment==='all'||r.sentiment===sentiment)&&(!q||`${r.symbol} ${r.title} ${r.summary} ${r.category}`.toLocaleLowerCase('tr').includes(q)));
  $('kapCount').textContent=kapRows.length; $('kapPositive').textContent=kapRows.filter(r=>r.sentiment==='Olumlu').length; $('kapNeutral').textContent=kapRows.filter(r=>r.sentiment==='Nötr').length; $('kapNegative').textContent=kapRows.filter(r=>r.sentiment==='Olumsuz').length;
  $('kapList').innerHTML=rows.length?rows.map(r=>`<article class="kap-item"><div class="kap-score ${r.sentiment==='Olumlu'?'positive-bg':r.sentiment==='Olumsuz'?'negative-bg':'neutral-bg'}"><strong>${r.impactScore}</strong><small>${'★'.repeat(r.stars||1)}</small></div><div class="kap-body"><div class="kap-meta"><b>${esc(r.symbol||'KAP')}</b><span>${esc(r.category)}</span><time>${esc([r.date,r.time].filter(Boolean).join(' '))}</time></div><h3>${esc(r.title)}</h3><p>${esc(r.summary||'')}</p><div class="kap-footer"><span class="pill">${esc(r.sentiment)}</span><a href="${esc(r.url)}" target="_blank" rel="noopener">KAP bildirimini aç ↗</a></div></div></article>`).join(''):'<div class="empty">Seçime uyan bildirim bulunamadı.</div>';
}
async function loadKap(force=false){
  if(!$('kapList'))return; $('kapList').innerHTML='<div class="empty">KAP bildirimleri alınıyor…</div>';
  try{const d=await fetchJson(`/api/kap/notifications?limit=100&force=${force?1:0}`,{cache:'no-store'}); kapRows=Array.isArray(d.rows)?d.rows:[]; $('kapUpdated').textContent=`${d.updated||'-'} · ${d.source||'KAP'}${d.stale?' · önbellek':''}`; renderKap(); if(!d.ok)$('kapList').insertAdjacentHTML('afterbegin',`<div class="error-box">KAP verisi alınamadı: ${esc(d.error||'KAP sayfası yanıt vermedi.')} <a href="https://www.kap.org.tr/tr/bildirim-sorgu" target="_blank" rel="noopener">KAP Bildirim Sorgu'yu aç ↗</a></div>`)}catch(e){$('kapList').innerHTML=`<div class="error-box">KAP verileri alınamadı: ${esc(e.message)} <a href="https://www.kap.org.tr/tr/bildirim-sorgu" target="_blank" rel="noopener">KAP'ı aç ↗</a></div>`}
}
$('refreshKap')?.addEventListener('click',()=>loadKap(true)); $('kapSearch')?.addEventListener('input',renderKap); $('kapSentiment')?.addEventListener('change',renderKap);
document.querySelector('[data-view="kap"]')?.addEventListener('click',()=>{if(!kapRows.length)loadKap(false)});


// v2.9 AI Decision Center
let decisionFavorites=new Set(safeJsonParse(localStorage.getItem('decisionFavorites')||'[]',[]));
function decisionRisk(r){
  const ai=Number(r.aiRiskScore); const atr=Number(r.trAtr); const resistance=Number(r.resistanceDistancePct);
  let risk=Number.isFinite(ai)?ai:50;
  if(Number.isFinite(atr))risk+=Math.max(0,atr-1.5)*8;
  if(Number.isFinite(resistance)&&resistance<0)risk+=8;
  if(Number(r.moneyFlowScore)<45)risk+=8;
  return Math.max(0,Math.min(100,Math.round(risk)));
}
function decisionExpectedReturn(r){
  const ai=Number(r.aiExpectedReturn10d);
  if(Number.isFinite(ai)&&ai!==0)return ai;
  return Math.max(-5,Math.min(18,((Number(r.breakoutScore)||0)-50)*.08+((Number(r.rsScore)||0)-50)*.05+((Number(r.moneyFlowScore)||0)-50)*.035));
}
function decisionConfidence(r){
  const ai=Number(r.aiBreakoutProbability)||0, model=Number(r.aiModelConfidence)||0, comp=Number(r.compositeScore??r.score)||0, rs=Number(r.rsScore)||0, mf=Number(r.moneyFlowScore)||0;
  const aiPart=ai>0?ai:model;return Math.round(Math.max(0,Math.min(100,aiPart*.30+comp*.38+rs*.18+mf*.14)));
}
function decisionScore(r){
  const conf=decisionConfidence(r), ret=decisionExpectedReturn(r), risk=decisionRisk(r), squeeze=Number(r.squeezeScore)||0;
  return Math.round(Math.max(0,Math.min(100,conf*.58+Math.max(0,ret)*1.25+(100-risk)*.20+squeeze*.10)));
}
function decisionRiskLabel(v){return v<=35?'Düşük':v<=60?'Orta':'Yüksek'}
function decisionReasons(r){
 const out=[]; if(Number(r.compositeScore??r.score)>=80)out.push('Composite çok güçlü'); if(Number(r.aiBreakoutProbability)>=70)out.push('AI breakout olasılığı yüksek'); if(Number(r.rsScore)>=75)out.push('Göreceli güç lideri'); if(Number(r.moneyFlowScore)>=65)out.push('Para akışı pozitif'); if(Number(r.squeezeScore)>=70)out.push('Güçlü sıkışma'); if(r.conditions?.emaTrend)out.push('EMA trendi pozitif'); if(!out.length)out.push('Teknik veriler birlikte değerlendirildi');return out.slice(0,4);
}
function decisionRisks(r,risk){
 const out=[]; if(risk>60)out.push('Toplam risk yüksek'); if(Number(r.volumeRatio)<1.2)out.push('Hacim teyidi zayıf'); if(Number(r.resistanceDistancePct)>5)out.push(`Dirence %${n(r.resistanceDistancePct)} uzak`); if(Number(r.rsi)>75)out.push('RSI aşırı alıma yakın'); if(!out.length)out.push('Belirgin teknik risk sınırlı'); return out.slice(0,3);
}
function toggleDecisionFavorite(symbol,e){if(e)e.stopPropagation();decisionFavorites.has(symbol)?decisionFavorites.delete(symbol):decisionFavorites.add(symbol);localStorage.setItem('decisionFavorites',JSON.stringify([...decisionFavorites]));renderDecisionCenter()}
window.toggleDecisionFavorite=toggleDecisionFavorite;
function renderDecisionCenter(){

  if(!$('decisionOpportunities')) return;

  const limit = Number($('decisionLimit')?.value || 10);

  const rows = (allRows || [])
    .map(r => ({
      ...r,
      _decisionRisk: decisionRisk(r),
      _decisionReturn: decisionExpectedReturn(r),
      _decisionConfidence: decisionConfidence(r),
      _decisionScore: decisionScore(r)
    }))
    .filter(r => r.symbol && r._decisionConfidence >= 35)
    .sort((a,b) => b._decisionScore - a._decisionScore)
    .slice(0, limit);

  $('decisionOpportunityCount').textContent = rows.length;

  $('decisionAvgConfidence').textContent = rows.length
    ? `${Math.round(rows.reduce((a,b) => a + b._decisionConfidence, 0) / rows.length)}%`
    : '0%';

  $('decisionAvgReturn').textContent = rows.length
    ? `${n(rows.reduce((a,b) => a + b._decisionReturn, 0) / rows.length)}%`
    : '0%';

  $('decisionLowRisk').textContent =
    rows.filter(r => r._decisionRisk <= 35).length;

  $('decisionFavoriteCount').textContent =
    decisionFavorites.size;

  $('decisionOpportunities').innerHTML = rows.length
    ? rows.map((r,i) => {

        const reasons = decisionReasons(r);
        const risks = decisionRisks(r, r._decisionRisk);

        return `
          <article class="decision-card"
                   onclick="showDetail('${esc(r.symbol)}')">

            <div class="decision-rank">${i + 1}</div>

            <div class="decision-main">

              <div class="decision-symbol">
                <b>${esc(r.symbol)}</b>
                <small>${esc(r.name || r.sector || '')}</small>
              </div>

              <div class="decision-metrics">

                <span>
                  <small>Karar</small>
                  <b>${r._decisionScore}</b>
                </span>

                <span>
                  <small>Güven</small>
                  <b>%${r._decisionConfidence}</b>
                </span>

                <span>
                  <small>Beklenti</small>
                  <b class="${r._decisionReturn >= 0 ? 'positive' : 'negative'}">
                    ${n(r._decisionReturn)}%
                  </b>
                </span>

                <span>
                  <small>Risk</small>
                  <b class="risk-${decisionRiskLabel(r._decisionRisk).toLowerCase()}">
                    ${decisionRiskLabel(r._decisionRisk)}
                  </b>
                </span>

              </div>

              <div class="decision-explain">

                <div>
                  ${reasons.map(x => `✓ ${esc(x)}`).join('<br>')}
                </div>

                <div class="risk">
                  ${risks.map(x => `! ${esc(x)}`).join('<br>')}
                </div>

              </div>

            </div>

            <button
              class="decision-star ${decisionFavorites.has(r.symbol) ? 'on' : ''}"
              onclick="toggleDecisionFavorite('${esc(r.symbol)}',event)">
              ★
            </button>

          </article>
        `;

      }).join('')
    : 'Uygun fırsat bulunamadı. Önce BIST 100 veya BIST Tüm taraması yap.';


  const fav = (allRows || [])
    .filter(r => decisionFavorites.has(r.symbol))
    .map(r => ({
      ...r,
      _decisionRisk: decisionRisk(r),
      _decisionReturn: decisionExpectedReturn(r),
      _decisionConfidence: decisionConfidence(r),
      _decisionScore: decisionScore(r)
    }))
    .sort((a,b) => b._decisionScore - a._decisionScore);

  $('decisionFavorites').innerHTML = fav.length
    ? fav.map(r => `
        <div class="decision-fav-row"
             onclick="showDetail('${esc(r.symbol)}')">

          <b>${esc(r.symbol)}</b>

          <span>Karar ${r._decisionScore}</span>

          <span>Güven %${r._decisionConfidence}</span>

          <span class="${r._decisionReturn >= 0 ? 'positive' : 'negative'}">
            ${n(r._decisionReturn)}%
          </span>

          <button onclick="toggleDecisionFavorite('${esc(r.symbol)}',event)">
            ×
          </button>

        </div>
      `).join('')
    : 'Henüz favori aday yok.';


  const matrix = rows.slice(0,20);

  $('decisionMatrix').innerHTML = matrix.length
    ? matrix.map(r => `
        <button
          class="matrix-point risk-${decisionRiskLabel(r._decisionRisk).toLowerCase()}"
          style="
            left:${Math.max(3,Math.min(95,r._decisionRisk))}%;
            bottom:${Math.max(5,Math.min(92,50+r._decisionReturn*3))}%;
          "
          title="${esc(r.symbol)} · Risk ${r._decisionRisk} · Beklenti ${n(r._decisionReturn)}%"
          onclick="showDetail('${esc(r.symbol)}')">

          <b>${esc(r.symbol)}</b>

        </button>
      `).join('') + 'Risk → Beklenen getiri ↑'
    : 'Tarama verisi bekleniyor.';
}
async function refreshDecisionCenter(){

  const btn = $('refreshDecision');

  if(btn){
    btn.disabled = true;
    btn.textContent = 'Yenileniyor…';
  }

  try{

    const data = await fetchJson(
      `/api/last-scan?_=${Date.now()}`,
      {cache:'no-store'}
    );

    if(Array.isArray(data.rows) && data.rows.length){
      allRows = data.rows;
    }

    renderDecisionCenter();

  }catch(e){

    console.error('Decision Center yenileme hatası:', e);

    $('decisionOpportunities').innerHTML =
      `<div class="error-box">
        Karar Merkezi yenilenemedi: ${esc(e.message)}
      </div>`;

  }finally{

    if(btn){
      btn.disabled = false;
      btn.textContent = 'Karar Merkezini Yenile';
    }

  }
}

const refreshDecisionBtn = $('refreshDecision');

if(refreshDecisionBtn){
  refreshDecisionBtn.addEventListener('click', async (e) => {
    e.preventDefault();
    await refreshDecisionCenter();
  });
}

$('decisionLimit')?.addEventListener(
  'change',
  renderDecisionCenter
);

renderDecisionCenter();

// v2.9.6 shared module bridge
window.__bistSetRows = function(rows){
  allRows = Array.isArray(rows) ? rows : [];
  if(typeof render === 'function') render();
};

window.__bistGetRows = function(){
  return allRows;
};

window.__bistSetKapRows = function(rows){
  kapRows = Array.isArray(rows) ? rows : [];
};

window.__bistGetKapRows = function(){
  return kapRows;
};

window.renderBuilderResult = renderBuilderResult;
window.runScreenerAi = runScreenerAi;
window.renderKap = renderKap;
window.renderDecisionCenter = renderDecisionCenter;
function renderMinerviniResults(){
  const rows = (allRows || [])
    .filter(r => r.minervini && r.minervini.passed === true)
    .sort((a,b) => {
      const rsDiff = (Number(b.rsScore) || 0) - (Number(a.rsScore) || 0);
      if(rsDiff !== 0) return rsDiff;

      return (Number(b.score) || 0) - (Number(a.score) || 0);
    });

  const area = $('minerviniResultArea');
  const label = $('minerviniResultLabel');
  const body = $('minerviniRows');

  if(!area || !label || !body) return;

  area.classList.remove('hidden');

  label.textContent =
    `${rows.length} hisse 8/8 Minervini kriterini karşılıyor`;

  body.innerHTML = rows.length
    ? rows.map(r => `
        <tr onclick="showDetail('${esc(r.symbol)}')" style="cursor:pointer;">
          <td><b>${esc(r.symbol)}</b></td>
          <td>${n(r.close)}</td>
          <td>${r.rsScore ?? '-'}</td>
          <td><b>${r.minervini?.score ?? 0}/8</b></td>
          <td>${n(r.sma50)}</td>
          <td>${n(r.sma150)}</td>
          <td>${n(r.sma200)}</td>
          <td>${n(r.low52)}</td>
          <td>${n(r.high52)}</td>
        </tr>
      `).join('')
    : `
      <tr>
        <td colspan="9">
          Minervini kriterlerinin tamamını karşılayan hisse bulunamadı.
        </td>
      </tr>
    `;
}

$('openMinerviniResults')?.addEventListener('click', async () => {
  try{
    if(!allRows.length){
      await loadLast();
    }

    renderMinerviniResults();

  }catch(e){
    alert(`Minervini sonuçları açılamadı: ${e.message}`);
  }
});

// Yükselenler / Düşenler / Hacimliler sekmeleri
document.addEventListener('click', (event) => {
  const marketTab = event.target.closest('.market-tab');

  if (!marketTab) return;

  const selected = marketTab.dataset.marketTab;

  document.querySelectorAll('.market-tab').forEach(tab => {
    tab.classList.toggle(
      'active',
      tab.dataset.marketTab === selected
    );
  });

  document.querySelectorAll('.market-tab-panel').forEach(panel => {
    panel.classList.toggle(
      'active',
      panel.dataset.marketPanel === selected
    );
  });
});



