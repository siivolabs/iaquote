const state = { page: 1, perPage: 30, total: 0, enrichmentTimer: null };
const euro = new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'EUR' });
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
}[char]));

async function api(url, opts = {}) {
  const response = await fetch(url, opts);
  let data = {};
  try { data = await response.json(); } catch (_) { /* réponse vide */ }
  if (!response.ok) throw new Error(data.error || `Erreur HTTP ${response.status}`);
  return data;
}

function toast(message, bad = false) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.toggle('bad-toast', bad);
  element.classList.remove('hidden');
  window.setTimeout(() => element.classList.add('hidden'), 5000);
}

function badge(status) {
  if (status === 'verified' || status === 'pass') return '<span class="badge ok">Vérifié</span>';
  if (status === 'review' || status === 'unknown') return '<span class="badge warn">À contrôler</span>';
  if (status === 'fail' || status === 'failed') return '<span class="badge bad">Échec</span>';
  return '<span class="badge neutral">Non enrichi</span>';
}

function applyTheme(theme) {
  const selected = theme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = selected;
  localStorage.setItem('iaquote-theme', selected);
  const button = $('#themeToggle');
  if (button) {
    const isDark = selected === 'dark';
    button.textContent = isDark ? '☀ Mode clair' : '☾ Mode sombre';
    button.setAttribute('aria-pressed', String(isDark));
  }
}

$('#themeToggle').onclick = () => {
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
};
applyTheme(document.documentElement.dataset.theme || 'dark');

function showView(id) {
  $$('.view').forEach(view => view.classList.toggle('active', view.id === id));
  $$('.tab').forEach(tab => tab.classList.toggle('active', tab.dataset.view === id));
  window.scrollTo({ top: 0, behavior: 'smooth' });
  if (id === 'catalog') loadCatalog();
  if (id === 'enrichment') {
    loadEnrichments();
    loadEnrichmentJob();
  }
  if (id === 'history') loadHistory();
}
$$('.tab').forEach(button => { button.onclick = () => showView(button.dataset.view); });
$$('[data-go]').forEach(button => { button.onclick = () => showView(button.dataset.go); });

const jobLabels = {
  queued: 'En attente', running: 'En cours', blocked: 'Configuration requise',
  cancelling: 'Arrêt en cours', cancelled: 'Arrêté', completed: 'Terminé',
  completed_with_errors: 'Terminé avec erreurs'
};

function renderEnrichmentJob(job) {
  const card = $('#bulkProgress');
  const summary = $('#enrichmentSummary');
  if (!job) {
    card.classList.add('hidden');
    summary.textContent = 'Aucune tâche d’enrichissement lancée.';
    stopEnrichmentPolling();
    return;
  }
  card.classList.remove('hidden');
  const percent = Number(job.progress_percent || 0);
  $('#bulkStatus').textContent = jobLabels[job.status] || job.status;
  $('#bulkStatus').className = `pill ${job.status === 'completed' ? 'ok' : job.status === 'blocked' || job.status === 'completed_with_errors' ? 'warn' : job.status === 'cancelled' ? 'neutral' : 'working'}`;
  $('#bulkProgressBar').style.width = `${Math.min(100, Math.max(0, percent))}%`;
  $('#bulkProgressBar').setAttribute('aria-valuenow', String(percent));
  $('#bulkProgressText').textContent = `${job.processed} / ${job.total} · ${percent.toLocaleString('fr-FR')} %`;
  $('#bulkVerified').textContent = job.verified;
  $('#bulkReview').textContent = job.review;
  $('#bulkFailed').textContent = job.failed;
  $('#bulkRemaining').textContent = job.remaining;
  $('#bulkError').textContent = job.last_error || '';
  $('#cancelBulk').classList.toggle('hidden', !['queued', 'running', 'cancelling'].includes(job.status));
  $('#cancelBulk').disabled = job.status === 'cancelling';
  summary.textContent = job.status === 'running'
    ? `Enrichissement automatique : ${job.processed} produit(s) traité(s) sur ${job.total}.`
    : `Dernier enrichissement : ${jobLabels[job.status] || job.status} · ${job.verified} vérifié(s), ${job.review} à contrôler, ${job.failed} échec(s).`;
  if (['queued', 'running', 'cancelling'].includes(job.status)) startEnrichmentPolling();
  else stopEnrichmentPolling();
}

