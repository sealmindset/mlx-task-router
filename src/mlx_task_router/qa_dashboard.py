"""QA Evidence Dashboard — visual proof of routing quality assurance.

Serves at /qa/dashboard. Shows overall quality score, per-category trust,
gate hit rate, cost analysis, and quality trends.
"""

from __future__ import annotations

from fastapi.responses import HTMLResponse

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MLX Task Router — Quality Assurance</title>
<script src="https://cdn.tailwindcss.com?plugins=typography"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  body { background: #0f172a; color: #e2e8f0; font-family: 'Inter', system-ui, sans-serif; }
  .card { background: #1e293b; border-radius: 0.75rem; padding: 1rem; border: 1px solid #334155; }
  .stat-value { font-size: 1.75rem; font-weight: 700; }
  .stat-label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }
  .pulse { animation: pulse 2s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
  .badge { display: inline-block; padding: 0.125rem 0.5rem; border-radius: 9999px; font-size: 0.7rem; font-weight: 600; }
  .badge-proven { background: #065f46; color: #6ee7b7; }
  .badge-trusted { background: #1e40af; color: #93c5fd; }
  .badge-building { background: #78350f; color: #fcd34d; }
  .badge-unproven { background: #334155; color: #94a3b8; }
  .badge-degraded { background: #7f1d1d; color: #fca5a5; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 0.5rem; color: #94a3b8; font-size: 0.75rem; border-bottom: 1px solid #334155; }
  td { padding: 0.5rem; font-size: 0.8rem; border-bottom: 1px solid #1e293b; }
</style>
</head>
<body class="min-h-screen p-4 md:p-6 max-w-7xl mx-auto">

<!-- Header -->
<div class="flex items-center justify-between mb-6">
  <div>
    <h1 class="text-xl font-bold">Quality Assurance Dashboard</h1>
    <p class="text-xs text-slate-400 mt-1">
      MLX Task Router <span id="version" class="text-slate-500">v?</span>
      <span id="health-badge" class="ml-3 inline-block px-2 py-0.5 rounded text-xs font-medium">—</span>
    </p>
  </div>
  <div class="text-right text-xs text-slate-500">
    <div>Auto-refresh: <span class="text-green-400 pulse">●</span> 5s</div>
    <div id="last-updated"></div>
  </div>
</div>

<!-- Quality Score Hero -->
<div class="card mb-6 text-center py-8" id="hero-card">
  <div class="stat-label mb-2">Overall Quality Assurance</div>
  <div id="hero-score" class="text-5xl font-black text-emerald-400">—</div>
  <div id="hero-ci" class="text-sm text-slate-400 mt-1">No data yet</div>
  <div id="hero-message" class="text-xs text-slate-500 mt-3 max-w-xl mx-auto"></div>
</div>

<!-- Summary Cards -->
<div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 mb-6">
  <div class="card">
    <div class="stat-label">Gate Hits</div>
    <div class="stat-value text-amber-400" id="stat-gated">0</div>
  </div>
  <div class="card">
    <div class="stat-label">Bypassed</div>
    <div class="stat-value text-emerald-400" id="stat-bypassed">0</div>
  </div>
  <div class="card">
    <div class="stat-label">Swapped</div>
    <div class="stat-value text-rose-400" id="stat-swapped">0</div>
  </div>
  <div class="card">
    <div class="stat-label">Gate Hit Rate</div>
    <div class="stat-value text-cyan-400" id="stat-gate-rate">0%</div>
  </div>
  <div class="card">
    <div class="stat-label">Shadow Cost</div>
    <div class="stat-value text-violet-400" id="stat-cost">$0.00</div>
  </div>
  <div class="card">
    <div class="stat-label">Categories</div>
    <div class="stat-value text-white" id="stat-categories">0</div>
  </div>
</div>

<!-- Charts + Trust Breakdown -->
<div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
  <!-- Trust Level Pie -->
  <div class="card">
    <h2 class="text-sm font-semibold text-slate-300 mb-3">Trust Level Distribution</h2>
    <div class="flex justify-center" style="height:220px">
      <canvas id="trust-chart"></canvas>
    </div>
  </div>

  <!-- Gate Zone Diagram -->
  <div class="card">
    <h2 class="text-sm font-semibold text-slate-300 mb-3">Gate Configuration</h2>
    <div id="gate-zone" class="mt-4">
      <div class="flex items-center gap-2 mb-3">
        <span class="text-xs text-slate-400">Status:</span>
        <span id="gate-status" class="badge badge-unproven">OFF</span>
      </div>
      <div class="relative h-10 rounded-lg overflow-hidden bg-slate-700 mb-2">
        <div id="zone-green" class="absolute h-full bg-emerald-900/60" style="left:0;width:30%"></div>
        <div id="zone-yellow" class="absolute h-full bg-amber-900/60" style="left:30%;width:40%"></div>
        <div id="zone-red" class="absolute h-full bg-rose-900/60" style="left:70%;width:30%"></div>
      </div>
      <div class="flex justify-between text-xs text-slate-500">
        <span>0.0</span>
        <span id="gate-lower-label">0.3</span>
        <span id="gate-upper-label">0.7</span>
        <span>1.0</span>
      </div>
      <div class="flex justify-between text-xs mt-1">
        <span class="text-emerald-400">Local</span>
        <span class="text-amber-400">Gate (Verify)</span>
        <span class="text-rose-400">Forward</span>
      </div>
    </div>
    <div id="trust-summary" class="mt-4 space-y-1 text-xs"></div>
  </div>

  <!-- Quality Score Over Time (placeholder) -->
  <div class="card">
    <h2 class="text-sm font-semibold text-slate-300 mb-3">Category Pass Rates</h2>
    <div style="height:220px">
      <canvas id="pass-rate-chart"></canvas>
    </div>
  </div>
</div>

<!-- Per-Category Evidence Table -->
<div class="card mb-6">
  <h2 class="text-sm font-semibold text-slate-300 mb-3">Per-Category Evidence</h2>
  <div class="overflow-x-auto">
    <table>
      <thead>
        <tr>
          <th>Category</th>
          <th>Trust Level</th>
          <th>Samples</th>
          <th>Pass Rate</th>
          <th>95% CI</th>
          <th>Failures</th>
          <th>Gate Override</th>
          <th>Recent</th>
        </tr>
      </thead>
      <tbody id="categories-body"></tbody>
    </table>
  </div>
  <div id="no-data" class="text-center text-slate-500 text-sm py-8">
    No categories yet. Enable the QA gate to start building evidence.
  </div>
</div>

<script>
const BASE = window.location.origin;
let trustChart = null;
let passRateChart = null;

function trustBadge(level) {
  const cls = {proven:'badge-proven', trusted:'badge-trusted', building:'badge-building',
               unproven:'badge-unproven', degraded:'badge-degraded'}[level] || 'badge-unproven';
  return `<span class="badge ${cls}">${level}</span>`;
}

async function fetchJSON(path) {
  try { const r = await fetch(BASE + path); return await r.json(); }
  catch { return null; }
}

async function refresh() {
  const [root, qa, categories, cost] = await Promise.all([
    fetchJSON('/'),
    fetchJSON('/qa'),
    fetchJSON('/qa/categories'),
    fetchJSON('/qa/cost'),
  ]);

  if (root) {
    document.getElementById('version').textContent = `v${root.version}`;
  }

  // Hero quality score
  if (qa && qa.quality) {
    const q = qa.quality;
    if (q.score !== null) {
      document.getElementById('hero-score').textContent = q.score + '%';
      const ci = q.confidence_interval_95;
      document.getElementById('hero-ci').textContent =
        `95% CI: [${ci[0]}%, ${ci[1]}%] — ${q.total_validated} validated`;
      document.getElementById('hero-message').textContent = q.message;
      // Color based on score
      const el = document.getElementById('hero-score');
      el.className = q.score >= 95 ? 'text-5xl font-black text-emerald-400' :
                     q.score >= 85 ? 'text-5xl font-black text-amber-400' :
                     'text-5xl font-black text-rose-400';
    }

    // Gate status badge
    const gs = document.getElementById('gate-status');
    if (qa.enabled) {
      gs.textContent = 'ON';
      gs.className = 'badge badge-proven';
    } else {
      gs.textContent = 'OFF';
      gs.className = 'badge badge-unproven';
    }

    // Gate bounds
    const lower = qa.gate_bounds?.lower || 0.3;
    const upper = qa.gate_bounds?.upper || 0.7;
    document.getElementById('gate-lower-label').textContent = lower.toFixed(2);
    document.getElementById('gate-upper-label').textContent = upper.toFixed(2);
    document.getElementById('zone-green').style.width = (lower * 100) + '%';
    document.getElementById('zone-yellow').style.left = (lower * 100) + '%';
    document.getElementById('zone-yellow').style.width = ((upper - lower) * 100) + '%';
    document.getElementById('zone-red').style.left = (upper * 100) + '%';
    document.getElementById('zone-red').style.width = ((1 - upper) * 100) + '%';

    // Trust summary
    if (qa.categories_summary) {
      const cs = qa.categories_summary;
      document.getElementById('trust-summary').innerHTML = [
        `<div class="flex justify-between"><span>Proven</span><span class="text-emerald-400">${cs.proven}</span></div>`,
        `<div class="flex justify-between"><span>Trusted</span><span class="text-blue-400">${cs.trusted}</span></div>`,
        `<div class="flex justify-between"><span>Building</span><span class="text-amber-400">${cs.building}</span></div>`,
        `<div class="flex justify-between"><span>Degraded</span><span class="text-rose-400">${cs.degraded}</span></div>`,
      ].join('');
    }
  }

  // Cost stats
  if (cost) {
    document.getElementById('stat-gated').textContent = cost.total_gated;
    document.getElementById('stat-bypassed').textContent = cost.total_bypassed;
    document.getElementById('stat-swapped').textContent = cost.total_swapped;
    document.getElementById('stat-gate-rate').textContent = Math.round(cost.gate_hit_rate * 100) + '%';
    document.getElementById('stat-cost').textContent = '$' + cost.estimated_shadow_cost_usd.toFixed(4);
  }

  // Categories table + charts
  if (categories && categories.length > 0) {
    document.getElementById('no-data').style.display = 'none';
    document.getElementById('stat-categories').textContent = categories.length;

    const tbody = document.getElementById('categories-body');
    tbody.innerHTML = categories.map(c => {
      const ci = c.confidence_interval_95 || [0, 0];
      const gate = c.gate_lower_override !== null
        ? `[${c.gate_lower_override?.toFixed(2) || '—'}, ${c.gate_upper_override?.toFixed(2) || '—'}]`
        : 'default';
      const recent = (c.recent_scores || []).map(s =>
        `<span class="${s >= 4 ? 'text-emerald-400' : s >= 3 ? 'text-amber-400' : 'text-rose-400'}">${s}</span>`
      ).join(' ');
      return `<tr>
        <td class="font-mono text-xs">${c.category}</td>
        <td>${trustBadge(c.trust_level)}</td>
        <td>${c.total_samples}</td>
        <td class="font-mono">${(c.pass_rate * 100).toFixed(1)}%</td>
        <td class="text-xs text-slate-400">[${(ci[0]*100).toFixed(1)}%, ${(ci[1]*100).toFixed(1)}%]</td>
        <td class="${c.fail_count > 0 ? 'text-rose-400' : 'text-slate-500'}">${c.fail_count}</td>
        <td class="text-xs text-slate-400">${gate}</td>
        <td class="text-xs">${recent}</td>
      </tr>`;
    }).join('');

    // Trust pie chart
    const levels = {proven:0, trusted:0, building:0, unproven:0, degraded:0};
    categories.forEach(c => { levels[c.trust_level] = (levels[c.trust_level] || 0) + 1; });
    const trustData = {
      labels: ['Proven', 'Trusted', 'Building', 'Unproven', 'Degraded'],
      datasets: [{
        data: [levels.proven, levels.trusted, levels.building, levels.unproven, levels.degraded],
        backgroundColor: ['#065f46', '#1e40af', '#78350f', '#334155', '#7f1d1d'],
        borderWidth: 0,
      }],
    };
    if (trustChart) {
      trustChart.data = trustData;
      trustChart.update();
    } else {
      trustChart = new Chart(document.getElementById('trust-chart'), {
        type: 'doughnut', data: trustData,
        options: { plugins: { legend: { labels: { color: '#94a3b8', font: { size: 10 } } } },
                   responsive: true, maintainAspectRatio: false },
      });
    }

    // Pass rate bar chart
    const prData = {
      labels: categories.slice(0, 10).map(c => c.category),
      datasets: [{
        label: 'Pass Rate %',
        data: categories.slice(0, 10).map(c => (c.pass_rate * 100)),
        backgroundColor: categories.slice(0, 10).map(c =>
          c.pass_rate >= 0.95 ? '#065f46' : c.pass_rate >= 0.85 ? '#78350f' : '#7f1d1d'
        ),
        borderWidth: 0,
        borderRadius: 4,
      }],
    };
    if (passRateChart) {
      passRateChart.data = prData;
      passRateChart.update();
    } else {
      passRateChart = new Chart(document.getElementById('pass-rate-chart'), {
        type: 'bar', data: prData,
        options: {
          responsive: true, maintainAspectRatio: false,
          scales: {
            y: { beginAtZero: true, max: 100, ticks: { color: '#94a3b8' }, grid: { color: '#1e293b' } },
            x: { ticks: { color: '#94a3b8', font: { size: 9 } }, grid: { display: false } },
          },
          plugins: { legend: { display: false } },
        },
      });
    }
  } else {
    document.getElementById('no-data').style.display = 'block';
  }

  document.getElementById('last-updated').textContent = new Date().toLocaleTimeString();
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def qa_dashboard_html() -> HTMLResponse:
    """Return the QA evidence dashboard."""
    return HTMLResponse(content=_HTML)
