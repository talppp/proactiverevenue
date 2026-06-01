const fs = require('fs');
const path = require('path');
const { chromium, firefox, webkit } = require('@playwright/test');
const { fixBug, applyPatches } = require('../lib/claude-fixer');
const config = require('../pipeline.config');

const cfg = config.gates[6];
const OUTPUT    = path.join(__dirname, '../.pipeline/gate6.json');
const BROWSERS  = { chromium, firefox, webkit };

async function main() {
  fs.mkdirSync(path.dirname(OUTPUT), { recursive: true });
  fs.mkdirSync(cfg.screenshotDir, { recursive: true });
  fs.mkdirSync(cfg.videoDir, { recursive: true });

  const result = {
    gate: 6,
    name: cfg.name,
    passed: false,
    startTime: Date.now(),
    scenarios: [],
    fixes: [],
  };

  console.log('\n' + '═'.repeat(62));
  console.log(`🚦 Gate 6: ${cfg.name}`);
  console.log('═'.repeat(62));
  console.log(`  Browsers     : ${cfg.browsers.join(', ')}`);
  console.log(`  Environments : ${cfg.environments.map(e => `${e.name}(${e.role})`).join(', ')}`);

  const scenarios = loadScenarios(cfg.testDir);
  console.log(`  Scenarios    : ${scenarios.length} loaded\n`);

  for (let attempt = 0; attempt <= cfg.maxFixes; attempt++) {
    result.scenarios = [];
    let allPassed = true;

    for (const browserName of cfg.browsers) {
      const browserType = BROWSERS[browserName];
      if (!browserType) { console.warn(`  ⚠ Unknown browser: ${browserName}`); continue; }

      console.log(`\n  ── ${browserName.toUpperCase()} ─────────────────────────────────`);
      const browser = await browserType.launch({ headless: true });

      for (const scenario of scenarios) {
        const scenarioResult = await runScenario(browser, browserName, scenario, cfg.environments, cfg.screenshotDir, cfg.videoDir);
        result.scenarios.push(scenarioResult);
        if (!scenarioResult.passed) allPassed = false;
        const icon = scenarioResult.passed ? '✅' : '❌';
        console.log(`  ${icon} [${browserName}] ${scenario.name}`);
        if (!scenarioResult.passed) console.log(`     Error: ${scenarioResult.error}`);
      }

      await browser.close();
    }

    if (allPassed) {
      result.passed = true;
      break;
    }

    if (attempt < cfg.maxFixes) {
      console.log(`\n  🤖 Calling Claude for E2E fix ${attempt + 1} / ${cfg.maxFixes}...`);
      const failures = result.scenarios.filter(s => !s.passed);
      const errorSummary = failures.map(f => `[${f.browser}] ${f.scenario}: ${f.error}`).join('\n');
      try {
        const fix = await fixBug({
          errorOutput: errorSummary,
          testFile: path.join(cfg.testDir, 'scenarios.js'),
          sourceFiles: collectSrcFiles(config.projectRoot),
          gateName: cfg.name,
          attempt,
          model: config.claude.model,
        });
        console.log(`     Root cause : ${fix.rootCause}`);
        const patchResults = applyPatches(fix.patches, config.projectRoot);
        result.fixes.push({ attempt: attempt + 1, rootCause: fix.rootCause, patches: patchResults });
      } catch (fixErr) {
        console.error(`  ⚠ Auto-fix error: ${fixErr.message}`);
        break;
      }
    }
  }

  result.duration = ((Date.now() - result.startTime) / 1000).toFixed(1) + 's';
  const passed  = result.scenarios.filter(s => s.passed).length;
  const total   = result.scenarios.length;
  console.log(`\n  Summary: ${passed}/${total} scenarios passed`);

  fs.writeFileSync(OUTPUT, JSON.stringify(result, null, 2));
  appendStepSummary(result, passed, total);
  process.exit(result.passed ? 0 : 1);
}