function startEnrichmentPolling() {
  if (state.enrichmentTimer) return;
  state.enrichmentTimer = window.setInterval(async () => {
    await Promise.all([loadEnrichmentJob(), loadHealth(false, false)]);
    if ($('#enrichment').classList.contains('active')) loadEnrichments();
  }, 2000);
}
function stopEnrichmentPolling() {
  if (!state.enrichmentTimer) return;
  window.clearInterval(state.enrichmentTimer);
  state.enrichmentTimer = null;
}

async function loadEnrichmentJob() {
  try {
    const data = await api('/api/enrich/jobs/current');
    renderEnrichmentJob(data.job);
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadHealth(showError = true, checkOllama = true) {
  try {
    const health = await api('/api/health');
    $('#appStatus').textContent = 'Application opérationnelle';
    $('#appStatus').className = 'pill ok';
    $('#statCatalog').textContent = health.catalog_count;
    $('#statVerified').textContent = health.verified_count;
    $('#statQuotes').textContent = health.quote_count;
    if (health.enrichment_job) renderEnrichmentJob(health.enrichment_job);
  } catch (error) {
    $('#appStatus').textContent = 'Application en erreur';
    $('#appStatus').className = 'pill bad';
    if (showError) toast(error.message, true);
  }
  if (!checkOllama) return;
  try {
    const ollama = await api('/api/ollama/test');
    $('#ollamaStatus').textContent = ollama.ok
      ? (ollama.model_present ? `Ollama · ${ollama.model}` : 'Ollama joignable · modèle absent')
      : 'Ollama indisponible';
    $('#ollamaStatus').className = `pill ${ollama.ok && ollama.model_present ? 'ok' : ollama.ok ? 'warn' : 'bad'}`;
    $('#statModel').textContent = ollama.model || '—';
  } catch (_) {
    $('#ollamaStatus').textContent = 'Ollama indisponible';
    $('#ollamaStatus').className = 'pill bad';
  }
}

async function loadCatalog() {
  const q = encodeURIComponent($('#catalogSearch').value);
  const category = encodeURIComponent($('#categoryFilter').value);
  const status = encodeURIComponent($('#statusFilter').value);
  try {
    const data = await api(`/api/catalog?q=${q}&category=${category}&status=${status}&page=${state.page}&per_page=${state.perPage}`);
    state.total = data.total;
    $('#catalogInfo').textContent = `${data.total} article(s) trouvé(s)`;
    $('#pageInfo').textContent = `Page ${data.page} / ${Math.max(1, Math.ceil(data.total / data.per_page))}`;
    $('#prevPage').disabled = data.page <= 1;
    $('#nextPage').disabled = data.page * data.per_page >= data.total;
    if ($('#categoryFilter').options.length === 1) {
      data.categories.forEach(item => $('#categoryFilter').add(new Option(item, item)));
    }
    $('#catalogRows').innerHTML = data.items.map(product => `<tr>
      <td class="code">${esc(product.code)}</td>
      <td><b>${esc(product.designation)}</b><br><small class="muted">${esc(product.brand)}</small></td>
      <td>${esc(product.category)}</td><td>${euro.format(product.sale_price_ttc)}</td>
      <td>${euro.format(product.margin_ht)}</td>
      <td>Boutique ${product.stock_shop}<br><small class="muted">Central ${product.stock_central}</small></td>
      <td>${badge(product.enrichment_status)}${product.source_url ? `<br><a href="${esc(product.source_url)}" target="_blank" rel="noopener">Fiche technique</a>` : ''}</td>
    </tr>`).join('');
  } catch (error) { toast(error.message, true); }
}

$('#catalogRefresh').onclick = () => { state.page = 1; loadCatalog(); };
$('#prevPage').onclick = () => { state.page -= 1; loadCatalog(); };
$('#nextPage').onclick = () => { state.page += 1; loadCatalog(); };
$('#catalogSearch').addEventListener('keydown', event => {
  if (event.key === 'Enter') { state.page = 1; loadCatalog(); }
});

$('#csvFile').onchange = async event => {
  const file = event.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  toast('Import en cours…');
  try {
    const data = await api('/api/import', { method: 'POST', body: form });
    const queued = data.queued_for_enrichment || 0;
    toast(`${data.imported} article(s) importé(s) · ${queued} placé(s) dans la file d’enrichissement`);
    state.page = 1;
    if (data.enrichment_job) renderEnrichmentJob(data.enrichment_job);
    await Promise.all([loadCatalog(), loadHealth(false)]);
  } catch (error) {
    toast(error.message, true);
  } finally {
    event.target.value = '';
  }
};

function quoteHtml(quote) {
  const checks = quote.checks.map(check => `<div class="check-item">${badge(check.status)}<b>${esc(check.name)}</b><span class="muted">${esc(check.detail)}</span></div>`).join('');
  const lines = quote.lines.map(line => `<tr><td>${esc(line.category)}</td><td><b>${esc(line.designation)}</b><br><small>${esc(line.reason)}</small></td><td class="code">${esc(line.code)}</td><td>${euro.format(line.sale_price_ttc)}</td><td>${euro.format(line.margin_ht)}</td><td>${line.stock_shop}/${line.stock_central}</td></tr>`).join('');
  const vigilance = (quote.sales_copy.points_de_vigilance || []).map(item => `<li>${esc(item)}</li>`).join('') || '<li>Aucun point supplémentaire.</li>';
  const questions = (quote.sales_copy.questions_client || []).map(item => `<li>${esc(item)}</li>`).join('');
  return `<article class="panel"><div class="result-head"><div><p class="eyebrow">PROPOSITION #${quote.id || '—'}</p><h2>${quote.ai.ok ? `Argumentaire généré par ${esc(quote.ai.model)}` : 'Mode de secours sans IA'}</h2></div><div class="metrics"><div class="metric"><span>Total TTC</span><strong>${euro.format(quote.total_ttc)}</strong></div><div class="metric"><span>Marge brute HT</span><strong>${euro.format(quote.margin_ht)}</strong></div><div class="metric"><span>Écart budget</span><strong>${euro.format(quote.budget_delta)}</strong></div></div></div><div class="notice">${esc(quote.sales_copy.argumentaire)}</div><div class="table-wrap quote-table"><table><thead><tr><th>Catégorie</th><th>Produit et raison</th><th>Référence</th><th>Prix TTC</th><th>Marge HT</th><th>Stock B/C</th></tr></thead><tbody>${lines}</tbody></table></div></article><div class="grid two"><article class="panel"><h2>Contrôles déterministes</h2><div class="check-grid">${checks}</div></article><article class="panel"><h2>Références copiables</h2><div class="copybox"><textarea id="refsBox" rows="9" readonly>${esc(quote.references)}</textarea><button class="secondary" onclick="copyRefs()">Copier</button></div></article></div><div class="grid two"><article class="panel"><h2>Points de vigilance</h2><ul>${vigilance}</ul></article><article class="panel"><h2>Questions de validation client</h2><ul>${questions}</ul></article></div>`;
}
window.copyRefs = async () => {
  await navigator.clipboard.writeText($('#refsBox').value);
  toast('Références copiées');
};

$('#quoteForm').onsubmit = async event => {
  event.preventDefault();
  $('#quoteLoading').textContent = 'Sélection, contrôles et appel Ollama…';
  const body = {
    client_need: $('#clientNeed').value,
    budget_ttc: Number($('#budget').value),
    machine_type: $('#machineType').value,
    strategy: $('#strategy').value,
    verified_only: $('#verifiedOnly').checked
  };
  try {
    const quote = await api('/api/quotes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const result = $('#quoteResult');
    result.innerHTML = quoteHtml(quote);
    result.classList.remove('hidden');
    result.scrollIntoView({ behavior: 'smooth' });
    loadHealth(false);
  } catch (error) { toast(error.message, true); }
  finally { $('#quoteLoading').textContent = ''; }
};

async function loadEnrichments() {
  try {
    const data = await api('/api/enrichments');
    $('#enrichmentRows').innerHTML = data.items.map(item => `<tr><td class="code">${esc(item.code)}</td><td><b>${esc(item.designation)}</b><br><small>${esc(item.category)}</small></td><td>${badge(item.status)}</td><td>${Math.round((item.confidence || 0) * 100)} %</td><td>${item.source_url ? `<a href="${esc(item.source_url)}" target="_blank" rel="noopener">Source</a>` : '—'}</td><td>${esc(item.validation_note || '')}</td></tr>`).join('');
  } catch (error) { toast(error.message, true); }
}

$('#enrichForm').onsubmit = async event => {
  event.preventDefault();
  const code = $('#enrichCode').value.trim();
  const body = { url: $('#enrichUrl').value.trim() || null, force: $('#enrichForce').checked };
  $('#enrichMessage').textContent = 'Requête en cours…';
  try {
    const data = await api(`/api/enrich/${encodeURIComponent(code)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    $('#enrichMessage').textContent = `${data.enrichment_status} · confiance ${Math.round((data.confidence || 0) * 100)} %`;
    toast('Enrichissement enregistré');
    await Promise.all([loadEnrichments(), loadHealth(false)]);
  } catch (error) {
    $('#enrichMessage').textContent = error.message;
    toast(error.message, true);
  }
};

$('#startBulk').onclick = async () => {
  $('#startBulk').disabled = true;
  try {
    const job = await api('/api/enrich/bulk', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ force: false }) });
    renderEnrichmentJob(job);
    toast(job.total ? `${job.total} produit(s) placé(s) dans la file` : 'Aucun produit à enrichir');
  } catch (error) { toast(error.message, true); }
  finally { $('#startBulk').disabled = false; }
};

$('#cancelBulk').onclick = async () => {
  const data = await api('/api/enrich/jobs/current');
  if (!data.job) return;
  try {
    const job = await api(`/api/enrich/jobs/${data.job.id}/cancel`, { method: 'POST' });
    renderEnrichmentJob(job);
    toast('Arrêt de la tâche demandé');
  } catch (error) { toast(error.message, true); }
};

async function loadHistory() {
  try {
    const data = await api('/api/quotes');
    $('#historyList').innerHTML = data.items.length ? data.items.map(quote => `<article onclick="openQuote(${quote.id})"><span class="badge ${quote.ai && quote.ai.ok ? 'ok' : 'warn'}">${quote.ai && quote.ai.ok ? 'Ollama' : 'Secours'}</span><h3>Devis #${quote.id} · ${euro.format(quote.total_ttc)}</h3><p>${esc(quote.client_need)}</p><small>${new Date(quote.created_at).toLocaleString('fr-FR')}</small></article>`).join('') : '<p class="muted">Aucun devis enregistré.</p>';
  } catch (error) { toast(error.message, true); }
}
window.openQuote = async id => {
  try {
    const quote = await api(`/api/quotes/${id}`);
    const detail = $('#historyDetail');
    detail.innerHTML = quoteHtml(quote);
    detail.classList.remove('hidden');
    detail.scrollIntoView({ behavior: 'smooth' });
  } catch (error) { toast(error.message, true); }
};
$('#historyRefresh').onclick = loadHistory;

loadHealth();
loadEnrichmentJob();
