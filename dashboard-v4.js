(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

  function createSectionTitle(number, title, subtitle = '') {
    const head = document.createElement('div');
    head.className = 'v4-section-title';
    head.innerHTML = `<span class="v4-section-number">${number}</span><div><h2>${esc(title)}</h2>${subtitle ? `<p>${esc(subtitle)}</p>` : ''}</div>`;
    return head;
  }

  function wrapBlock(number, title, subtitle, nodes, className = '') {
    const section = document.createElement('section');
    section.className = `v4-dashboard-section ${className}`.trim();
    section.appendChild(createSectionTitle(number, title, subtitle));
    nodes.forEach((node) => node && section.appendChild(node));
    return section;
  }

  function makeThemeControls() {
    const controls = document.createElement('div');
    controls.className = 'v4-theme-controls';
    controls.innerHTML = `
      <button type="button" data-theme-choice="dark" title="Koyu tema">☾</button>
      <button type="button" data-theme-choice="light" title="Açık tema">☀</button>
      <button type="button" data-theme-choice="system" title="Sistem temasını kullan">◐</button>`;
    controls.addEventListener('click', (event) => {
      const button = event.target.closest('[data-theme-choice]');
      if (!button) return;
      setTheme(button.dataset.themeChoice);
    });
    return controls;
  }

  function resolveTheme(choice) {
    if (choice === 'system') {
      return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    return choice === 'light' ? 'light' : 'dark';
  }

  function setTheme(choice) {
    localStorage.setItem('bistDashboardTheme', choice);
    document.documentElement.dataset.theme = resolveTheme(choice);
    document.querySelectorAll('[data-theme-choice]').forEach((button) => {
      button.classList.toggle('active', button.dataset.themeChoice === choice);
    });
  }

  function setupTheme() {
    const topActions = document.querySelector('.top-actions');
    if (topActions && !topActions.querySelector('.v4-theme-controls')) {
      topActions.insertBefore(makeThemeControls(), topActions.lastElementChild);
    }
    const choice = localStorage.getItem('bistDashboardTheme') || 'dark';
    setTheme(choice);
    matchMedia('(prefers-color-scheme: light)').addEventListener?.('change', () => {
      if ((localStorage.getItem('bistDashboardTheme') || 'dark') === 'system') setTheme('system');
    });
  }

  function compactHeader() {
    document.body.classList.add('dashboard-v4-enabled');
    const topbar = document.querySelector('.topbar');
    topbar?.classList.add('v4-compact-topbar');
    const snapshot = document.querySelector('#dashboardView .snapshot-head');
    snapshot?.classList.add('v4-compact-snapshot');
  }

  function createScreenerMirror() {
    const panel = document.createElement('section');
    panel.className = 'panel v4-ai-results-panel';
    panel.innerHTML = `
      <div class="panel-title v4-ai-result-head">
        <div><span class="eyebrow">SCREENER AI</span><h3 id="v4AiResultTitle">Sonuçlar</h3><span id="v4AiResultCount">Henüz sorgu çalıştırılmadı</span></div>
        <div class="v4-ai-actions">
          <button type="button" class="secondary small" id="v4NewQuery">＋ Yeni Sorgu</button>
          <button type="button" class="secondary small" id="v4Examples">✦ Örnek Komutlar</button>
          <button type="button" class="danger small" id="v4ClearAi">Sonuçları Temizle</button>
        </div>
      </div>
      <div id="v4AiCriteria" class="ai-criteria"></div>
      <div id="v4AiResults" class="v4-ai-result-body">
        <div class="v4-empty-ai">
          <strong>Bir sorgu yazarak taramayı başlat.</strong>
          <span>Hazır komutları kullanabilir veya kendi cümleni yazabilirsin.</span>
        </div>
      </div>`;

    panel.querySelector('#v4NewQuery').addEventListener('click', () => {
      $('screenerAiPrompt')?.focus();
      document.querySelector('.screener-ai-hero')?.scrollIntoView({behavior:'smooth', block:'center'});
    });
    panel.querySelector('#v4Examples').addEventListener('click', () => {
      document.querySelector('.ai-command-library')?.scrollIntoView({behavior:'smooth', block:'center'});
    });
    panel.querySelector('#v4ClearAi').addEventListener('click', () => {
      const source = $('screenerAiResults');
      if (source) source.innerHTML = '<div class="empty">Dashboard üzerindeki AI komut kutusunu kullan.</div>';
      const label = $('screenerAiResultLabel');
      if (label) label.textContent = 'Henüz sorgu çalıştırılmadı';
      syncScreenerMirror();
    });
    return panel;
  }

  function isEmptyResultHtml(html) {
    return !html || /Dashboard üzerindeki AI komut kutusunu kullan|Henüz sorgu|0 sonuç|uygun hisse bulunamadı/i.test(html);
  }

  function quickQueriesHtml() {
    const queries = [
      ['🔥','Hacim patlaması olan hisseleri bul'],
      ['↗','Breakout yapan hisseleri bul'],
      ['🚀','Güçlü yükselen hisseleri göster'],
      ['💰','Para girişi olan hisseleri bul'],
      ['📈','RS yükselen hisseleri göster'],
      ['⚡','Momentum kazanan hisseleri göster'],
      ['🛡','Düşük riskli hisseleri göster'],
      ['🎯','Trend gücü yüksek hisseleri göster']
    ];
    return `<div class="v4-empty-ai"><div class="v4-search-icon">⌕</div><strong>Aradığın kriterlere uygun hisse bulunamadı.</strong><span>Farklı anahtar kelimeler deneyebilir veya hızlı sorgulardan birini seçebilirsin.</span><small>HIZLI YENİ SORGULAR</small><div class="v4-quick-query-grid">${queries.map(([icon,text]) => `<button type="button" data-v4-query="${esc(text)}">${icon} ${esc(text)}</button>`).join('')}</div><em>💡 İpucu: Daha genel bir sorgu yazarak daha fazla sonuç elde edebilirsin.</em></div>`;
  }

  function syncScreenerMirror() {
    const sourceResults = $('screenerAiResults');
    const sourceCriteria = $('screenerAiCriteria');
    const sourceLabel = $('screenerAiResultLabel');
    const targetResults = $('v4AiResults');
    const targetCriteria = $('v4AiCriteria');
    const targetCount = $('v4AiResultCount');
    const targetTitle = $('v4AiResultTitle');
    if (!targetResults) return;

    const prompt = $('screenerAiPrompt')?.value?.trim() || localStorage.getItem('bistLastScreenerAiPrompt') || '';
    targetTitle.textContent = prompt ? `“${prompt}” için sonuçlar` : 'Screener AI Sonuçları';
    targetCount.textContent = sourceLabel?.textContent || 'Henüz sorgu çalıştırılmadı';
    targetCriteria.innerHTML = sourceCriteria?.innerHTML || '';
    const html = sourceResults?.innerHTML || '';
    targetResults.innerHTML = isEmptyResultHtml(html) ? quickQueriesHtml() : html;

    targetResults.querySelectorAll('[data-v4-query]').forEach((button) => {
      button.addEventListener('click', () => {
        const promptBox = $('screenerAiPrompt');
        if (promptBox) promptBox.value = button.dataset.v4Query;
        $('runScreenerAi')?.click();
        document.querySelector('.screener-ai-hero')?.scrollIntoView({behavior:'smooth', block:'center'});
      });
    });
  }

  function watchScreener() {
    const source = $('screenerAiResults');
    if (!source) return;
    new MutationObserver(syncScreenerMirror).observe(source, {childList:true, subtree:true, characterData:true});
    const label = $('screenerAiResultLabel');
    if (label) new MutationObserver(syncScreenerMirror).observe(label, {childList:true, subtree:true, characterData:true});
    syncScreenerMirror();
  }

  function safeRows() {
    try {
      return Array.isArray(allRows) ? allRows : [];
    } catch (_) {
      return [];
    }
  }

  function sectorName(row) {
    return String(row?.sector || row?.industry || 'Diğer').trim() || 'Diğer';
  }

  function metric(row, period) {
    if (period === 'weekly') return Number(row.change5d ?? row.weeklyChangePct ?? row.changePct ?? 0);
    if (period === 'monthly') return Number(row.change20d ?? row.monthlyChangePct ?? row.changePct ?? 0);
    return Number(row.changePct ?? 0);
  }

  function heatColor(value) {
    const v = Math.max(-5, Math.min(5, Number(value) || 0));
    if (v >= 0) {
      const alpha = .16 + Math.abs(v) / 5 * .42;
      return `rgba(34,197,94,${alpha.toFixed(2)})`;
    }
    const alpha = .16 + Math.abs(v) / 5 * .42;
    return `rgba(239,68,68,${alpha.toFixed(2)})`;
  }

  function createHeatmapPanel(title, type) {
    const panel = document.createElement('article');
    panel.className = 'panel v4-heatmap-panel';
    panel.dataset.heatmapType = type;
    panel.innerHTML = `
      <div class="panel-title">
        <div><h3>${esc(title)}</h3><span>Renk performansı gösterir.</span></div>
        <div class="v4-period-tabs"><button class="active" data-period="daily">Günlük</button><button data-period="weekly">Haftalık</button><button data-period="monthly">Aylık</button></div>
      </div>
      <div class="v4-heatmap" data-v4-heatmap="${type}"><div class="empty">Tarama verisi bekleniyor.</div></div>`;
    panel.querySelectorAll('[data-period]').forEach((button) => button.addEventListener('click', () => {
      panel.querySelectorAll('[data-period]').forEach((x) => x.classList.toggle('active', x === button));
      renderV4Heatmaps(button.dataset.period, type);
    }));
    return panel;
  }

  function renderV4Heatmaps(period = 'daily', requestedType = null) {
    const rows = safeRows();
    const types = requestedType ? [requestedType] : ['stocks','sectors'];
    types.forEach((type) => {
      const target = document.querySelector(`[data-v4-heatmap="${type}"]`);
      if (!target) return;
      if (!rows.length) {
        target.innerHTML = '<div class="empty">Tarama verisi bekleniyor.</div>';
        return;
      }
      if (type === 'stocks') {
        const sorted = [...rows].sort((a,b) => Math.abs(metric(b, period)) - Math.abs(metric(a, period))).slice(0, 40);
        target.innerHTML = sorted.map((row) => {
          const change = metric(row, period);
          return `<button type="button" class="v4-heat-tile" data-v4-symbol="${esc(row.symbol)}" style="background:${heatColor(change)}"><b>${esc(row.symbol)}</b><span>${change >= 0 ? '+' : ''}${change.toFixed(2)}%</span></button>`;
        }).join('');
      } else {
        const grouped = new Map();
        rows.forEach((row) => {
          const sector = sectorName(row);
          const item = grouped.get(sector) || {sum:0,count:0,score:0};
          item.sum += metric(row, period); item.score += Number(row.compositeScore ?? row.score ?? 0); item.count += 1;
          grouped.set(sector, item);
        });
        const sectors = [...grouped.entries()].map(([name,item]) => ({name, change:item.sum/item.count, score:item.score/item.count, count:item.count})).sort((a,b) => Math.abs(b.change)-Math.abs(a.change)).slice(0, 28);
        target.innerHTML = sectors.map((sector) => `<button type="button" class="v4-heat-tile v4-sector-tile" style="background:${heatColor(sector.change)}"><b>${esc(sector.name)}</b><span>${sector.change >= 0 ? '+' : ''}${sector.change.toFixed(2)}%</span><small>${sector.count} hisse</small></button>`).join('');
      }
    });
    document.querySelectorAll('[data-v4-symbol]').forEach((button) => button.addEventListener('click', () => {
      const row = rows.find((item) => item.symbol === button.dataset.v4Symbol);
      if (row && typeof openDetail === 'function') openDetail(row);
    }));
  }

  function watchRows() {
    const source = $('marketHeatmap') || $('rows');
    if (source) new MutationObserver(() => renderV4Heatmaps()).observe(source, {childList:true, subtree:true});
    setInterval(renderV4Heatmaps, 5000);
  }

  function createModuleGrid() {
    const grid = document.createElement('section');
    grid.className = 'v4-module-grid';
    const modules = [
      ['watchlist','★','Watchlist','İzlemek istediğin hisseleri takip et'],
      ['breakout','◎','Breakout','Kırılma potansiyeli olan hisseler'],
      ['minervini','▣','Minervini','Minervini stratejisine uygun hisseler'],
      ['relative','RS','Relative Strength','Göreceli güç analizi'],
      ['ai','AI','AI Breakout','Yapay zekâ destekli kırılım analizi'],
      ['backtest','BT','Backtest','Stratejilerini geriye dönük test et'],
      ['builder','✦','AI Builder','Kendi stratejini oluştur'],
      ['alarms','⏰','Alarm Merkezi','Fiyat ve koşul alarmlarını yönet'],
      ['portfolio','▣','Portföy','Portföyünü yönet ve analiz et'],
      ['kap','KAP','KAP AI','KAP haberlerini analiz et'],
      ['screenerai','⌕','Screener AI','Yapay zekâ destekli tarama'],
      ['decision','◈','AI Decision Center','Akıllı karar destek merkezi'],
      ['settings','⚙','Ayarlar','Uygulama ayarlarını yönet']
    ];
    grid.innerHTML = modules.map(([view,icon,title,desc]) => `<button type="button" data-v4-view="${view}"><span>${icon}</span><div><b>${title}</b><small>${desc}</small></div><em>›</em></button>`).join('');
    grid.addEventListener('click', (event) => {
      const button = event.target.closest('[data-v4-view]');
      if (!button) return;
      document.querySelector(`.nav-item[data-view="${button.dataset.v4View}"]`)?.click();
    });
    return grid;
  }

  function arrangeDashboard() {
    const root = $('dashboardView');
    if (!root || root.dataset.v4Arranged === '1') return;
    root.dataset.v4Arranged = '1';

    const snapshot = root.querySelector('.snapshot-head');
    const cards = $('marketCards');
    const breadth = root.querySelector('.breadth-strip');
    const lists = root.querySelector('.market-lists-grid');
    const hero = root.querySelector('.screener-ai-hero');
    const health = root.querySelector('.health-panel');
    const intelligence = root.querySelector('.intelligence-grid');
    const stats = root.querySelector('.dashboard-stats');
    const dashGrid = root.querySelector('.dashboard-grid');
    const oldHeatmap = root.querySelector('.heatmap-panel');

    const fragment = document.createDocumentFragment();
    if (snapshot) fragment.appendChild(snapshot);
    fragment.appendChild(wrapBlock(2, 'Genel Veriler', 'Endeks, döviz, emtia ve kripto görünümü', [cards], 'v4-general-section'));
    fragment.appendChild(wrapBlock(3, 'Piyasa Özeti', 'Yükselenler, düşenler ve yüksek hacimliler', [lists], 'v4-market-summary'));

    const aiGrid = document.createElement('div');
    aiGrid.className = 'v4-ai-grid';
    if (hero) aiGrid.appendChild(hero);
    aiGrid.appendChild(createScreenerMirror());
    fragment.appendChild(wrapBlock(4, 'Tarama & Analiz', 'Doğal dil ile tarama ve hızlı yeni sorgular', [aiGrid], 'v4-analysis-section'));

    if (breadth) fragment.appendChild(breadth);
    if (health) fragment.appendChild(health);
    if (intelligence) fragment.appendChild(intelligence);
    if (stats) fragment.appendChild(stats);
    if (dashGrid) fragment.appendChild(dashGrid);

    const heatmaps = document.createElement('section');
    heatmaps.className = 'v4-heatmaps-grid';
    heatmaps.appendChild(createHeatmapPanel('Hisselerin Sıcaklık Haritası', 'stocks'));
    heatmaps.appendChild(createHeatmapPanel('Sektörlerin Sıcaklık Haritası', 'sectors'));
    fragment.appendChild(heatmaps);
    fragment.appendChild(createModuleGrid());

    if (oldHeatmap) oldHeatmap.remove();
    root.replaceChildren(fragment);
    syncScreenerMirror();
    renderV4Heatmaps();
  }

  function boot() {
    compactHeader();
    setupTheme();
    arrangeDashboard();
    watchScreener();
    watchRows();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once:true});
  else boot();
})();
