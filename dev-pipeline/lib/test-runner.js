const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { fixBug, applyPatches } = require('./claude-fixer');
const config = require('../pipeline.config');

// ── Auto-fix eligibility ─────────────────────────────────────────────────────
// Auto-fix is skipped when:
//   1. SKIP_AUTO_FIX=true  (explicit opt-out)
//   2. ANTHROPIC_API_KEY is missing or empty  (no credentials → would always 400)
// Either condition makes a failed test a hard failure — no LLM calls attempted.
function autoFixEnabled() {
  if (process.env.SKIP_AUTO_FIX === 'true') return false;
  if (!process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_API_KEY.trim() === '') return false;
  return true;
}

async function runGate({ gateNumber, gateName, command, testFile, sourceFiles = [], maxFixes = 3, outputFile }) {
  const fixEnabled = autoFixEnabled();
  const effectiveMaxFixes = fixEnabled ? maxFixes : 0;

  const result = {
    gate: gateNumber,
    name: gateName,
    passed: false,
    skipped: false,
    autoFixEnabled: fixEnabled,
    attempts: 0,
    fixes: [],
    output: '',
    startTime: Date.now(),
    duration: null,
  };

  const bar = '═'.repeat(62);
  console.log(`\n${bar}`);
  console.log(`🚦 Gate ${gateNumber}: ${gateName}`);
  if (!fixEnabled) {
    const reason = process.env.SKIP_AUTO_FIX === 'true'
      ? 'SKIP_AUTO_FIX=true'
      : 'ANTHROPIC_API_KEY not set';
    console.log(`   ℹ  Auto-fix disabled (${reason}) — tests run once, pass or fail`);
  }
  console.log(bar);

  for (let attempt = 0; attempt <= effectiveMaxFixes; attempt++) {
    result.attempts = attempt + 1;
    const totalRuns = effectiveMaxFixes + 1;
    console.log(`\n  ▶ Run ${attempt + 1}${totalRuns > 1 ? ` / ${totalRuns}` : ''}...`);

    try {
      const output = execSync(command, {
        encoding: 'utf8',
        stdio: 'pipe',
        timeout: 300_000,
        cwd: config.projectRoot,
        env: {
          ...process.env,
          DB_URL:            process.env.DB_URL            || 'memory',
          ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY || '',
          PYTHONPATH:        process.env.PYTHONPATH        || config.projectRoot,
        },
      });
      result.passed = true;
      result.output = output;
      console.log('  ✅ PASSED');
      break;
    } catch (err) {
      const errorOutput = [err.stdout, err.stderr].filter(Boolean).join('\n');
      result.output = errorOutput;
      console.log('  ❌ FAILED');
      console.log(errorOutput.slice(0, 800).split('\n').map(l => '    ' + l).join('\n'));

      if (fixEnabled && attempt < effectiveMaxFixes) {
        console.log(`\n  🤖 Claude auto-fix ${attempt + 1} / ${effectiveMaxFixes}...`);
        try {
          const fix = await fixBug({
            errorOutput,
            testFile,
            sourceFiles,
            gateName,
            attempt,
            model: config.claude.model,
          });
          console.log(`     Root cause : ${fix.rootCause}`);
          console.log(`     Confidence : ${Math.round((fix.confidence || 0) * 100)}%`);
          const patchResults = applyPatches(fix.patches, config.projectRoot);
          result.fixes.push({
            attempt: attempt + 1,
            rootCause: fix.rootCause,
            confidence: fix.confidence,
            patches: patchResults,
          });
        } catch (fixErr) {
          // ── Resilient: LLM errors never fail the gate ──────────────────
          // A billing error, network timeout, or bad response from Claude
          // must NOT mask a real test failure — we log and move on.
          const isCredentialError = /credit|billing|quota|401|403|400/i.test(fixErr.message);
          const tag = isCredentialError ? '💳 billing/credential issue' : '⚠  LLM error';
          console.warn(`  ${tag}: ${fixErr.message}`);
          console.warn('     Tests failed but auto-fix could not run — see error above.');
          result.fixes.push({ attempt: attempt + 1, error: fixErr.message, skippedReason: tag });
          // Stop retrying — remaining attempts won't help without working LLM access
          break;
        }
      }
    }
  }

  result.duration = ((Date.now() - result.startTime) / 1000).toFixed(1) + 's';

  if (outputFile) {
    fs.mkdirSync(path.dirname(outputFile), { recursive: true });
    fs.writeFileSync(outputFile, JSON.stringify(result, null, 2));
  }

  if (process.env.GITHUB_STEP_SUMMARY) {
    const icon   = result.passed ? '✅' : '❌';
    const fixNote = !fixEnabled ? 'auto-fix off' : `${result.fixes.length} fix(es)`;
    fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY,
      `| ${icon} Gate ${gateNumber} | ${gateName} | ${result.duration} | ${fixNote} |\n`);
  }

  return result;
}

module.exports = { runGate };
