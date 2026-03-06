#!/usr/bin/env python3
"""
Coderz k6 Runner — Web UI to configure, trigger, and monitor k6 load tests.
Streams live output via Server-Sent Events. Pushes results to Prometheus.
"""
import json, os, re, subprocess, tempfile, threading, time, uuid
from flask import Flask, request, jsonify, Response, stream_with_context

app = Flask(__name__)

# ── In-memory run store ────────────────────────────────────────────────────────
_runs = {}          # run_id -> {status, output:[], proc, summary}
_runs_lock = threading.Lock()

PROM_URL    = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
GRAFANA_URL = "http://109.199.120.120:3000"
DOTNET_BASE = "http://coderz-dotnet-api:8080"
ESHOP_BASE  = "http://eshop-publicapi:8080"

# ── HTML UI ────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Coderz — k6 Load Testing</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
     background:#0d1117;color:#e6edf3;min-height:100vh}
.wrap{max-width:1200px;margin:0 auto;padding:28px 24px}
header{display:flex;align-items:center;gap:14px;padding-bottom:24px;
       border-bottom:1px solid #21262d;margin-bottom:28px}
header h1{font-size:22px;font-weight:700;color:#f6c90e}
header p{color:#8b949e;font-size:13px;margin-top:3px}
.layout{display:grid;grid-template-columns:380px 1fr;gap:20px}
.panel{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:22px}
.panel h2{font-size:13px;font-weight:600;color:#8b949e;text-transform:uppercase;
          letter-spacing:.08em;margin-bottom:18px}
.field{margin-bottom:14px}
label{display:block;font-size:12px;color:#8b949e;margin-bottom:5px;font-weight:500}
input,select{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;
             padding:9px 12px;color:#e6edf3;font-size:13px;outline:none;
             transition:border-color .15s}
input:focus,select:focus{border-color:#f6c90e}

/* ── Scenario groups ── */
.sc-group{margin-bottom:10px}
.sc-group-label{font-size:10px;font-weight:600;letter-spacing:.08em;
                text-transform:uppercase;color:#484f58;margin-bottom:6px;
                padding-left:2px;display:flex;align-items:center;gap:6px}
.sc-group-label::after{content:'';flex:1;height:1px;background:#21262d}
.scenarios{display:grid;grid-template-columns:1fr 1fr;gap:7px}
.sc{background:#0d1117;border:1px solid #30363d;border-radius:7px;padding:10px 8px;
    cursor:pointer;text-align:center;transition:all .15s;color:#8b949e;font-size:11.5px}
.sc .icon{font-size:17px;display:block;margin-bottom:2px}
.sc.on{border-color:#f6c90e;color:#f6c90e;background:rgba(246,201,14,.07)}
.sc:hover:not(.on){border-color:#58a6ff;color:#58a6ff}
.sc.dotnet.on{border-color:#58a6ff;color:#58a6ff;background:rgba(88,166,255,.07)}
.sc.dotnet:hover:not(.on){border-color:#bc8cff;color:#bc8cff}

/* ── Quick target presets ── */
.presets{display:flex;flex-wrap:wrap;gap:5px;margin-top:4px}
.preset-btn{padding:4px 10px;background:#0d1117;border:1px solid #30363d;
            border-radius:5px;color:#8b949e;font-size:11px;cursor:pointer;
            transition:all .15s;white-space:nowrap}
.preset-btn:hover{border-color:#58a6ff;color:#58a6ff}

.btn{width:100%;padding:13px;font-size:14px;font-weight:700;border:none;
     border-radius:7px;cursor:pointer;transition:opacity .15s;margin-top:6px}
.btn-run{background:#f6c90e;color:#0d1117}
.btn-stop{background:#f85149;color:#fff;display:none}
.btn:hover{opacity:.88}
.btn:disabled{opacity:.35;cursor:not-allowed}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}
.stat{background:#0d1117;border:1px solid #30363d;border-radius:7px;
      padding:12px;text-align:center}
.stat .lbl{font-size:10px;color:#8b949e;text-transform:uppercase;
           letter-spacing:.07em;margin-bottom:5px}
.stat .val{font-size:20px;font-weight:700}
.val.green{color:#3fb950} .val.yellow{color:#f6c90e} .val.red{color:#f85149}
.val.blue{color:#58a6ff}
.prog{height:3px;background:#21262d;border-radius:2px;overflow:hidden;margin-bottom:14px}
.prog-bar{height:100%;background:#f6c90e;width:0%;transition:width .4s}
.term{background:#010409;border:1px solid #21262d;border-radius:7px;
      padding:14px;height:380px;overflow-y:auto;font-family:'JetBrains Mono',
      'Fira Code','Courier New',monospace;font-size:11.5px;line-height:1.65}
.term .ln{padding:0;white-space:pre-wrap;word-break:break-all}
.ln.info{color:#58a6ff} .ln.ok{color:#3fb950} .ln.warn{color:#f6c90e}
.ln.err{color:#f85149} .ln.dim{color:#484f58} .ln.white{color:#e6edf3}
.badge{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;
       border-radius:16px;font-size:11px;font-weight:600}
.badge.idle{background:#21262d;color:#8b949e}
.badge.running{background:rgba(246,201,14,.12);color:#f6c90e}
.badge.done{background:rgba(63,185,80,.12);color:#3fb950}
.badge.error{background:rgba(248,81,73,.12);color:#f85149}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor}
.dot.pulse{animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
.links{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.lnk{padding:7px 14px;background:#21262d;border-radius:6px;color:#e6edf3;
     text-decoration:none;font-size:12px;transition:background .15s}
.lnk:hover{background:#30363d}
.hint{font-size:11px;color:#484f58;margin-top:5px}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div style="font-size:32px">⚡</div>
    <div>
      <h1>k6 Load Testing</h1>
      <p>Configure, run, and monitor load tests against your Coderz services</p>
    </div>
    <div style="margin-left:auto">
      <span class="badge idle" id="badge">
        <span class="dot" id="dot"></span>
        <span id="badge-text">Idle</span>
      </span>
    </div>
  </header>

  <div class="layout">
    <!-- Left: Config panel -->
    <div class="panel">
      <h2>Test Configuration</h2>

      <div class="field">
        <label>Target URL</label>
        <input id="url" value="http://coderz-dotnet-api:8080/api/items"
               placeholder="http://...">
        <div class="hint">Use Docker service names for internal targets</div>
      </div>

      <div class="field">
        <label>Quick Targets</label>
        <div class="presets">
          <button class="preset-btn" onclick="setUrl('http://coderz-dotnet-api:8080/api/items')">.NET Items</button>
          <button class="preset-btn" onclick="setUrl('http://coderz-dotnet-api:8080/api/health')">.NET Health</button>
          <button class="preset-btn" onclick="setUrl('http://coderz-dotnet-api:8080/api/logs')">.NET Logs</button>
          <button class="preset-btn" onclick="setUrl('http://coderz-webapp:8080/api/users')">Webapp Users</button>
          <button class="preset-btn" onclick="setUrl('http://coderz-webapp:8080/api/products')">Webapp Products</button>
          <button class="preset-btn" onclick="setUrl('http://eshop-publicapi:8080/api/catalog-items')" style="border-color:#3fb950;color:#3fb950">eShop Catalog</button>
          <button class="preset-btn" onclick="setUrl('http://eshop-publicapi:8080/api/authenticate')" style="border-color:#3fb950;color:#3fb950">eShop Auth</button>
        </div>
      </div>

      <div class="field">
        <label>Scenario</label>

        <div class="sc-group">
          <div class="sc-group-label">Generic</div>
          <div class="scenarios">
            <div class="sc on" id="sc-constant" onclick="pickScenario('constant')">
              <span class="icon">⚡</span>Constant Load
            </div>
            <div class="sc" id="sc-rampup" onclick="pickScenario('rampup')">
              <span class="icon">📈</span>Ramp Up
            </div>
            <div class="sc" id="sc-spike" onclick="pickScenario('spike')">
              <span class="icon">🔥</span>Spike
            </div>
            <div class="sc" id="sc-stress" onclick="pickScenario('stress')">
              <span class="icon">💥</span>Stress
            </div>
          </div>
        </div>

        <div class="sc-group">
          <div class="sc-group-label">.NET API</div>
          <div class="scenarios">
            <div class="sc dotnet" id="sc-dotnet-items" onclick="pickScenario('dotnet-items')">
              <span class="icon">📦</span>Items Read
            </div>
            <div class="sc dotnet" id="sc-dotnet-crud" onclick="pickScenario('dotnet-crud')">
              <span class="icon">✏️</span>Items CRUD
            </div>
            <div class="sc dotnet" id="sc-dotnet-mixed" onclick="pickScenario('dotnet-mixed')">
              <span class="icon">🔀</span>Mixed Load
            </div>
            <div class="sc dotnet" id="sc-dotnet-stress" onclick="pickScenario('dotnet-stress')">
              <span class="icon">🏋️</span>.NET Stress
            </div>
          </div>
        </div>

        <div class="sc-group">
          <div class="sc-group-label" style="color:#3fb950">eShop API</div>
          <div class="scenarios">
            <div class="sc" id="sc-eshop-browse" onclick="pickScenario('eshop-browse')" style="border-color:#21262d">
              <span class="icon">🛍️</span>Browse
            </div>
            <div class="sc" id="sc-eshop-auth" onclick="pickScenario('eshop-auth')" style="border-color:#21262d">
              <span class="icon">🔑</span>Auth+Shop
            </div>
            <div class="sc" id="sc-eshop-cart" onclick="pickScenario('eshop-cart')" style="border-color:#21262d">
              <span class="icon">🛒</span>Cart Flow
            </div>
            <div class="sc" id="sc-eshop-order" onclick="pickScenario('eshop-order')" style="border-color:#21262d">
              <span class="icon">📦</span>Order Flow
            </div>
            <div class="sc" id="sc-eshop-stress" onclick="pickScenario('eshop-stress')" style="border-color:#21262d">
              <span class="icon">💪</span>eShop Stress
            </div>
          </div>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div class="field">
          <label>Virtual Users (VUs)</label>
          <input type="number" id="vus" value="10" min="1" max="500">
        </div>
        <div class="field">
          <label>Duration</label>
          <input id="duration" value="30s" placeholder="30s / 1m / 5m">
        </div>
      </div>

      <div class="field">
        <label>HTTP Method</label>
        <select id="method">
          <option value="GET">GET</option>
          <option value="POST">POST</option>
        </select>
      </div>

      <button class="btn btn-run" id="btn-run" onclick="runTest()">▶ Run Test</button>
      <button class="btn btn-stop" id="btn-stop" onclick="stopTest()">■ Stop</button>

      <div class="links">
        <a class="lnk" href="GRAFANA_URL/d/k6-results" target="_blank">📊 k6 Results</a>
        <a class="lnk" href="GRAFANA_URL/d/dotnet-api" target="_blank">⚙️ .NET Dashboard</a>
        <a class="lnk" href="http://109.199.120.120:5050/swagger" target="_blank">📄 API Docs</a>
        <a class="lnk" href="http://109.199.120.120:5200/swagger" target="_blank">🛍️ eShop API</a>
        <a class="lnk" href="http://109.199.120.120:5106" target="_blank">🌐 eShop Web</a>
      </div>
    </div>

    <!-- Right: Live output -->
    <div>
      <div class="stats">
        <div class="stat"><div class="lbl">Requests</div><div class="val blue" id="s-reqs">—</div></div>
        <div class="stat"><div class="lbl">Req / sec</div><div class="val yellow" id="s-rps">—</div></div>
        <div class="stat"><div class="lbl">P95 Latency</div><div class="val" id="s-p95">—</div></div>
        <div class="stat"><div class="lbl">Error Rate</div><div class="val" id="s-err">—</div></div>
      </div>

      <div class="prog"><div class="prog-bar" id="prog"></div></div>

      <div class="panel" style="padding:14px">
        <h2 style="margin-bottom:10px">Live Output</h2>
        <div class="term" id="term"></div>
      </div>
    </div>
  </div>
</div>

<script>
let scenario = 'constant';
let runId = null;
let evtSrc = null;
let startTs = null;
let totalSec = 30;
let progTimer = null;

const DOTNET = 'http://coderz-dotnet-api:8080';
const ESHOP  = 'http://eshop-publicapi:8080';

function setUrl(u) {
  document.getElementById('url').value = u;
}

function pickScenario(s) {
  scenario = s;
  document.querySelectorAll('.sc').forEach(el => el.classList.remove('on'));
  document.getElementById('sc-' + s).classList.add('on');
  const map = {
    constant:        {vus: 10,  duration: '30s', url: null},
    rampup:          {vus: 50,  duration: '1m',  url: null},
    spike:           {vus: 100, duration: '1m',  url: null},
    stress:          {vus: 200, duration: '3m',  url: null},
    'dotnet-items':  {vus: 20,  duration: '1m',  url: DOTNET + '/api/items'},
    'dotnet-crud':   {vus: 10,  duration: '2m',  url: DOTNET},
    'dotnet-mixed':  {vus: 30,  duration: '2m',  url: DOTNET},
    'dotnet-stress': {vus: 100, duration: '3m',  url: DOTNET},
    'eshop-browse':  {vus: 20,  duration: '1m',  url: ESHOP},
    'eshop-auth':    {vus: 10,  duration: '2m',  url: ESHOP},
    'eshop-cart':    {vus: 10,  duration: '2m',  url: ESHOP},
    'eshop-order':   {vus: 5,   duration: '2m',  url: ESHOP},
    'eshop-stress':  {vus: 100, duration: '3m',  url: ESHOP},
  };
  if (map[s]) {
    document.getElementById('vus').value      = map[s].vus;
    document.getElementById('duration').value = map[s].duration;
    if (map[s].url) document.getElementById('url').value = map[s].url;
  }
}

function parseSec(d) {
  const m = d.match(/^(\d+)(s|m|h)$/);
  if (!m) return 30;
  const n = parseInt(m[1]);
  return m[2]==='s' ? n : m[2]==='m' ? n*60 : n*3600;
}

function setBadge(state, text) {
  const b = document.getElementById('badge');
  const d = document.getElementById('dot');
  b.className = 'badge ' + state;
  document.getElementById('badge-text').textContent = text;
  d.className = 'dot' + (state==='running' ? ' pulse' : '');
}

function addLine(txt, cls) {
  const t = document.getElementById('term');
  const d = document.createElement('div');
  d.className = 'ln ' + (cls||'');
  d.textContent = txt;
  t.appendChild(d);
  t.scrollTop = t.scrollHeight;
}

function clearTerm() {
  document.getElementById('term').innerHTML = '';
  ['s-reqs','s-rps','s-p95','s-err'].forEach(id => {
    document.getElementById(id).textContent = '—';
    document.getElementById(id).className = 'val';
  });
  document.getElementById('prog').style.width = '0%';
}

function runTest() {
  const url      = document.getElementById('url').value.trim();
  const vus      = parseInt(document.getElementById('vus').value) || 10;
  const duration = document.getElementById('duration').value.trim() || '30s';
  const method   = document.getElementById('method').value;
  if (!url) { alert('Please enter a target URL.'); return; }

  clearTerm();
  totalSec = parseSec(duration);
  startTs  = Date.now();
  setBadge('running', 'Running');
  document.getElementById('btn-run').style.display  = 'none';
  document.getElementById('btn-stop').style.display = 'block';
  addLine(`▶ ${scenario.toUpperCase()} · ${vus} VUs · ${duration}`, 'info');
  addLine(`  Target : ${method} ${url}`, 'dim');
  addLine('─'.repeat(64), 'dim');

  fetch('/api/run', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({url, vus, duration, method, scenario})
  })
  .then(r => r.json())
  .then(d => { runId = d.run_id; stream(runId); startProgress(); })
  .catch(e => { addLine('Failed to start: ' + e, 'err'); resetUI(); });
}

function stream(id) {
  if (evtSrc) evtSrc.close();
  evtSrc = new EventSource('/api/stream/' + id);
  evtSrc.onmessage = e => {
    const d = JSON.parse(e.data);
    if (d.type === 'line') {
      const t = d.text;
      let c = 'dim';
      if (/✓|passed|PASS/.test(t))  c = 'ok';
      else if (/✗|WARN|failed/.test(t)) c = 'warn';
      else if (/ERR|error/.test(t))  c = 'err';
      else if (/default|http_req|iteration|VU|running/.test(t)) c = 'white';
      addLine(t, c);
    } else if (d.type === 'stats') {
      document.getElementById('s-reqs').textContent = d.reqs ?? '—';
      document.getElementById('s-rps').textContent  = d.rps  ? d.rps.toFixed(1)  : '—';
      const p95 = d.p95;
      document.getElementById('s-p95').textContent  =
        p95 ? (p95 >= 1000 ? (p95/1000).toFixed(2)+'s' : p95.toFixed(0)+'ms') : '—';
      const ep = d.err_pct ?? 0;
      const eEl = document.getElementById('s-err');
      eEl.textContent = ep.toFixed(1) + '%';
      eEl.className   = 'val ' + (ep>5 ? 'red' : ep>0 ? 'yellow' : 'green');
    } else if (d.type === 'done') {
      evtSrc.close();
      setBadge(d.ok ? 'done' : 'error', d.ok ? 'Done' : 'Failed');
      resetUI(false);
      document.getElementById('prog').style.width = '100%';
      addLine('─'.repeat(64), 'dim');
      addLine(d.ok ? '✓ Test completed' : '✗ Test finished with errors',
              d.ok ? 'ok' : 'err');
    }
  };
  evtSrc.onerror = () => { evtSrc.close(); resetUI(); };
}

function startProgress() {
  if (progTimer) clearInterval(progTimer);
  progTimer = setInterval(() => {
    const pct = Math.min(94, ((Date.now()-startTs)/1000 / totalSec) * 100);
    document.getElementById('prog').style.width = pct + '%';
    if ((Date.now()-startTs)/1000 > totalSec+10) clearInterval(progTimer);
  }, 400);
}

function stopTest() {
  if (!runId) return;
  fetch('/api/stop/' + runId, {method:'POST'});
  if (evtSrc) evtSrc.close();
  addLine('⚠ Stopped by user', 'warn');
  setBadge('idle', 'Stopped');
  resetUI(false);
}

function resetUI(full=true) {
  document.getElementById('btn-run').style.display  = 'block';
  document.getElementById('btn-stop').style.display = 'none';
  document.getElementById('btn-run').disabled = false;
  if (full) setBadge('idle', 'Idle');
  if (progTimer) clearInterval(progTimer);
}
</script>
</body>
</html>
""".replace("GRAFANA_URL", GRAFANA_URL)

# ── Helpers ────────────────────────────────────────────────────────────────────

def parse_sec(d: str) -> int:
    m = re.match(r'^(\d+)(s|m|h)$', d.strip())
    if not m:
        return 30
    v = int(m.group(1))
    return v if m.group(2) == 's' else v * 60 if m.group(2) == 'm' else v * 3600


def _extract_base(url: str) -> str:
    """Strip path from URL, returning scheme://host:port only."""
    m = re.match(r'(https?://[^/]+)', url)
    return m.group(1) if m else url


def build_k6_script(url: str, vus: int, duration: str, method: str, scenario: str) -> str:
    sec  = parse_sec(duration)
    ramp = max(10, sec // 5)
    base = _extract_base(url)

    # ── Stage shapes ──────────────────────────────────────────────────────────
    if scenario == "rampup":
        stages = f"""[
      {{ duration: '{ramp}s', target: 1 }},
      {{ duration: '{sec - ramp*2}s', target: {vus} }},
      {{ duration: '{ramp}s', target: 0 }},
    ]"""
    elif scenario == "spike":
        hold = max(5, sec - 30)
        stages = f"""[
      {{ duration: '10s', target: 2 }},
      {{ duration: '5s',  target: {vus} }},
      {{ duration: '{hold}s', target: {vus} }},
      {{ duration: '5s',  target: 0 }},
    ]"""
    elif scenario in ("stress", "dotnet-stress", "eshop-stress", "eshop-cart", "eshop-order"):
        step = max(1, vus // 5)
        stages = f"""[
      {{ duration: '30s', target: {step} }},
      {{ duration: '30s', target: {step*2} }},
      {{ duration: '30s', target: {step*3} }},
      {{ duration: '30s', target: {step*4} }},
      {{ duration: '30s', target: {vus} }},
      {{ duration: '30s', target: 0 }},
    ]"""
    elif scenario == "dotnet-mixed":
        stages = f"""[
      {{ duration: '{ramp}s', target: {max(1, vus // 3)} }},
      {{ duration: '{sec - ramp*2}s', target: {vus} }},
      {{ duration: '{ramp}s', target: 0 }},
    ]"""
    else:  # constant, dotnet-items, dotnet-crud, eshop-browse, eshop-auth
        stages = f"""[
      {{ duration: '{duration}', target: {vus} }},
    ]"""

    # ── .NET API — Items Read ─────────────────────────────────────────────────
    if scenario == "dotnet-items":
        return f"""import http from 'k6/http';
import {{ check, sleep }} from 'k6';

const BASE = '{base}';
const CATS = ['Electronics', 'Books', 'Sports', 'Home'];

export const options = {{
  stages: {stages},
  thresholds: {{
    http_req_failed:   ['rate<0.05'],
    http_req_duration: ['p(95)<2000'],
  }},
}};

export default function () {{
  const page = Math.floor(Math.random() * 5) + 1;
  const cat  = CATS[Math.floor(Math.random() * CATS.length)];
  const roll = Math.random();
  let res;

  if (roll < 0.40) {{
    res = http.get(BASE + '/api/items?page=' + page + '&pageSize=10');
    check(res, {{ 'list 200': r => r.status === 200 }});
  }} else if (roll < 0.70) {{
    res = http.get(BASE + '/api/items?category=' + cat + '&page=1&pageSize=5');
    check(res, {{ 'filter 200': r => r.status === 200 }});
  }} else if (roll < 0.90) {{
    const id = Math.floor(Math.random() * 20) + 1;
    res = http.get(BASE + '/api/items/' + id);
    check(res, {{ 'get ok': r => r.status === 200 || r.status === 404 }});
  }} else {{
    res = http.get(BASE + '/api/health');
    check(res, {{ 'health 200': r => r.status === 200 }});
  }}

  check(res, {{ 'response < 2s': r => r.timings.duration < 2000 }});
  sleep(0.2 + Math.random() * 0.3);
}}
"""

    # ── .NET API — Items CRUD ─────────────────────────────────────────────────
    if scenario == "dotnet-crud":
        return f"""import http from 'k6/http';
import {{ check, sleep }} from 'k6';

const BASE    = '{base}';
const HEADERS = {{ 'Content-Type': 'application/json' }};
const CATS    = ['Electronics', 'Books', 'Sports', 'Home'];

export const options = {{
  stages: {stages},
  thresholds: {{
    http_req_failed:   ['rate<0.10'],
    http_req_duration: ['p(95)<3000'],
  }},
}};

export default function () {{
  // 1. List items (GET)
  const list = http.get(BASE + '/api/items?page=1&pageSize=5');
  check(list, {{ 'list 200': r => r.status === 200 }});
  sleep(0.1);

  // 2. Create a new item (POST)
  const payload = JSON.stringify({{
    name:        'k6-item-' + Date.now(),
    description: 'Created by k6 load test',
    price:       Math.round((9.99 + Math.random() * 90) * 100) / 100,
    stock:       Math.floor(Math.random() * 100) + 1,
    category:    CATS[Math.floor(Math.random() * CATS.length)],
  }});
  const created = http.post(BASE + '/api/items', payload, {{ headers: HEADERS }});
  check(created, {{ 'create 201': r => r.status === 201 }});
  sleep(0.1);

  // 3. Read a random existing item (GET by id)
  const id = Math.floor(Math.random() * 20) + 1;
  const got = http.get(BASE + '/api/items/' + id);
  check(got, {{ 'get ok': r => r.status === 200 || r.status === 404 }});
  sleep(0.1);

  // 4. Update the item (PUT) — update stock on a seeded item
  const upd = http.put(BASE + '/api/items/' + id,
    JSON.stringify({{ stock: Math.floor(Math.random() * 200) }}),
    {{ headers: HEADERS }});
  check(upd, {{ 'update ok': r => r.status === 200 || r.status === 404 }});

  sleep(0.3 + Math.random() * 0.5);
}}
"""

    # ── .NET API — Mixed Load ─────────────────────────────────────────────────
    if scenario == "dotnet-mixed":
        return f"""import http from 'k6/http';
import {{ check, sleep }} from 'k6';

const BASE    = '{base}';
const HEADERS = {{ 'Content-Type': 'application/json' }};
const CATS    = ['Electronics', 'Books', 'Sports', 'Home'];

export const options = {{
  stages: {stages},
  thresholds: {{
    http_req_failed:   ['rate<0.05'],
    http_req_duration: ['p(95)<2500'],
  }},
}};

export default function () {{
  const roll = Math.random();

  if (roll < 0.30) {{
    // Paginated item listing
    const page = Math.floor(Math.random() * 5) + 1;
    const r = http.get(BASE + '/api/items?page=' + page + '&pageSize=10');
    check(r, {{ 'list ok': res => res.status === 200 }});

  }} else if (roll < 0.50) {{
    // Category filter
    const cat = CATS[Math.floor(Math.random() * CATS.length)];
    const r = http.get(BASE + '/api/items?category=' + cat);
    check(r, {{ 'filter ok': res => res.status === 200 }});

  }} else if (roll < 0.65) {{
    // Single item lookup
    const id = Math.floor(Math.random() * 20) + 1;
    const r = http.get(BASE + '/api/items/' + id);
    check(r, {{ 'item ok': res => res.status === 200 || res.status === 404 }});

  }} else if (roll < 0.75) {{
    // Create item
    const r = http.post(BASE + '/api/items',
      JSON.stringify({{
        name: 'mixed-' + Date.now(),
        price: Math.round(Math.random() * 100 * 100) / 100,
        stock: 50,
        category: CATS[Math.floor(Math.random() * CATS.length)],
      }}),
      {{ headers: HEADERS }});
    check(r, {{ 'create ok': res => res.status === 201 }});

  }} else if (roll < 0.90) {{
    // Request logs endpoint
    const r = http.get(BASE + '/api/logs?page=1&pageSize=10');
    check(r, {{ 'logs ok': res => res.status === 200 }});

  }} else {{
    // Health check
    const r = http.get(BASE + '/api/health');
    check(r, {{ 'health ok': res => res.status === 200 }});
  }}

  sleep(0.15 + Math.random() * 0.35);
}}
"""

    # ── .NET API — Stress ─────────────────────────────────────────────────────
    if scenario == "dotnet-stress":
        return f"""import http from 'k6/http';
import {{ check, sleep }} from 'k6';

const BASE = '{base}';
const CATS = ['Electronics', 'Books', 'Sports', 'Home'];

export const options = {{
  stages: {stages},
  thresholds: {{
    http_req_failed:   ['rate<0.20'],
    http_req_duration: ['p(95)<5000'],
  }},
}};

export default function () {{
  // Stress with mixed read load to find breaking point
  const page = Math.floor(Math.random() * 10) + 1;
  const cat  = CATS[Math.floor(Math.random() * CATS.length)];
  const roll = Math.random();
  let res;

  if (roll < 0.60) {{
    res = http.get(BASE + '/api/items?page=' + page + '&pageSize=20');
  }} else if (roll < 0.85) {{
    res = http.get(BASE + '/api/items?category=' + cat + '&page=1&pageSize=10');
  }} else {{
    res = http.get(BASE + '/api/items/' + (Math.floor(Math.random() * 20) + 1));
  }}

  check(res, {{
    'status ok':     r => r.status < 500,
    'response < 5s': r => r.timings.duration < 5000,
  }});
  sleep(0.05 + Math.random() * 0.15);
}}
"""

    # ── eShopOnWeb — Browse Catalog (public, no auth) ─────────────────────────
    if scenario == "eshop-browse":
        return f"""import http from 'k6/http';
import {{ check, sleep }} from 'k6';

const BASE = '{base}';

export const options = {{
  stages: {stages},
  thresholds: {{
    http_req_failed:   ['rate<0.05'],
    http_req_duration: ['p(95)<3000'],
  }},
}};

export default function () {{
  const roll = Math.random();

  if (roll < 0.50) {{
    // List catalog items (paged)
    const page = Math.floor(Math.random() * 5) + 1;
    const res = http.get(BASE + '/api/catalog-items?pageSize=10&pageIndex=' + page);
    check(res, {{ 'catalog list 200': r => r.status === 200 }});

  }} else if (roll < 0.75) {{
    // Get single catalog item by id
    const id = Math.floor(Math.random() * 12) + 1;
    const res = http.get(BASE + '/api/catalog-items/' + id);
    check(res, {{ 'catalog item ok': r => r.status === 200 || r.status === 404 }});

  }} else if (roll < 0.88) {{
    // Get catalog brands
    const res = http.get(BASE + '/api/catalog-brands');
    check(res, {{ 'brands 200': r => r.status === 200 }});

  }} else {{
    // Get catalog types
    const res = http.get(BASE + '/api/catalog-types');
    check(res, {{ 'types 200': r => r.status === 200 }});
  }}

  sleep(0.2 + Math.random() * 0.3);
}}
"""

    # ── eShopOnWeb — Auth + Browse ────────────────────────────────────────────
    if scenario == "eshop-auth":
        return f"""import http from 'k6/http';
import {{ check, sleep }} from 'k6';

const BASE    = '{base}';
const HEADERS = {{ 'Content-Type': 'application/json' }};

// eShopOnWeb default seeded credentials
const USERNAME = 'admin@microsoft.com';
const PASSWORD = 'Pass@word1';

export const options = {{
  stages: {stages},
  thresholds: {{
    http_req_failed:   ['rate<0.10'],
    http_req_duration: ['p(95)<4000'],
  }},
}};

export default function () {{
  // 1. Authenticate to get JWT token
  const authRes = http.post(
    BASE + '/api/authenticate',
    JSON.stringify({{ username: USERNAME, password: PASSWORD }}),
    {{ headers: HEADERS }}
  );
  check(authRes, {{ 'auth 200': r => r.status === 200 }});
  sleep(0.1);

  let token = '';
  try {{ token = JSON.parse(authRes.body).token; }} catch (e) {{}}

  if (!token) {{
    sleep(0.5);
    return;
  }}

  const authHeaders = {{
    'Content-Type':  'application/json',
    'Authorization': 'Bearer ' + token,
  }};

  // 2. Browse catalog as authenticated user
  const roll = Math.random();

  if (roll < 0.40) {{
    const page = Math.floor(Math.random() * 5) + 1;
    const res = http.get(BASE + '/api/catalog-items?pageSize=10&pageIndex=' + page,
      {{ headers: authHeaders }});
    check(res, {{ 'authed catalog 200': r => r.status === 200 }});

  }} else if (roll < 0.70) {{
    const id = Math.floor(Math.random() * 12) + 1;
    const res = http.get(BASE + '/api/catalog-items/' + id, {{ headers: authHeaders }});
    check(res, {{ 'authed item ok': r => r.status === 200 || r.status === 404 }});

  }} else {{
    const res = http.get(BASE + '/api/catalog-brands', {{ headers: authHeaders }});
    check(res, {{ 'authed brands 200': r => r.status === 200 }});
  }}

  sleep(0.3 + Math.random() * 0.4);
}}
"""

    # ── eShop — Cart Flow (login → add items → view cart) ────────────────────
    if scenario == "eshop-cart":
        return f"""import http from 'k6/http';
import {{ check, sleep }} from 'k6';

const BASE    = '{base}';
const HEADERS = {{ 'Content-Type': 'application/json' }};

export const options = {{
  stages: {stages},
  thresholds: {{
    http_req_failed:   ['rate<0.10'],
    http_req_duration: ['p(95)<4000'],
  }},
}};

export default function () {{
  // 1. Login
  const authRes = http.post(BASE + '/api/authenticate',
    JSON.stringify({{ username: 'admin@microsoft.com', password: 'Pass@word1' }}),
    {{ headers: HEADERS }});
  check(authRes, {{ 'auth 200': r => r.status === 200 }});
  sleep(0.1);

  let token = '';
  try {{ token = JSON.parse(authRes.body).token; }} catch (e) {{}}
  if (!token) {{ sleep(0.5); return; }}

  const authHeaders = {{ ...HEADERS, 'Authorization': 'Bearer ' + token }};

  // 2. Browse catalog
  const itemId = Math.floor(Math.random() * 12) + 1;
  const catalog = http.get(BASE + '/api/catalog-items/' + itemId);
  check(catalog, {{ 'item ok': r => r.status === 200 || r.status === 404 }});
  sleep(0.2);

  // 3. Add item to cart
  const addRes = http.post(BASE + '/api/cart/items',
    JSON.stringify({{ catalogItemId: itemId, quantity: Math.floor(Math.random() * 3) + 1 }}),
    {{ headers: authHeaders }});
  check(addRes, {{ 'add to cart ok': r => r.status === 200 || r.status === 404 }});
  sleep(0.2);

  // 4. View cart
  const cartRes = http.get(BASE + '/api/cart', {{ headers: authHeaders }});
  check(cartRes, {{ 'get cart 200': r => r.status === 200 }});

  sleep(0.3 + Math.random() * 0.4);
}}
"""

    # ── eShop — Order Flow (login → browse → add to cart → checkout) ─────────
    if scenario == "eshop-order":
        return f"""import http from 'k6/http';
import {{ check, sleep }} from 'k6';

const BASE    = '{base}';
const HEADERS = {{ 'Content-Type': 'application/json' }};

export const options = {{
  stages: {stages},
  thresholds: {{
    http_req_failed:   ['rate<0.15'],
    http_req_duration: ['p(95)<5000'],
  }},
}};

export default function () {{
  // 1. Login
  const authRes = http.post(BASE + '/api/authenticate',
    JSON.stringify({{ username: 'admin@microsoft.com', password: 'Pass@word1' }}),
    {{ headers: HEADERS }});
  check(authRes, {{ 'auth 200': r => r.status === 200 }});
  sleep(0.1);

  let token = '';
  try {{ token = JSON.parse(authRes.body).token; }} catch (e) {{}}
  if (!token) {{ sleep(0.5); return; }}

  const authHeaders = {{ ...HEADERS, 'Authorization': 'Bearer ' + token }};

  // 2. Add a random item to cart
  const itemId = Math.floor(Math.random() * 12) + 1;
  const addRes = http.post(BASE + '/api/cart/items',
    JSON.stringify({{ catalogItemId: itemId, quantity: 1 }}),
    {{ headers: authHeaders }});
  check(addRes, {{ 'add item ok': r => r.status === 200 || r.status === 404 }});
  sleep(0.3);

  // 3. Checkout
  const orderRes = http.post(BASE + '/api/orders/checkout', '{{}}', {{ headers: authHeaders }});
  check(orderRes, {{ 'checkout ok': r => r.status === 201 || r.status === 400 }});
  sleep(0.2);

  // 4. List orders
  const ordersRes = http.get(BASE + '/api/orders', {{ headers: authHeaders }});
  check(ordersRes, {{ 'orders list 200': r => r.status === 200 }});

  sleep(0.5 + Math.random() * 0.5);
}}
"""

    # ── eShopOnWeb — Stress ───────────────────────────────────────────────────
    if scenario == "eshop-stress":
        return f"""import http from 'k6/http';
import {{ check, sleep }} from 'k6';

const BASE = '{base}';

export const options = {{
  stages: {stages},
  thresholds: {{
    http_req_failed:   ['rate<0.20'],
    http_req_duration: ['p(95)<5000'],
  }},
}};

export default function () {{
  const roll = Math.random();
  let res;

  if (roll < 0.55) {{
    const page = Math.floor(Math.random() * 10) + 1;
    res = http.get(BASE + '/api/catalog-items?pageSize=10&pageIndex=' + page);
  }} else if (roll < 0.80) {{
    const id = Math.floor(Math.random() * 12) + 1;
    res = http.get(BASE + '/api/catalog-items/' + id);
  }} else if (roll < 0.90) {{
    res = http.get(BASE + '/api/catalog-brands');
  }} else {{
    res = http.get(BASE + '/api/catalog-types');
  }}

  check(res, {{
    'status ok':     r => r.status < 500,
    'response < 5s': r => r.timings.duration < 5000,
  }});
  sleep(0.05 + Math.random() * 0.1);
}}
"""

    # ── Generic scenarios ─────────────────────────────────────────────────────
    if method == "POST":
        call = f"""  const payload = JSON.stringify({{ items: [{{ product_id: 1, qty: 2 }}] }});
  const params  = {{ headers: {{ 'Content-Type': 'application/json' }} }};
  const res = http.post('{url}', payload, params);"""
    else:
        call = f"  const res = http.get('{url}');"

    return f"""import http from 'k6/http';
import {{ check, sleep }} from 'k6';

export const options = {{
  stages: {stages},
  thresholds: {{
    http_req_failed:   ['rate<0.15'],
    http_req_duration: ['p(95)<3000'],
  }},
}};

export default function () {{
{call}
  check(res, {{
    'status 2xx':    r => r.status >= 200 && r.status < 300,
    'response < 3s': r => r.timings.duration < 3000,
  }});
  sleep(0.1 + Math.random() * 0.4);
}}
"""

# ── Background runner ──────────────────────────────────────────────────────────

def _run(run_id, url, vus, duration, method, scenario):
    def push(event):
        with _runs_lock:
            _runs[run_id]["output"].append(event)

    script = build_k6_script(url, vus, duration, method, scenario)
    fd, script_path = tempfile.mkstemp(suffix=".js", prefix="k6-")
    _, summary_path = tempfile.mkstemp(suffix=".json", prefix="k6-summary-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(script)

        env = {
            **os.environ,
            "K6_PROMETHEUS_RW_SERVER_URL": f"{PROM_URL}/api/v1/write",
            "K6_PROMETHEUS_RW_TREND_STATS": "p(50),p(90),p(95),p(99)",
            "K6_PROMETHEUS_RW_PUSH_INTERVAL": "5s",
        }
        cmd = [
            "k6", "run",
            "--out", "experimental-prometheus-rw",
            "--summary-export", summary_path,
            script_path
        ]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, bufsize=1
        )
        with _runs_lock:
            _runs[run_id]["proc"] = proc

        for line in proc.stdout:
            push({"type": "line", "text": line.rstrip()})

        proc.wait()
        success = proc.returncode == 0

        # Parse summary
        stats = {}
        try:
            with open(summary_path) as f:
                s = json.load(f)
            m = s.get("metrics", {})
            reqs  = m.get("http_reqs", {})
            dur   = m.get("http_req_duration", {})
            fails = m.get("http_req_failed", {})
            vals  = dur.get("values", {})
            stats = {
                "reqs":    int(reqs.get("count", 0)),
                "rps":     round(reqs.get("rate", 0), 2),
                "p95":     round(vals.get("p(95)", 0), 2),
                "avg":     round(vals.get("avg",   0), 2),
                "err_pct": round(fails.get("values", {}).get("rate", 0) * 100, 2),
            }
        except Exception:
            pass

        push({"type": "stats", **stats})
        push({"type": "done", "ok": success})

        with _runs_lock:
            _runs[run_id]["status"]  = "done" if success else "error"
            _runs[run_id]["summary"] = stats

    except Exception as exc:
        push({"type": "line", "text": f"Runner error: {exc}"})
        push({"type": "done", "ok": False})
        with _runs_lock:
            _runs[run_id]["status"] = "error"
    finally:
        for p in (script_path, summary_path):
            try:
                os.unlink(p)
            except Exception:
                pass

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.post("/api/run")
def api_run():
    data     = request.get_json(force=True)
    run_id   = uuid.uuid4().hex[:8]
    with _runs_lock:
        _runs[run_id] = {"status": "running", "output": [], "proc": None, "summary": {}}
    threading.Thread(
        target=_run,
        args=(run_id, data["url"], int(data.get("vus", 10)),
              data.get("duration", "30s"), data.get("method", "GET"),
              data.get("scenario", "constant")),
        daemon=True
    ).start()
    return jsonify({"run_id": run_id})


@app.get("/api/stream/<run_id>")
def api_stream(run_id):
    def generate():
        idx = 0
        while True:
            with _runs_lock:
                run      = _runs.get(run_id)
                if not run:
                    break
                chunk    = run["output"][idx:]
                idx     += len(chunk)
                finished = run["status"] in ("done", "error", "stopped")

            for evt in chunk:
                yield f"data: {json.dumps(evt)}\n\n"

            if finished and not chunk:
                break
            time.sleep(0.15)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.post("/api/stop/<run_id>")
def api_stop(run_id):
    with _runs_lock:
        run = _runs.get(run_id)
        if run and run.get("proc"):
            run["proc"].terminate()
            run["status"] = "stopped"
    return jsonify({"ok": True})


@app.get("/health")
def health():
    return jsonify({"status": "ok", "k6": "ready"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000, debug=False, threaded=True)
