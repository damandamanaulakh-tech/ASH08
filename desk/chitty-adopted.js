'use strict';

const state = { csrf: '', status: null, config: null };
const $ = (id) => document.getElementById(id);
const text = (id, value) => { $(id).textContent = value == null || value === '' ? '—' : String(value); };

function notify(message, kind='') {
  const node = $('notice');
  node.textContent = message;
  node.className = `notice ${kind}`.trim();
}

function token() { return localStorage.getItem('ash08_api_token') || ''; }

async function api(path, options={}) {
  const headers = new Headers(options.headers || {});
  const bearer = token();
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`);
  if (state.csrf) headers.set('X-CSRF-Token', state.csrf);
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(path, {...options, headers, credentials: 'same-origin'});
  let payload;
  try { payload = await response.json(); }
  catch { payload = {ok:false,error:{code:'INVALID_RESPONSE',message:'Server returned non-JSON'}}; }
  if (!response.ok) {
    const error = payload.error || {};
    throw new Error(`${error.code || response.status}: ${error.message || 'Request failed'}`);
  }
  return payload;
}

async function session() {
  const payload = await api('/api/session');
  state.csrf = payload.csrf_token || '';
  text('auth-status', payload.auth_mode === 'bearer' ? 'BEARER TOKEN' : 'SAME-ORIGIN SESSION');
}

function clearTable(id) {
  const body = $(id);
  while (body.firstChild) body.removeChild(body.firstChild);
  return body;
}

function td(value, className='') {
  const cell = document.createElement('td');
  cell.textContent = value == null || value === '' ? '—' : String(value);
  if (className) cell.className = className;
  return cell;
}

function emptyRow(body, columns, message) {
  const row = document.createElement('tr');
  const cell = td(message, 'muted');
  cell.colSpan = columns;
  row.appendChild(cell);
  body.appendChild(row);
}

function parameterState(parameter, telemetry) {
  if (parameter.scope === 'governance') return ['ENDPOINT READY', 'state-ready'];
  if (parameter.scope === 'audit') return ['AUDIT ENDPOINT READY', 'state-ready'];
  if (parameter.scope === 'metadata') return ['TELEMETRY INPUT', 'state-ready'];
  let available = false;
  for (const snapshot of telemetry) {
    const feature = (snapshot.features || {})[parameter.id];
    if (feature && feature.status === 'AVAILABLE') available = true;
  }
  return available ? ['AVAILABLE', 'state-available'] : ['UNKNOWN UNTIL EVIDENCE', 'state-unknown'];
}

function renderRegistry() {
  const status = state.status || {};
  const telemetry = status.telemetry || [];
  const body = clearTable('registry-table');
  const parameters = status.parameters || [];
  if (!parameters.length) return emptyRow(body, 5, 'No adopted registry returned.');
  parameters.forEach(parameter => {
    const row = document.createElement('tr');
    const [runtimeState, className] = parameterState(parameter, telemetry);
    row.append(td(parameter.id));
    row.append(td(parameter.family));
    row.append(td(parameter.name));
    row.append(td(parameter.scope));
    row.append(td(runtimeState, className));
    body.appendChild(row);
  });
}

function renderSources() {
  const body = clearTable('sources-table');
  const rows = (state.status || {}).sources || [];
  if (!rows.length) return emptyRow(body, 4, 'No source evidence registered.');
  rows.forEach(item => {
    const row = document.createElement('tr');
    row.append(td(item.source_name));
    row.append(td(item.sha256, 'hash'));
    row.append(td(item.canonical_source_name));
    row.append(td(item.duplicate_of_hash ? 'YES' : 'NO'));
    body.appendChild(row);
  });
}

function renderTelemetry() {
  const body = clearTable('telemetry-table');
  const rows = (state.status || {}).telemetry || [];
  if (!rows.length) return emptyRow(body, 7, 'No telemetry snapshots. Submit real OHLCV evidence to compute fields.');
  rows.sort((a,b) => String(a.symbol).localeCompare(String(b.symbol))).forEach(item => {
    const features = item.features || {};
    const values = Object.values(features);
    const available = values.filter(feature => feature && feature.status === 'AVAILABLE').length;
    const unknown = values.filter(feature => feature && feature.status === 'UNKNOWN').length;
    const adjusted = features['CN-009'];
    const sector = features['CN-024'];
    const row = document.createElement('tr');
    row.append(td(item.symbol));
    row.append(td(item.asof));
    row.append(td(available, 'state-available'));
    row.append(td(unknown, 'state-unknown'));
    row.append(td(adjusted?.status === 'AVAILABLE' ? String(adjusted.value?.adjusted_data) : 'UNKNOWN'));
    row.append(td(sector?.status === 'AVAILABLE' ? sector.value : 'UNKNOWN'));
    row.append(td(item.decision_impact === false ? 'NONE' : 'INVALID'));
    body.appendChild(row);
  });
}

function eventEvidence(item) {
  if (item.event_type === 'paper_trade_evidence') return `${item.quantity || '—'} @ ${item.entry_price || '—'} · ${item.parameter_set_id || '—'}`;
  if (item.event_type === 'rule_followed') return item.rule_followed ? 'Rule followed' : `Override: ${item.reason || 'reason missing'}`;
  if (item.event_type === 'emotion') return `Emotion ${item.emotion_score}/5 · no decision impact`;
  if (item.event_type === 'research_evidence') return `Matched ${item.matched_cases} · Failed ${item.failed_cases} · Locked ${Boolean(item.promotion_locked)}`;
  return '—';
}

function renderEvents() {
  const body = clearTable('events-table');
  const rows = (state.status || {}).recent_events || [];
  if (!rows.length) return emptyRow(body, 4, 'No adopted audit events recorded.');
  [...rows].reverse().forEach(item => {
    const row = document.createElement('tr');
    row.append(td(item.recorded_at));
    row.append(td(item.event_type));
    row.append(td(item.symbol || item.pattern_id || item.trade_id));
    row.append(td(eventEvidence(item)));
    body.appendChild(row);
  });
}

function renderMetrics() {
  const status = state.status || {};
  text('adopted-count', status.adopted_count);
  text('decision-impact', status.decision_impact === false ? 'NONE' : 'INVALID');
  text('source-count', (status.sources || []).length);
  text('symbol-count', (status.telemetry || []).length);
  text('event-count', (status.recent_events || []).length);
  text('discussion-count', status.discussion_queue_included);
}

function render() {
  renderMetrics();
  renderRegistry();
  renderSources();
  renderTelemetry();
  renderEvents();
}

async function load() {
  const [status, config] = await Promise.all([api('/api/chitty/adopted'), api('/api/config')]);
  state.status = status;
  state.config = config.config || {};
  render();
  notify('Only the 31 approved net-new Chitty items are wired. Existing decision logic is unchanged.', 'success');
}

function jsonFromForm(form) {
  const raw = new FormData(form).get('payload');
  try { return JSON.parse(String(raw || '')); }
  catch (error) { throw new Error(`INVALID_JSON: ${error.message}`); }
}

async function submitSource(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  const body = {source_name: data.get('source_name'), content: data.get('content')};
  await api('/api/chitty/source/register', {method: 'POST', body: JSON.stringify(body)});
  event.currentTarget.reset();
  await load();
  notify('Source hash and duplicate lineage registered.', 'success');
}

async function submitTelemetry(event) {
  event.preventDefault();
  const body = jsonFromForm(event.currentTarget);
  await api('/api/chitty/telemetry/compute', {method: 'POST', body: JSON.stringify(body)});
  event.currentTarget.reset();
  await load();
  notify('Telemetry computed and persisted with zero decision impact.', 'success');
}

async function submitAudit(event) {
  event.preventDefault();
  const body = jsonFromForm(event.currentTarget);
  await api('/api/chitty/audit/record', {method: 'POST', body: JSON.stringify(body)});
  event.currentTarget.reset();
  await load();
  notify('Adopted audit event validated and appended.', 'success');
}

async function captureBook() {
  const book = await api('/api/paper/book');
  const config = state.config || {};
  const positions = book.open || [];
  let captured = 0;
  const rejected = [];
  for (const item of positions) {
    const body = {
      event_type: 'paper_trade_evidence',
      trade_id: item.position_id || item.trade_id || item.order_id,
      symbol: item.symbol,
      instrument_key: item.instrument_key,
      entry_time: item.opened_at || item.entry_time,
      entry_price: item.entry,
      quantity: item.qty,
      gross_pnl: item.gross_pnl,
      net_pnl: item.net_pnl,
      costs: item.costs,
      parameter_set_id: item.parameter_set_id || config.parameter_set_id,
      quote_timestamp: item.mark_asof || item.updated_at || item.opened_at,
      reason: 'CURRENT_PAPER_BOOK_CAPTURE',
    };
    try {
      await api('/api/chitty/audit/record', {method: 'POST', body: JSON.stringify(body)});
      captured += 1;
    } catch (error) {
      rejected.push(`${item.symbol || 'UNKNOWN'}: ${error.message}`);
    }
  }
  await load();
  if (!positions.length) return notify('Current paper book has no open positions to capture.', 'error');
  if (rejected.length) return notify(`Captured ${captured}; rejected ${rejected.length}. ${rejected.join(' | ')}`, 'error');
  notify(`Captured ${captured} current paper position evidence record(s).`, 'success');
}

function bind() {
  $('refresh').addEventListener('click', () => load().catch(error => notify(error.message, 'error')));
  $('source-form').addEventListener('submit', event => submitSource(event).catch(error => notify(error.message, 'error')));
  $('telemetry-form').addEventListener('submit', event => submitTelemetry(event).catch(error => notify(error.message, 'error')));
  $('audit-form').addEventListener('submit', event => submitAudit(event).catch(error => notify(error.message, 'error')));
  $('capture-book').addEventListener('click', () => captureBook().catch(error => notify(error.message, 'error')));
}

window.addEventListener('DOMContentLoaded', async () => {
  bind();
  try { await session(); await load(); }
  catch (error) { notify(error.message, 'error'); }
});
