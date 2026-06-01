const fs = require('fs');
const path = require('path');
const config = require('../pipeline.config');

const PIPELINE_DIR = path.join(__dirname, '../.pipeline');
const OUTPUT_DIR   = config.report.outputDir;
const GATE_FILES   = [1, 2, 3, 4, 5, 6].map(n => ({ n, file: path.join(PIPELINE_DIR, `gate${n}.json`) }));

function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  const gates = GATE_FILES.map(({ n, file }) => {
    if (!fs.existsSync(file)) return { gate: n, name: `Gate ${n}`, passed: false, skipped: true, duration: '-', fixes: [], attempts: 0 };
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  });

  const totalPassed   = gates.filter(g => g.passed).length;
  const totalSkipped  = gates.filter(g => g.skipped).length;
  const totalFixed    = gates.reduce((s, g) => s + (g.fixes || []).filter(f => !f.error).length, 0);
  const overallPassed = gates.every(g => g.passed || g.skipped);
  const runDate       = new Date().toISOString();

  const summary = { runDate, overallPassed, gates: totalPassed, skipped: totalSkipped, autoFixes: totalFixed, gateDetails: gates };

  // JSON report
  fs.writeFileSync(path.join(OUTPUT_DIR, 'summary.json'), JSON.stringify(summary, null, 2));

  // Markdown report
  fs.writeFileSync(path.join(OUTPUT_DIR, 'summary.md'), buildMarkdown(summary, gates, runDate));

  // HTML report
  fs.writeFileSync(path.join(OUTPUT_DIR, 'summary.html'), buildHTML(summary, gates, runDate));

  console.log('\n' + '═'.repeat(62));
  console.log('📊 Pipeline Report Generated');
  console.log('═'.repeat(62));
  console.log(`  Overall : ${overallPassed ? '✅ PASSED' : '❌ FAILED'}`);
  console.log(`  Gates   : ${totalPassed}/6 passed`);
  console.log(`  Fixes   : ${totalFixed} auto-fix(es) applied by Claude`);
  console.log(`  Reports : ${OUTPUT_DIR}`);
  console.log('');

  // Post markdown to GitHub step summary if available
  if (process.env.GITHUB_STEP_SUMMARY) {
    fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, buildMarkdown(summary, gates, runDate));
  }
}

function buildMarkdown(summary, gates, runDate) {
  const badge = summary.overallPassed ? '🟢 PASSED' : '🔴 FAILED';
  const lines = [
    `# Dev Pipeline Report — ${badge}`,
    `**Run:** ${runDate}  |  **Auto-fixes applied:** ${summary.autoFixes}`,
    '',
    '## Gate Results',
    '',
    '| Gate | Name | Status | Duration | Attempts | Auto-Fixes |',
    '|------|------|--------|----------|----------|------------|',
  ];

  for (const g of gates) {
    const status = g.skipped ? '⏭ Skipped' : g.passed ? '✅ Passed' : '❌ Failed';
    const fixes  = (g.fixes || []).filter(f => !f.error).length;
    lines.push(`| ${g.gate} | ${g.name} | ${status} | ${g.duration || '-'} | ${g.attempts || '-'} | ${fixes} |`);
  }

  lines.push('', '## Auto-Fix Details', '');
  for (const g of gates) {
    const fixes = (g.fixes || []).filter(f => f.rootCause);
    if (!fixes.length) continue;
    lines.push(`### Gate ${g.gate}: ${g.name}`);
    for (const f of fixes) {
      lines.push(`- **Attempt ${f.attempt}** — ${f.rootCause} *(confidence: ${Math.round((f.confidence || 0) * 100)}%)*`);
      for (const p of (f.patches || [])) {
        lines.push(`  - \`${p.file}\`: ${p.status === 'applied' ? p.description : `skipped — ${p.reason}`}`);
      }
    }
    lines.push('');
  }

  // E2E scenario results if present
  const gate6 = gates.find(g => g.gate === 6);
  if (gate6?.scenarios?.length) {
    lines.push('## E2E Scenario Results', '');
    lines.push('| Scenario | Browser | Status | Duration |');
    lines.push('|----------|---------|--------|----------|');
    for (const s of gate6.scenarios) {
      const st = s.passed ? '✅' : `❌ ${s.error?.slice(0, 60)}`;
      lines.push(`| ${s.scenario} | ${s.browser} | ${st} | ${s.duration || '-'} |`);
    }
    lines.push('');
  }

  return lines.join('\n');
}