async function runScenario(browser, browserName, scenario, environments, screenshotDir, videoDir) {
  const record = {
    browser: browserName,
    scenario: scenario.name,
    passed: false,
    error: null,
    screenshots: [],
    duration: null,
  };
  const t0 = Date.now();

  // Create one isolated context per environment
  const contexts = {};
  const pages = {};

  try {
    for (const env of environments) {
      const ctx = await browser.newContext({
        baseURL: env.baseURL,
        recordVideo: { dir: videoDir },
        extraHTTPHeaders: { 'X-Env-Name': env.name, 'X-Env-Role': env.role },
      });

      // Route FQDN → local server (simulates real DNS resolution per environment)
      const target = new URL(env.baseURL);
      await ctx.route(`**://${env.fqdn}/**`, route => {
        const orig = route.request().url();
        const rewritten = orig.replace(env.fqdn, target.host);
        route.continue({ url: rewritten });
      });

      contexts[env.name] = ctx;
      pages[env.name] = await ctx.newPage();
    }

    // Run the scenario with access to all environment pages
    await scenario.run(pages, environments);
    record.passed = true;

    // Capture final screenshots from each environment
    for (const [envName, page] of Object.entries(pages)) {
      const file = path.join(screenshotDir, `${scenario.name}-${browserName}-${envName}-pass.png`);
      await page.screenshot({ path: file, fullPage: true });
      record.screenshots.push(file);
    }

  } catch (err) {
    record.error = err.message;
    for (const [envName, page] of Object.entries(pages)) {
      try {
        const file = path.join(screenshotDir, `${scenario.name}-${browserName}-${envName}-fail.png`);
        await page.screenshot({ path: file, fullPage: true });
        record.screenshots.push(file);
      } catch { /* screenshot failed */ }
    }
  } finally {
    for (const ctx of Object.values(contexts)) {
      await ctx.close().catch(() => {});
    }
  }

  record.duration = ((Date.now() - t0) / 1000).toFixed(1) + 's';
  return record;
}

function loadScenarios(testDir) {
  if (fs.existsSync(testDir)) {
    const files = fs.readdirSync(testDir).filter(f => /\.(test|spec|scenario)\.[jt]s$/.test(f));
    if (files.length > 0) {
      const loaded = [];
      for (const f of files) {
        try {
          const mod = require(path.join(testDir, f));
          if (Array.isArray(mod)) loaded.push(...mod);
          else if (mod.name && mod.run) loaded.push(mod);
        } catch (e) {
          console.warn(`  ⚠ Could not load scenario file ${f}: ${e.message}`);
        }
      }
      if (loaded.length > 0) return loaded;
    }
  }

  // Default smoke scenarios when no test files exist
  return [
    {
      name: 'system-health-check',
      run: async (pages, envs) => {
        const systemEnv = envs.find(e => e.role === 'system-api');
        if (!systemEnv) return;
        const res = await pages[systemEnv.name].goto(`${systemEnv.baseURL}/healthz`);
        if (!res || res.status() >= 400) throw new Error(`Health returned ${res?.status()}`);
      },
    },
    {
      name: 'user-a-page-load',
      run: async (pages, envs) => {
        const envA = envs.find(e => e.role === 'sender');
        if (!envA) return;
        const res = await pages[envA.name].goto(envA.baseURL);
        if (!res || res.status() >= 400) throw new Error(`User-A page returned ${res?.status()}`);
      },
    },
    {
      name: 'user-b-page-load',
      run: async (pages, envs) => {
        const envB = envs.find(e => e.role === 'receiver');
        if (!envB) return;
        const res = await pages[envB.name].goto(envB.baseURL);
        if (!res || res.status() >= 400) throw new Error(`User-B page returned ${res?.status()}`);
      },
    },
  ];
}

function collectSrcFiles(projectRoot) {
  const files = [];
  const walk = (dir, depth = 0) => {
    if (depth > 3 || !fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full, depth + 1);
      else if (/\.[jt]s$/.test(entry.name) && !/\.(test|spec)/.test(entry.name)) files.push(full);
    }
  };
  ['src', 'lib', 'app'].forEach(d => walk(path.join(projectRoot, d)));
  return files.slice(0, 10);
}

function appendStepSummary(result, passed, total) {
  if (!process.env.GITHUB_STEP_SUMMARY) return;
  const icon = result.passed ? '✅' : '❌';
  fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY,
    `| ${icon} Gate 6 | ${result.name} | ${result.duration} | ${passed}/${total} scenarios |\n`);
}

main().catch(err => {
  console.error('Gate 6 fatal:', err);
  process.exit(1);
});
