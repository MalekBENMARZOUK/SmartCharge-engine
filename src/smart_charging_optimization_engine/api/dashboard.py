from __future__ import annotations


def render_dashboard_html() -> str:
    return """<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>SCE Planner Dashboard</title>
  <style>
    :root {
      --ink: #132238;
      --muted: #58708c;
      --sand: #f6f1e7;
      --paper: rgba(255,255,255,0.88);
      --line: rgba(19,34,56,0.12);
      --accent: #d9652b;
      --accent-2: #1b8f77;
      --warn: #c2410c;
      --ok: #0f766e;
      --shadow: 0 24px 60px rgba(19,34,56,0.15);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Georgia, Cambria, 'Times New Roman', serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(217,101,43,0.18), transparent 34%),
        radial-gradient(circle at top right, rgba(27,143,119,0.18), transparent 28%),
        linear-gradient(180deg, #f8f4ec 0%, #efe6d6 100%);
      min-height: 100vh;
    }
    .shell {
      width: min(1280px, calc(100% - 32px));
      margin: 24px auto 48px;
      display: grid;
      gap: 18px;
    }
    .hero, .panel, .table-wrap {
      background: var(--paper);
      backdrop-filter: blur(16px);
      border: 1px solid var(--line);
      border-radius: 22px;
      box-shadow: var(--shadow);
    }
    .hero {
      padding: 26px;
      display: grid;
      grid-template-columns: 1.6fr 1fr;
      gap: 16px;
      overflow: hidden;
    }
    .hero h1 {
      margin: 0 0 8px;
      font-size: clamp(2rem, 5vw, 4.2rem);
      line-height: 0.95;
      letter-spacing: -0.04em;
    }
    .hero p { margin: 0; color: var(--muted); font-size: 1rem; max-width: 60ch; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .metric, .panel { padding: 18px; }
    .metric {
      background: rgba(255,255,255,0.74);
      border: 1px solid var(--line);
      border-radius: 18px;
      transform: translateY(10px);
      opacity: 0;
      animation: rise 500ms ease forwards;
    }
    .metric:nth-child(2) { animation-delay: 70ms; }
    .metric:nth-child(3) { animation-delay: 140ms; }
    .metric:nth-child(4) { animation-delay: 210ms; }
    .metric .label {
      color: var(--muted);
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .metric .value { font-size: 2rem; margin-top: 10px; }
    .grid {
      display: grid;
      grid-template-columns: 1.05fr 1fr;
      gap: 18px;
    }
    .panel h2, .table-wrap h2 { margin: 0 0 12px; font-size: 1.05rem; letter-spacing: 0.02em; }
    .panel p.note { color: var(--muted); margin: 0; }
    table {
      width: 100%;
      border-collapse: collapse;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
      font-size: 0.92rem;
    }
    th, td {
      text-align: left;
      padding: 11px 10px;
      border-bottom: 1px solid var(--line);
    }
    th {
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    tr:last-child td { border-bottom: none; }
    .table-wrap { padding: 18px; overflow: hidden; }
    .pill {
      display: inline-flex;
      align-items: center;
      padding: 4px 10px;
      border-radius: 999px;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
      font-size: 0.8rem;
      font-weight: 600;
      background: rgba(19,34,56,0.08);
    }
    .pill.ok { background: rgba(15,118,110,0.12); color: var(--ok); }
    .pill.warn { background: rgba(194,65,12,0.12); color: var(--warn); }
    .compare-controls {
      display: grid;
      grid-template-columns: 1fr 1fr auto;
      gap: 10px;
      margin-bottom: 14px;
    }
    select, button {
      width: 100%;
      border-radius: 14px;
      border: 1px solid var(--line);
      padding: 12px 14px;
      background: white;
      color: var(--ink);
      font-size: 0.95rem;
    }
    button {
      background: linear-gradient(135deg, var(--accent), #f09b62);
      color: white;
      border: none;
      cursor: pointer;
      font-weight: 700;
    }
    .comparison-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 12px;
    }
    .comparison-card {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.72);
    }
    .comparison-card .title {
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .comparison-card .delta { font-size: 1.4rem; margin-top: 8px; }
    .empty { color: var(--muted); font-style: italic; }
    @keyframes rise { to { transform: translateY(0); opacity: 1; } }
    @media (max-width: 980px) {
      .hero, .grid { grid-template-columns: 1fr; }
      .metrics, .comparison-grid, .compare-controls { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 640px) {
      .shell { width: min(100% - 18px, 1280px); }
      .metrics, .comparison-grid, .compare-controls { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class=\"shell\">
    <section class=\"hero\">
      <div>
        <h1>Planner Control Room</h1>
        <p>
          Monitor queued optimizations, inspect recent run quality, and compare
          telemetry-driven replans against baseline schedules without leaving
          the service surface.
        </p>
      </div>
      <div class=\"metrics\">
        <article class=\"metric\">
          <div class=\"label\">Stored Runs</div>
          <div class=\"value\" id=\"metric-runs\">0</div>
        </article>
        <article class=\"metric\">
          <div class=\"label\">Queued Jobs</div>
          <div class=\"value\" id=\"metric-queued\">0</div>
        </article>
        <article class=\"metric\">
          <div class=\"label\">Succeeded Jobs</div>
          <div class=\"value\" id=\"metric-succeeded\">0</div>
        </article>
        <article class=\"metric\">
          <div class=\"label\">At-Risk Vehicles</div>
          <div class=\"value\" id=\"metric-risk\">0</div>
        </article>
      </div>
    </section>

    <section class=\"grid\">
      <section class=\"table-wrap\">
        <h2>Recent Runs</h2>
        <table>
          <thead>
            <tr>
              <th>Run</th><th>Kind</th><th>Status</th><th>Scenario</th>
              <th>Total Cost</th><th>Risk</th>
            </tr>
          </thead>
          <tbody id=\"runs-body\">
            <tr><td colspan=\"6\" class=\"empty\">Loading runs…</td></tr>
          </tbody>
        </table>
      </section>

      <section class=\"table-wrap\">
        <h2>Async Jobs</h2>
        <table>
          <thead>
            <tr><th>Job</th><th>Type</th><th>Status</th><th>Scenario</th><th>Run</th></tr>
          </thead>
          <tbody id=\"jobs-body\">
            <tr><td colspan=\"5\" class=\"empty\">Loading jobs…</td></tr>
          </tbody>
        </table>
      </section>
    </section>

    <section class=\"panel\">
      <h2>Run Comparison</h2>
      <div class=\"compare-controls\">
        <select id=\"baseline-run\"></select>
        <select id=\"candidate-run\"></select>
        <button id=\"compare-button\" type=\"button\">Compare</button>
      </div>
      <p class=\"note\" id=\"comparison-summary\">
        Pick two runs to inspect cost, unmet energy, and risk deltas.
      </p>
      <div class=\"comparison-grid\" id=\"comparison-grid\"></div>
    </section>
  </main>

  <script>
    const format = (value) => Number(value || 0).toFixed(2);

    function esc(str) {
      const el = document.createElement('span');
      el.textContent = String(str ?? '');
      return el.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function renderStatusPill(status) {
      const kind =
        status === 'succeeded' ||
        status === 'optimal' ||
        status === 'feasible'
          ? 'ok'
          : 'warn';
      return `<span class=\"pill ${kind}\">${esc(status)}</span>`;
    }

    function setComparisonCards(cards) {
      const host = document.getElementById('comparison-grid');
      host.innerHTML = cards.map(card => `
        <article class=\"comparison-card\">
          <div class=\"title\">${esc(card.title)}</div>
          <div class=\"delta\">${esc(card.value)}</div>
        </article>
      `).join('');
    }

    async function loadDashboard() {
      const [runsResponse, jobsResponse] = await Promise.all([
        fetch('/runs'),
        fetch('/jobs')
      ]);
      const runsPayload = await runsResponse.json();
      const jobsPayload = await jobsResponse.json();
      const runs = runsPayload.runs || [];
      const jobs = jobsPayload.jobs || [];

      document.getElementById('metric-runs').textContent = runs.length;
      document.getElementById('metric-queued').textContent = jobs.filter(
        (job) => job.status === 'queued' || job.status === 'running'
      ).length;
      document.getElementById('metric-succeeded').textContent = jobs.filter(
        (job) => job.status === 'succeeded'
      ).length;
      document.getElementById('metric-risk').textContent = runs.reduce(
        (sum, run) => sum + (run.summary?.at_risk_vehicle_count || 0),
        0,
      );

      const runsBody = document.getElementById('runs-body');
      runsBody.innerHTML = runs.length ? runs.map(run => `
        <tr>
          <td>${esc(run.run_id)}</td>
          <td>${esc(run.run_kind)}</td>
          <td>${renderStatusPill(run.status)}</td>
          <td>${esc(run.scenario_name)}</td>
          <td>${format(run.summary.total_cost)}</td>
          <td>${esc(run.summary.at_risk_vehicle_count)}</td>
        </tr>`).join('') : '<tr><td colspan=\"6\" class=\"empty\">No runs available yet.</td></tr>';

      const jobsBody = document.getElementById('jobs-body');
      jobsBody.innerHTML = jobs.length ? jobs.map(job => `
        <tr>
          <td>${esc(job.job_id)}</td>
          <td>${esc(job.run_kind)}</td>
          <td>${renderStatusPill(job.status)}</td>
          <td>${esc(job.scenario_name)}</td>
          <td>${esc(job.run_id || 'pending')}</td>
        </tr>`).join('') : '<tr><td colspan=\"5\" class=\"empty\">No jobs available yet.</td></tr>';

      const baseline = document.getElementById('baseline-run');
      const candidate = document.getElementById('candidate-run');
      const options = runs.map((run) => {
        const id = esc(run.run_id);
        const name = esc(run.scenario_name);
        return `<option value=\"${id}\">${id} \u2022 ${name}</option>`;
      }).join('');
      baseline.innerHTML = options;
      candidate.innerHTML = options;
      if (runs.length > 1) {
        baseline.value = runs[1].run_id;
        candidate.value = runs[0].run_id;
      }
      if (runs.length >= 2) {
        await compareRuns();
      } else {
        setComparisonCards([{ title: 'Comparison', value: 'Need 2 runs' }]);
      }
    }

    async function compareRuns() {
      const baseline = document.getElementById('baseline-run').value;
      const candidate = document.getElementById('candidate-run').value;
      if (!baseline || !candidate || baseline === candidate) {
        document.getElementById('comparison-summary').textContent = (
          'Pick two different runs to compare.'
        );
        setComparisonCards([{ title: 'Comparison', value: 'Waiting' }]);
        return;
      }
      const response = await fetch(
        `/runs/compare?baseline_run_id=${encodeURIComponent(baseline)}&candidate_run_id=${encodeURIComponent(candidate)}`
      );
      const comparison = await response.json();
      document.getElementById('comparison-summary').textContent = comparison.summary;
      setComparisonCards([
        { title: 'Total Cost Delta', value: format(comparison.total_cost_delta) },
        { title: 'Unmet Penalty Delta', value: format(comparison.unmet_demand_penalty_delta) },
        { title: 'At-Risk Vehicle Delta', value: String(comparison.at_risk_vehicle_delta) },
        { title: 'Solve Time Delta (s)', value: format(comparison.solve_time_delta_seconds) },
      ]);
    }

    document.getElementById('compare-button').addEventListener('click', compareRuns);
    loadDashboard();
    setInterval(loadDashboard, 15000);
  </script>
</body>
</html>"""
