const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const config = require('../pipeline.config');

const cfg = config.gates[5];
const OUTPUT = path.join(__dirname, '../.pipeline/gate5.json');

async function main() {
  fs.mkdirSync(path.dirname(OUTPUT), { recursive: true });

  const result = {
    gate: 5,
    name: cfg.name,
    passed: false,
    startTime: Date.now(),
    checks: {},
  };

  console.log('\n' + '═'.repeat(62));
  console.log(`🚦 Gate 5: ${cfg.name}`);
  console.log('═'.repeat(62));

  // ── 1. Required env vars ─────────────────────────────────────────
  console.log('\n  ▶ Checking required environment variables...');
  const missingVars = cfg.requiredEnvVars.filter(v => !process.env[v]);
  result.checks.envVars = { passed: missingVars.length === 0, missing: missingVars };
  console.log(result.checks.envVars.passed
    ? `  ✅ All required env vars present (${cfg.requiredEnvVars.join(', ')})`
    : `  ❌ Missing env vars: ${missingVars.join(', ')}`);

  // ── 2. Build ─────────────────────────────────────────────────────
  console.log(`\n  ▶ Running build: ${cfg.buildCommand}`);
  try {
    execSync(cfg.buildCommand, { cwd: config.projectRoot, stdio: 'pipe', timeout: 120_000 });
    result.checks.build = { passed: true };
    console.log('  ✅ Build succeeded');
  } catch (err) {
    result.checks.build = { passed: false, error: err.stderr?.toString().slice(0, 500) || err.message };
    console.log(`  ❌ Build failed: ${result.checks.build.error}`);
  }

  // ── 3. Docker Compose validation (if file exists) ────────────────
  const composeExists = fs.existsSync(cfg.dockerComposeFile);
  console.log(`\n  ▶ Docker Compose validation...`);
  if (!composeExists) {
    result.checks.docker = { passed: true, skipped: true, reason: 'docker-compose.yml not found — skipped' };
    console.log('  ⏭ Skipped (no docker-compose.yml)');
  } else {
    try {
      execSync(`docker compose -f "${cfg.dockerComposeFile}" config --quiet`, { stdio: 'pipe', timeout: 30_000 });
      result.checks.docker = { passed: true };
      console.log('  ✅ docker-compose.yml is valid');
    } catch (err) {
      const error = err.stderr?.toString().slice(0, 500) || err.message;
      result.checks.docker = { passed: false, error };
      console.log(`  ❌ docker-compose.yml invalid: ${error}`);
    }
  }

  // ── 4. Health endpoint (if server is reachable) ──────────────────
  console.log(`\n  ▶ Health check: ${cfg.healthCheckURL}`);
  result.checks.health = await checkHealth(cfg.healthCheckURL);
  if (result.checks.health.skipped) {
    console.log('  ⏭ Skipped (server not reachable — start it to enable this check)');
  } else {
    console.log(result.checks.health.passed
      ? `  ✅ Health endpoint OK (HTTP ${result.checks.health.status})`
      : `  ❌ Health check failed: ${result.checks.health.error}`);
  }

  const criticalChecks = [result.checks.envVars, result.checks.build];
  result.passed = criticalChecks.every(c => c.passed);
  result.duration = ((Date.now() - result.startTime) / 1000).toFixed(1) + 's';

  fs.writeFileSync(OUTPUT, JSON.stringify(result, null, 2));
  appendStepSummary(result);
  process.exit(result.passed ? 0 : 1);
}

async function checkHealth(url) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(5_000) });
    return { passed: res.ok, status: res.status };
  } catch (e) {
    if (e.code === 'ECONNREFUSED' || e.message.includes('ECONNREFUSED')) {
      return { passed: true, skipped: true, reason: 'server not running' };
    }
    return { passed: false, error: e.message };
  }
}

function appendStepSummary(result) {
  if (!process.env.GITHUB_STEP_SUMMARY) return;
  const icon = result.passed ? '✅' : '❌';
  fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY,
    `| ${icon} Gate 5 | ${result.name} | ${result.duration} | 0 fix(es) |\n`);
}

main().catch(err => {
  console.error('Gate 5 fatal:', err);
  process.exit(1);
});
