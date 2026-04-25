"""Routing dashboard — single-page web UI served at /dashboard.

No build step, no npm. Inline HTML/JS/CSS using:
  - Tailwind CSS (CDN) for styling
  - Chart.js (CDN) for routing pie chart and timeline
  - Vanilla JS fetch() polling every 5 seconds

Data sources: /stats, /routing/history, /routing/summary, /perf,
              /sessions, /sessions/summary, /health, /config
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MLX Task Router — Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  body { font-family: 'Inter', system-ui, -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 1.25rem; }
  .badge-local { background: #065f46; color: #6ee7b7; }
  .badge-forward { background: #7c2d12; color: #fdba74; }
  .badge-cache { background: #1e3a5f; color: #93c5fd; }
  .pulse { animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
  .stat-value { font-size: 2rem; font-weight: 700; line-height: 1.1; }
  .stat-label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #94a3b8; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; padding: 0.5rem; border-bottom: 1px solid #334155; }
  td { padding: 0.5rem; border-bottom: 1px solid #1e293b; font-size: 0.85rem; }
  tr:hover { background: #1e293b80; }
  .session-card { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 1rem; margin-bottom: 0.5rem; cursor: pointer; transition: border-color 0.15s; }
  .session-card:hover { border-color: #6366f1; }
  .session-detail { display: none; margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px solid #334155; }
  .session-card.open .session-detail { display: block; }
  ::-webkit-scrollbar { width: 6px; } ::-webkit-scrollbar-track { background: #0f172a; } ::-webkit-scrollbar-thumb { background: #475569; border-radius: 3px; }
</style>
</head>
<body class="min-h-screen p-4 md:p-6">

<!-- Header -->
<div class="flex items-center justify-between mb-6">
  <div>
    <h1 class="text-2xl font-bold text-white flex items-center gap-2">
      <span class="text-3xl">⚡</span> MLX Task Router
      <span id="version" class="text-sm font-normal text-slate-400 ml-2"></span>
    </h1>
    <p class="text-sm text-slate-400 mt-1">
      Model: <span id="model-name" class="text-indigo-400 font-mono">—</span>
      <span id="health-badge" class="ml-3 inline-block px-2 py-0.5 rounded text-xs font-medium">—</span>
    </p>
  </div>
  <div class="text-right text-xs text-slate-500">
    <div>Auto-refresh: <span id="refresh-indicator" class="text-green-400 pulse">●</span> 5s</div>
    <div id="last-updated"></div>
  </div>
</div>

<!-- Summary Cards -->
<div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 mb-6">
  <div class="card">
    <div class="stat-label">Total Requests</div>
    <div class="stat-value text-white" id="stat-total">0</div>
  </div>
  <div class="card">
    <div class="stat-label">Local %</div>
    <div class="stat-value text-emerald-400" id="stat-local-pct">0%</div>
  </div>
  <div class="card">
    <div class="stat-label">Cost Saved</div>
    <div class="stat-value text-green-400" id="stat-saved">$0</div>
  </div>
  <div class="card">
    <div class="stat-label">Local tok/s</div>
    <div class="stat-value text-sky-400" id="stat-tps">0</div>
  </div>
  <div class="card">
    <div class="stat-label">P50 Latency</div>
    <div class="stat-value text-amber-400" id="stat-p50">0ms</div>
  </div>
  <div class="card">
    <div class="stat-label">Sessions</div>
    <div class="stat-value text-violet-400" id="stat-sessions">0</div>
  </div>
</div>

<!-- Charts + Recent Decisions -->
<div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
  <!-- Routing Pie -->
  <div class="card">
    <h2 class="text-sm font-semibold text-slate-300 mb-3">Routing Distribution</h2>
    <div class="flex justify-center" style="height:220px">
      <canvas id="routing-chart"></canvas>
    </div>
    <div class="flex justify-center gap-4 mt-3 text-xs">
      <span class="flex items-center gap-1"><span class="w-2.5 h-2.5 rounded-full bg-emerald-500 inline-block"></span> Local</span>
      <span class="flex items-center gap-1"><span class="w-2.5 h-2.5 rounded-full bg-orange-500 inline-block"></span> Forward</span>
      <span class="flex items-center gap-1"><span class="w-2.5 h-2.5 rounded-full bg-sky-500 inline-block"></span> Cache</span>
    </div>
  </div>

  <!-- Performance -->
  <div class="card">
    <h2 class="text-sm font-semibold text-slate-300 mb-3">Performance</h2>
    <div class="space-y-3 text-sm">
      <div class="flex justify-between"><span class="text-slate-400">Routing avg</span><span id="perf-routing" class="font-mono text-white">—</span></div>
      <div class="flex justify-between"><span class="text-slate-400">Local gen avg</span><span id="perf-gen" class="font-mono text-white">—</span></div>
      <div class="flex justify-between"><span class="text-slate-400">Forward avg</span><span id="perf-fwd" class="font-mono text-white">—</span></div>
      <div class="flex justify-between"><span class="text-slate-400">P95 latency</span><span id="perf-p95" class="font-mono text-amber-400">—</span></div>
      <div class="flex justify-between"><span class="text-slate-400">P99 latency</span><span id="perf-p99" class="font-mono text-red-400">—</span></div>
      <div class="flex justify-between"><span class="text-slate-400">Requests / hour</span><span id="perf-rph" class="font-mono text-white">—</span></div>
    </div>
    <h2 class="text-sm font-semibold text-slate-300 mt-5 mb-3">Config</h2>
    <div class="space-y-1 text-xs text-slate-400" id="config-section">—</div>
  </div>

  <!-- Recent Decisions -->
  <div class="card overflow-auto" style="max-height: 400px">
    <h2 class="text-sm font-semibold text-slate-300 mb-3">Recent Routing Decisions</h2>
    <table>
      <thead><tr><th>Time</th><th>Route</th><th>Score</th><th>Trigger</th><th>Preview</th></tr></thead>
      <tbody id="decisions-body"></tbody>
    </table>
  </div>
</div>

<!-- Sessions -->
<div class="card mb-6">
  <div class="flex items-center justify-between mb-3">
    <h2 class="text-sm font-semibold text-slate-300">Sessions</h2>
    <span class="text-xs text-slate-500" id="session-summary-text">—</span>
  </div>
  <div id="sessions-container" class="space-y-2 max-h-96 overflow-auto"></div>
</div>

<div class="text-center text-xs text-slate-600 pb-4">
  MLX Task Router — <a href="https://github.com/sealmindset/mlx-task-router" class="text-indigo-500 hover:underline">GitHub</a>
</div>

<script>
const BASE = window.location.origin;
let routingChart = null;

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function badge(route) {
  const cls = route === 'local' ? 'badge-local' : route === 'cache' ? 'badge-cache' : 'badge-forward';
  return `<span class="px-1.5 py-0.5 rounded text-xs font-medium ${cls}">${route}</span>`;
}

function initChart() {
  const ctx = document.getElementById('routing-chart').getContext('2d');
  routingChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Local', 'Forward', 'Cache'],
      datasets: [{
        data: [0, 0, 0],
        backgroundColor: ['#10b981', '#f97316', '#3b82f6'],
        borderColor: '#1e293b',
        borderWidth: 3,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '65%',
      plugins: { legend: { display: false } },
      animation: { duration: 400 },
    }
  });
}

async function fetchJSON(path) {
  try { const r = await fetch(BASE + path); return await r.json(); }
  catch { return null; }
}

async function refresh() {
  const [root, stats, perf, history, sessionsSummary, sessions, health, cfg] = await Promise.all([
    fetchJSON('/'),
    fetchJSON('/stats'),
    fetchJSON('/perf'),
    fetchJSON('/routing/history?limit=30'),
    fetchJSON('/sessions/summary'),
    fetchJSON('/sessions?limit=10'),
    fetchJSON('/health'),
    fetchJSON('/config'),
  ]);

  // Header
  if (root) {
    document.getElementById('version').textContent = `v${root.version}`;
    document.getElementById('model-name').textContent = root.model || 'none';
  }
  if (health) {
    const hb = document.getElementById('health-badge');
    hb.textContent = health.status;
    hb.className = `ml-3 inline-block px-2 py-0.5 rounded text-xs font-medium ${health.status === 'healthy' ? 'bg-emerald-900 text-emerald-400' : 'bg-red-900 text-red-400'}`;
  }

  // Stats cards
  if (stats) {
    document.getElementById('stat-total').textContent = stats.requests_total.toLocaleString();
    document.getElementById('stat-local-pct').textContent = stats.local_percentage + '%';
    document.getElementById('stat-saved').textContent = stats.cost_saved_display;
  }
  if (perf) {
    document.getElementById('stat-tps').textContent = perf.local_tokens_per_sec || '—';
    document.getElementById('stat-p50').textContent = perf.latency_p50_ms ? perf.latency_p50_ms + 'ms' : '—';
    document.getElementById('perf-routing').textContent = perf.routing_avg_ms ? perf.routing_avg_ms + 'ms' : '—';
    document.getElementById('perf-gen').textContent = perf.local_avg_generation_ms ? perf.local_avg_generation_ms + 'ms' : '—';
    document.getElementById('perf-fwd').textContent = perf.forward_avg_latency_ms ? perf.forward_avg_latency_ms + 'ms' : '—';
    document.getElementById('perf-p95').textContent = perf.latency_p95_ms ? perf.latency_p95_ms + 'ms' : '—';
    document.getElementById('perf-p99').textContent = perf.latency_p99_ms ? perf.latency_p99_ms + 'ms' : '—';
    document.getElementById('perf-rph').textContent = perf.requests_last_hour || '0';

    // Chart
    if (routingChart) {
      routingChart.data.datasets[0].data = [perf.local_count || 0, perf.forward_count || 0, perf.cache_count || 0];
      routingChart.update();
    }
  }
  if (sessionsSummary) {
    document.getElementById('stat-sessions').textContent = sessionsSummary.total_sessions;
    document.getElementById('session-summary-text').textContent =
      `${sessionsSummary.total_sessions} total, ${sessionsSummary.active_sessions} active` +
      (sessionsSummary.current_session ? ` — current: ${sessionsSummary.current_session}` : '');
  }

  // Config
  if (cfg) {
    document.getElementById('config-section').innerHTML = [
      `<div class="flex justify-between"><span>Temperature</span><span class="font-mono text-white">${cfg.temperature}</span></div>`,
      `<div class="flex justify-between"><span>Top-P</span><span class="font-mono text-white">${cfg.top_p}</span></div>`,
      `<div class="flex justify-between"><span>Top-K</span><span class="font-mono text-white">${cfg.top_k}</span></div>`,
      `<div class="flex justify-between"><span>Max tokens</span><span class="font-mono text-white">${cfg.model_max_tokens}</span></div>`,
      `<div class="flex justify-between"><span>Context limit</span><span class="font-mono text-white">${cfg.max_local_context_tokens}</span></div>`,
      `<div class="flex justify-between"><span>Threshold</span><span class="font-mono text-white">${cfg.routing_threshold}</span></div>`,
    ].join('');
  }

  // Recent decisions
  if (history && Array.isArray(history)) {
    const tbody = document.getElementById('decisions-body');
    tbody.innerHTML = history.map(d => `<tr>
      <td class="text-slate-400 text-xs font-mono whitespace-nowrap">${fmtTime(d.timestamp)}</td>
      <td>${badge(d.route)}</td>
      <td class="font-mono text-xs">${d.forward_score.toFixed(2)}</td>
      <td class="text-xs text-slate-300">${d.trigger || '—'}</td>
      <td class="text-xs text-slate-500 max-w-xs truncate">${d.message_preview}</td>
    </tr>`).join('');
  }

  // Sessions
  if (sessions && Array.isArray(sessions)) {
    const container = document.getElementById('sessions-container');
    if (sessions.length === 0) {
      container.innerHTML = '<p class="text-sm text-slate-500">No sessions yet</p>';
    } else {
      container.innerHTML = sessions.map(s => {
        const dur = s.duration_seconds < 60 ? `${s.duration_seconds}s` : `${Math.round(s.duration_seconds / 60)}m`;
        const triggersHtml = Object.entries(s.top_triggers || {}).slice(0, 5)
          .map(([k, v]) => `<span class="inline-block px-1.5 py-0.5 bg-slate-700 rounded text-xs mr-1 mb-1">${k} <span class="text-slate-400">×${v}</span></span>`)
          .join('');
        const decisionsHtml = (s.recent_decisions || []).slice(-10).reverse()
          .map(d => `<tr>
            <td class="text-xs font-mono text-slate-400">${fmtTime(d.timestamp)}</td>
            <td>${badge(d.route)}</td>
            <td class="text-xs font-mono">${d.forward_score.toFixed(2)}</td>
            <td class="text-xs text-slate-500 truncate max-w-xs">${d.message_preview}</td>
          </tr>`).join('');
        return `<div class="session-card" onclick="this.classList.toggle('open')">
          <div class="flex items-center justify-between">
            <div>
              <span class="font-mono text-sm text-indigo-400">${s.session_id}</span>
              <span class="text-xs text-slate-500 ml-2">${dur}</span>
            </div>
            <div class="flex items-center gap-3 text-xs">
              <span class="text-emerald-400">${s.requests_local} local</span>
              <span class="text-orange-400">${s.requests_forwarded} fwd</span>
              <span class="text-sky-400">${s.requests_cache} cache</span>
              <span class="text-green-400">${s.cost_saved_display}</span>
              <span class="text-slate-500">${s.requests_total} total</span>
            </div>
          </div>
          <div class="session-detail">
            <div class="mb-2">${triggersHtml || '<span class="text-xs text-slate-500">No triggers</span>'}</div>
            <table><thead><tr><th>Time</th><th>Route</th><th>Score</th><th>Preview</th></tr></thead>
            <tbody>${decisionsHtml || '<tr><td colspan="4" class="text-xs text-slate-500">No decisions</td></tr>'}</tbody></table>
          </div>
        </div>`;
      }).join('');
    }
  }

  document.getElementById('last-updated').textContent = 'Updated: ' + new Date().toLocaleTimeString();
}

document.addEventListener('DOMContentLoaded', () => {
  initChart();
  refresh();
  setInterval(refresh, 5000);
});
</script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML
