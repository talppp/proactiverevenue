const fs = require('fs');
const path = require('path');
const autocannon = require('autocannon');
const config = require('../pipeline.config');

const cfg = config.gates[3];
const OUTPUT = path.join(__dirname, '../.pipeline/gate3.json');

async function main() {
  fs.mkdirSync(path.dirname(OUTPUT), { recursive: true });
  fs.mkdirSync(path.join(__dirname, '../.pipeline/screenshots'), { recursive: true });

  const result = {
    gate: 3,
    name: cfg.name,
    passed: false,
    startTime: Date.now(),
    sanity: null,
    stress: null,
    race: null,
    fixes: [],
  };

  console.log('\n' + '═'.repeat(62));
  console.log(`🚦 Gate 3: ${cfg.name}`);
  console.log('═'.repeat(62));

  // ── Sanity check ────────────────────────────────────────────────
  console.log('\n  ▶ Sanity check...');
  result.sanity = await runSanity(cfg.sanityURL);
  console.log(result.sanity.passed
    ? `  ✅ Sanity passed (HTTP ${result.sanity.status})`
    : `  ❌ Sanity failed: ${result.sanity.error}`);

  if (!result.sanity.passed) {
    result.duration = elapsed(result.startTime);
    fs.writeFileSync(OUTPUT, JSON.stringify(result, null, 2));
    process.exit(1);
  }

  // ── Stress test ─────────────────────────────────────────────────
  console.log(`\n  ▶ Stress test: ${cfg.stressConnections} connections × ${cfg.stressDuration}s → ${cfg.stressTarget}`);
  result.stress = await runStress(cfg.stressTarget, cfg.stressDuration, cfg.stressConnections);
  const errorRate = result.stress.errors / Math.max(result.stress.requests, 1);
  result.stress.passed = errorRate < 0.01; // < 1% errors
  console.log(result.stress.passed
    ? `  ✅ Stress passed — ${result.stress.requests} req, ${(errorRate * 100).toFixed(2)}% errors, p99 ${result.stress.latencyP99}ms`
    : `  ❌ Stress failed — error rate ${(errorRate * 100).toFixed(2)}%`);

  // ── Race condition test ──────────────────────────────────────────
  console.log(`\n  ▶ Race condition test: ${cfg.raceWorkers} concurrent workers → ${cfg.stressTarget}${cfg.raceEndpoint}`);
  result.race = await runRaceCondition(cfg.stressTarget + cfg.raceEndpoint, cfg.raceWorkers);
  console.log(result.race.passed
    ? `  ✅ Race condition passed — ${result.race.uniqueIds} unique IDs, 0 duplicates`
    : `  ❌ Race condition failed — ${result.race.duplicates} duplicate IDs, ${result.race.errors} errors`);

  result.passed = result.sanity.passed && result.stress.passed && result.race.passed;
  result.duration = elapsed(result.startTime);
  fs.writeFileSync(OUTPUT, JSON.stringify(result, null, 2));

  appendStepSummary(result);
  process.exit(result.passed ? 0 : 1);
}

async function runSanity(url) {
  try {
    const { default: fetch } = await import('node-fetch').catch(() => ({ default: global.fetch }));
    const res = await (fetch || global.fetch)(url, { signal: AbortSignal.timeout(10_000) });
    return { passed: res.ok, status: res.status };
  } catch (e) {
    return { passed: false, error: e.message };
  }
}

function runStress(url, duration, connections) {
  return new Promise((resolve, reject) => {
    const inst = autocannon({ url, duration, connections, pipelining: 1 }, (err, result) => {
      if (err) return reject(err);
      resolve({
        requests:   result.requests.total,
        errors:     result.errors,
        latencyP99: result.latency.p99,
        throughput: result.throughput.average,
      });
    });
    autocannon.track(inst, { renderProgressBar: false });
  });
}

async function runRaceCondition(url, workers) {
  const seen = new Set();
  let errors = 0;
  let duplicates = 0;

  const results = await Promise.allSettled(
    Array.from({ length: workers }, (_, i) =>
      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workerId: i, ts: Date.now() }),
        signal: AbortSignal.timeout(5_000),
      })
        .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
        .catch(e => ({ _error: e.message }))
    )
  );

  for (const r of results) {
    const val = r.status === 'fulfilled' ? r.value : { _error: r.reason?.message };
    if (val._error) { errors++; continue; }
    const id = val.id ?? val._id ?? JSON.stringify(val);
    if (seen.has(id)) duplicates++;
    else seen.add(id);
  }

  return { passed: errors === 0 && duplicates === 0, uniqueIds: seen.size, errors, duplicates };
}

function elapsed(start) {
  return ((Date.now() - start) / 1000).toFixed(1) + 's';
}

function appendStepSummary(result) {
  if (!process.env.GITHUB_STEP_SUMMARY) return;
  const icon = result.passed ? '✅' : '❌';
  fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY,
    `| ${icon} Gate 3 | ${result.name} | ${result.duration} | 0 fix(es) |\n`);
}

main().catch(err => {
  console.error('Gate 3 fatal:', err);
  process.exit(1);
});
