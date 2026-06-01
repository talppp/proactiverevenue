const path = require('path');

// ── Project root ─────────────────────────────────────────────────────────────
// Points at Phase1_AI_Issue_Router — the Python FastAPI project we're testing.
// Override via PROJECT_ROOT env var in CI or locally.
const PROJECT_ROOT = process.env.PROJECT_ROOT ||
  path.resolve(__dirname, '../Phase1_AI_Issue_Router');

module.exports = {
  projectRoot: PROJECT_ROOT,

  gates: {
    // ── Gate 1: Unit tests ─────────────────────────────────────────────────
    1: {
      name: 'Unit Tests + Auto-Fix',
      command: process.env.GATE1_CMD ||
        'pytest tests/test_l2_normalize.py tests/test_l4_router.py tests/test_p0_escalate.py -v --tb=short',
      testDir:  path.join(PROJECT_ROOT, 'tests'),
      srcDir:   path.join(PROJECT_ROOT, 'app'),
      maxFixes: 3,
    },

    // ── Gate 2: System / pipeline tests ───────────────────────────────────
    2: {
      name: 'System Tests + Auto-Fix',
      command: process.env.GATE2_CMD ||
        'pytest tests/test_e2e_pipeline.py -v --tb=short',
      testDir:  path.join(PROJECT_ROOT, 'tests'),
      srcDir:   path.join(PROJECT_ROOT, 'app'),
      maxFixes: 3,
    },

    // ── Gate 3: Stress + sanity + race-condition ───────────────────────────
    3: {
      name: 'Stress, Sanity & Race Condition Tests',
      // Python pytest stress suite (TestClient-based, no server needed)
      pytestStressCommand: process.env.GATE3_PYTEST_CMD ||
        'pytest proactiverevenue/tests/stress/ -v --tb=short',
      // HTTP stress via autocannon (needs uvicorn running)
      stressTarget:      process.env.STRESS_TARGET      || 'http://localhost:8000',
      stressDuration:    parseInt(process.env.STRESS_DURATION    || '10'),
      stressConnections: parseInt(process.env.STRESS_CONNECTIONS || '10'),
      raceWorkers:       parseInt(process.env.RACE_WORKERS       || '20'),
      raceEndpoint:      process.env.RACE_ENDPOINT               || '/healthz',
      sanityURL:         process.env.SANITY_URL                  || 'http://localhost:8000/healthz',
      srcDir:            path.join(PROJECT_ROOT, 'app'),
      maxFixes: 2,
    },

    // ── Gate 4: Full CI run + lint ─────────────────────────────────────────
    4: {
      name: 'CI Integration Tests',
      command: process.env.GATE4_CMD || 'pytest -q && ruff check .',
      testDir:  path.join(PROJECT_ROOT, 'tests'),
      srcDir:   path.join(PROJECT_ROOT, 'app'),
      maxFixes: 2,
    },

    // ── Gate 5: CD readiness ───────────────────────────────────────────────
    5: {
      name: 'CD Deployment Readiness',
      healthCheckURL:  process.env.HEALTH_URL    || 'http://localhost:8000/healthz',
      dockerComposeFile: process.env.COMPOSE_FILE || path.join(PROJECT_ROOT, 'docker-compose.yml'),
      requiredEnvVars: (process.env.REQUIRED_ENV_VARS || 'DB_URL').split(',').filter(Boolean),
      buildCommand:    process.env.BUILD_CMD      || 'pip install -e ".[dev]" -q',
      maxFixes: 1,
    },

    // ── Gate 6: Full E2E — Playwright, 3 agent environments ───────────────
    6: {
      name: 'Full E2E Multi-Environment Tests',
      testDir:       path.join(PROJECT_ROOT, 'tests'),
      srcDir:        path.join(PROJECT_ROOT, 'app'),
      maxFixes: 2,
      // Run only chromium for speed — add firefox/webkit when server is stable
      browsers:      (process.env.E2E_BROWSERS || 'chromium').split(','),
      screenshotDir: path.join(__dirname, '.pipeline/screenshots'),
      videoDir:      path.join(__dirname, '.pipeline/videos'),
      // All three environments hit the same server — different URL paths per agent
      environments: [
        {
          name:    'nathalie',
          role:    'agent-nathalie',
          baseURL: process.env.E2E_URL_NATHALIE || 'http://localhost:8000',
          fqdn:    'nathalie.apteker.internal',
          inboxPath: '/inbox/nathalie',
        },
        {
          name:    'fabi',
          role:    'agent-fabi',
          baseURL: process.env.E2E_URL_FABI || 'http://localhost:8000',
          fqdn:    'fabi.apteker.internal',
          inboxPath: '/inbox/fabi',
        },
        {
          name:    'system',
          role:    'system-api',
          baseURL: process.env.E2E_URL_SYS || 'http://localhost:8000',
          fqdn:    'api.apteker.internal',
          inboxPath: '/healthz',
        },
      ],
    },
  },

  claude: {
    model:     process.env.CLAUDE_MODEL || 'claude-sonnet-4-6',
    maxTokens: 4096,
  },

  report: {
    outputDir: path.join(__dirname, '.pipeline/reports'),
  },
};
