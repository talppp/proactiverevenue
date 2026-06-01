const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { fixBug, applyPatches } = require('./claude-fixer');
const config = require('../pipeline.config');

async function runGate({ gateNumber, gateName, command, testFile, sourceFiles = [], maxFixes = 3, outputFile }) {
  const result = {
    gate: gateNumber,
    name: gateName,
    passed: false,
    skipped: false,
    attempts: 0,
    fixes: [],
    output: '',
    startTime: Date.now(),
    duration: null,
  };

  const bar = '═'.repeat(62);
  console.log(`\n${bar}`);
  console.log(`🚦 Gate ${gateNumber}: ${gateName}`);
  console.log(bar);

  for (let attempt = 0; attempt <= maxFixes; attempt++) {
    result.attempts = attempt + 1;
    console.log(`\n  ▶ Run ${attempt + 1} / ${maxFixes + 1}...`);

    try {
      const output = execSync(command, {
        encoding: 'utf8',
        stdio: 'pipe',
        timeout: 300_000,
        cwd: config.projectRoot,
        env: {
          ...process.env,
          // Python project defaults — in-memory store, stub LLM
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

      if (attempt < maxFixes) {
        console.log(`\n  🤖 Claude auto-fix ${attempt + 1} / ${maxFixes}...`);
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
          console.error(`  ⚠  Auto-fix error: ${fixErr.message}`);
          result.fixes.push({ attempt: attempt + 1, error: fixErr.message });
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
    const icon = result.passed ? '✅' : '❌';
    fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY,
      `| ${icon} Gate ${gateNumber} | ${gateName} | ${result.duration} | ${result.fixes.length} fix(es) |\n`);
  }

  return result;
}

module.exports = { runGate };