function buildHTML(summary, gates, runDate) {
  const badge       = summary.overallPassed ? '#22c55e' : '#ef4444';
  const badgeLabel  = summary.overallPassed ? 'PASSED' : 'FAILED';
  const gateRows    = gates.map(g => {
    const status    = g.skipped ? `<span style="color:#a855f7">⏭ Skipped</span>`
                    : g.passed  ? `<span style="color:#22c55e">✅ Passed</span>`
                    :             `<span style="color:#ef4444">❌ Failed</span>`;
    const fixes     = (g.fixes || []).filter(f => !f.error).length;
    return `
      <tr>
        <td style="font-weight:600">Gate ${g.gate}</td>
        <td>${g.name}</td>
        <td>${status}</td>
        <td>${g.duration || '—'}</td>
        <td>${g.attempts || '—'}</td>
        <td>${fixes || '—'}</td>
      </tr>`;
  }).join('');

  const fixDetails = gates.flatMap(g =>
    (g.fixes || []).filter(f => f.rootCause).map(f => `
      <div style="margin:8px 0;padding:10px;background:#1e293b;border-radius:6px">
        <strong>Gate ${g.gate} / Attempt ${f.attempt}</strong>
        — ${f.rootCause}
        <span style="color:#94a3b8;font-size:0.85em">(${Math.round((f.confidence || 0) * 100)}% confidence)</span>
        ${(f.patches || []).map(p => `
          <div style="margin-top:4px;font-size:0.85em;color:${p.status === 'applied' ? '#4ade80' : '#f87171'}">
            ${p.status === 'applied' ? '✓' : '✗'} <code>${p.file}</code>
            ${p.status === 'applied' ? `— ${p.description}` : `— ${p.reason}`}
          </div>`).join('')}
      </div>`)
  ).join('');

  const gate6 = gates.find(g => g.gate === 6);
  const e2eTable = gate6?.scenarios?.length ? `
    <h2>E2E Scenarios</h2>
    <table>
      <thead><tr><th>Scenario</th><th>Browser</th><th>Status</th><th>Duration</th></tr></thead>
      <tbody>
        ${gate6.scenarios.map(s => `
          <tr>
            <td>${s.scenario}</td>
            <td>${s.browser}</td>
            <td>${s.passed
              ? `<span style="color:#22c55e">✅ Pass</span>`
              : `<span style="color:#ef4444">❌ ${(s.error || '').slice(0, 60)}</span>`}
            </td>
            <td>${s.duration || '—'}</td>
          </tr>`).join('')}
      </tbody>
    </table>` : '';

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Dev Pipeline Report</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; padding: 32px; }
  h1 { font-size: 1.8rem; margin-bottom: 4px; }
  h2 { font-size: 1.2rem; margin: 28px 0 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; }
  .meta { color: #64748b; font-size: 0.9rem; margin-bottom: 28px; }
  .badge { display: inline-block; padding: 6px 16px; border-radius: 4px; font-weight: 700;
           letter-spacing: .08em; color: #fff; background: ${badge}; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #1e293b; }
  th { background: #1e293b; color: #94a3b8; font-size: 0.8rem; text-transform: uppercase; }
  tr:hover td { background: #1e293b44; }
  code { background: #1e293b; padding: 2px 6px; border-radius: 3px; font-size: 0.85em; }
  .stats { display: flex; gap: 20px; margin-bottom: 28px; flex-wrap: wrap; }
  .stat { background: #1e293b; border-radius: 8px; padding: 16px 24px; min-width: 140px; }
  .stat-val { font-size: 2rem; font-weight: 700; }
  .stat-lbl { color: #64748b; font-size: 0.8rem; text-transform: uppercase; margin-top: 2px; }
</style>
</head>
<body>
  <h1>Dev Pipeline Report &nbsp; <span class="badge">${badgeLabel}</span></h1>
  <p class="meta">Run: ${runDate}</p>

  <div class="stats">
    <div class="stat"><div class="stat-val">${summary.gates}/6</div><div class="stat-lbl">Gates Passed</div></div>
    <div class="stat"><div class="stat-val">${summary.autoFixes}</div><div class="stat-lbl">Auto-Fixes</div></div>
    <div class="stat"><div class="stat-val">${summary.skipped}</div><div class="stat-lbl">Skipped</div></div>
  </div>

  <h2>Gates</h2>
  <table>
    <thead>
      <tr><th>Gate</th><th>Name</th><th>Status</th><th>Duration</th><th>Attempts</th><th>Auto-Fixes</th></tr>
    </thead>
    <tbody>${gateRows}</tbody>
  </table>

  ${fixDetails ? `<h2>Auto-Fix Details</h2>${fixDetails}` : ''}

  ${e2eTable}
</body>
</html>`;
}

main();
