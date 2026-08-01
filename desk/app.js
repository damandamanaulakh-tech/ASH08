'use strict';

const state = { csrf: '', authMode: '', health: null, book: null, scan: null, core: null };
const $ = (id) => document.getElementById(id);
const money = (value) => Number.isFinite(Number(value)) ? new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:2}).format(Number(value)) : '—';
const num = (value, digits=2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
const text = (id, value) => { $(id).textContent = value == null || value === '' ? '—' : String(value); };

function notify(message, kind='') {
  const node = $('notice');
  node.textContent = message;
  node.className = `notice ${kind}`.trim();
}

function token() { return localStorage.getItem('ash08_api_token') || ''; }
function idempotencyKey(prefix='manual') { return `${prefix}:${crypto.randomUUID()}`; }

async function api(path, options={}) {
  const headers = new Headers(options.headers || {});
  const bearer = token();
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`);
  if (state.csrf) headers.set('X-CSRF-Token', state.csrf);
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
  const response = await fetch(path, {...options, headers, credentials:'same-origin'});
  let payload;
  try { payload = await response.json(); } catch { payload = {ok:false,error:{code:'INVALID_RESPONSE',message:'Server returned non-JSON'}}; }
  if (!response.ok) {
    const error = payload.error || {};
    throw new Error(`${error.code || response.status}: ${error.message || 'Request failed'}`);
  }
  return payload;
}

async function session() {
  const payload = await api('/api/session');
  state.csrf = payload.csrf_token || '';
  state.authMode = payload.auth_mode || '';
  text('auth-status', payload.auth_mode === 'bearer' ? 'BEARER TOKEN' : 'SAME-ORIGIN SESSION');
}

function clearTable(id) { const body=$(id); while(body.firstChild) body.removeChild(body.firstChild); return body; }
function td(value, className='') { const cell=document.createElement('td'); cell.textContent=value == null ? '—' : String(value); if(className) cell.className=className; return cell; }
function emptyRow(body, columns, message) { const row=document.createElement('tr'); const cell=td(message); cell.colSpan=columns; cell.className='muted'; row.appendChild(cell); body.appendChild(row); }

function renderHealth() {
  const health=state.health || {}; const config=health.config || {}; const provider=health.provider || {};
  text('release', health.release); text('book-value', money(config.book_value));
  text('scanner-value', `SELECT ≥ ${config.scanner?.score_select ?? '—'} / WATCH ≥ ${config.scanner?.score_watch ?? '—'}`);
  text('profile-state', provider.profile_ok ? 'OK' : (provider.token_set ? 'FAILED' : 'TOKEN NOT SET'));
  text('quote-state', provider.quote_ok ? `OK · ${provider.last_quote_at || ''}` : `NOT VERIFIED${provider.last_error ? ` · ${provider.last_error}` : ''}`);
  const market=$('market-status'); market.textContent=provider.quote_ok ? 'MARKET VERIFIED' : 'MARKET UNAVAILABLE'; market.className=`badge ${provider.quote_ok?'good':'warn'}`;
}

function renderCore() {
  const core=state.core || {}; text('core-state', `${core.status || 'MISSING'} · ${core.count || 0}`);
  text('source-state', core.meta?.source || core.source || '—');
}

function renderScan() {
  const scan=state.scan || {}; text('scan-asof', scan.asof || 'Never');
  const body=clearTable('scan-table'); const rows=scan.rows || [];
  if(!rows.length) return emptyRow(body,5,'No scan rows. Core may be blocked until real feature evidence exists.');
  rows.slice(0,250).forEach(item=>{ const row=document.createElement('tr'); row.append(td(item.symbol)); row.append(td(item.decision,`decision ${item.decision}`)); row.append(td(item.score==null?'—':num(item.score))); row.append(td(item.coverage==null?'—':`${Math.round(Number(item.coverage)*100)}%`)); row.append(td(item.reason)); body.appendChild(row); });
}

function renderBook() {
  const book=state.book || {};
  text('cash-value', money(book.cash)); text('cash-note', `Equity ${money(book.equity)}`);
  text('gross-value', money(book.gross_exposure)); text('gross-note', `Target ${money(book.target_gross_exposure)}`);
  text('open-value', book.open_count ?? 0); text('open-note', `Maximum ${book.plan?.max_open ?? '—'}`);
  text('pnl-value', money(book.total_pnl));
  const gov=book.governor || {}; text('governor-status', gov.verified ? `GOV ${gov.level}` : 'GOV UNVERIFIED'); text('gov-level', gov.verified ? gov.level : 'NO VERIFIED EVIDENCE'); text('gov-target', `${gov.exposure_pct ?? '—'}% · ${money(book.target_gross_exposure)}`); text('gov-cut', money(book.required_governor_cut));

  const positions=clearTable('positions-table'); const opens=book.open || [];
  if(!opens.length) emptyRow(positions,9,'No open positions');
  opens.forEach(item=>{ const row=document.createElement('tr'); row.append(td(item.symbol)); row.append(td(item.qty)); row.append(td(money(item.entry))); row.append(td(money(item.ltp))); row.append(td(money(item.net_pnl))); row.append(td(money(item.stop))); row.append(td(money(item.target))); row.append(td(item.mark_status)); const action=document.createElement('td'); const button=document.createElement('button'); button.textContent='Close'; button.addEventListener('click',()=>closePosition(item.symbol)); action.appendChild(button); row.appendChild(action); positions.appendChild(row); });

  const orders=clearTable('orders-table'); const orderRows=book.orders || [];
  if(!orderRows.length) emptyRow(orders,6,'No orders');
  orderRows.forEach(item=>{ const row=document.createElement('tr'); row.append(td(item.created_at)); row.append(td(item.symbol)); row.append(td(item.requested_qty)); row.append(td(item.qty)); row.append(td(item.status)); row.append(td(item.reason)); orders.appendChild(row); });

  const closed=clearTable('closed-table'); const closedRows=book.closed || [];
  if(!closedRows.length) emptyRow(closed,6,'No closed positions');
  closedRows.forEach(item=>{ const row=document.createElement('tr'); row.append(td(item.symbol)); row.append(td(item.qty)); row.append(td(money(item.entry))); row.append(td(money(item.exit_price))); row.append(td(money(item.net_pnl))); row.append(td(item.exit_reason)); closed.appendChild(row); });
}

async function loadAll() {
  const [health,book,scan,core]=await Promise.all([api('/api/health'),api('/api/paper/book'),api('/api/scan/latest'),api('/api/universe/core')]);
  state.health=health; state.book=book; state.scan=scan; state.core=core; renderHealth(); renderBook(); renderScan(); renderCore(); notify('Reviewed ₹50 lakh baseline loaded.','success');
}

async function mutate(label, path, body={}, headers={}) {
  notify(`${label}…`);
  try { const result=await api(path,{method:'POST',body:JSON.stringify(body),headers}); await loadAll(); notify(`${label} completed.`, 'success'); return result; }
  catch(error){ notify(error.message,'error'); throw error; }
}

async function refreshUniverse(){ await mutate('Refreshing instrument master','/api/universe/refresh',{}); }
async function runScan(){ await mutate('Running strict scan','/api/scan/run',{bucket:'core'}); }
async function refreshPnl(){ await mutate('Fetching live marks','/api/pnl/tick',{}); }
async function autoBuy(){ await mutate('Auto-buying fresh SELECT rows','/api/paper/auto',{}); }
async function closePosition(symbol){ const value=prompt(`Executable close price for ${symbol}. Leave blank to use Upstox.`,''); const body={symbol}; if(value) body.price=Number(value); await mutate(`Closing ${symbol}`,'/api/positions/close',body); }

async function buy(event){
  event.preventDefault(); const data=new FormData(event.currentTarget); const body={symbol:data.get('symbol'),qty:Number(data.get('qty')),hold_sessions:Number(data.get('hold_sessions'))}; ['price','stop','target'].forEach(key=>{const value=data.get(key); if(value!=='') body[key]=Number(value);});
  await mutate('Submitting risk-sized order','/api/paper/buy',body,{'Idempotency-Key':idempotencyKey()});
}

async function evaluateGovernor(event){ event.preventDefault(); const data=new FormData(event.currentTarget); const body={}; ['damage','q10','sell','any_fii','evidence_complete','evidence_fresh'].forEach(key=>body[key]=data.get(key)==='on'); body.evidence_asof=new Date().toISOString(); await mutate('Evaluating governor','/api/governor/evaluate',body); }

function bind(){
  $('refresh-all').addEventListener('click',()=>loadAll().catch(error=>notify(error.message,'error')));
  $('refresh-universe').addEventListener('click',()=>refreshUniverse().catch(()=>{})); $('run-scan').addEventListener('click',()=>runScan().catch(()=>{})); $('refresh-pnl').addEventListener('click',()=>refreshPnl().catch(()=>{})); $('auto-buy').addEventListener('click',()=>autoBuy().catch(()=>{}));
  $('buy-form').addEventListener('submit',event=>buy(event).catch(()=>{})); $('governor-form').addEventListener('submit',event=>evaluateGovernor(event).catch(()=>{}));
  $('api-token').value=token(); $('save-token').addEventListener('click',()=>{localStorage.setItem('ash08_api_token',$('api-token').value.trim());notify('Bearer token saved in this browser.','success');}); $('clear-token').addEventListener('click',()=>{localStorage.removeItem('ash08_api_token');$('api-token').value='';notify('Bearer token cleared.','success');});
}

window.addEventListener('DOMContentLoaded',async()=>{ bind(); try{await session();await loadAll();}catch(error){notify(error.message,'error');} });
