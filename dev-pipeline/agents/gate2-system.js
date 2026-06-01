const path = require('path');
const fs = require('fs');
const { runGate } = require('../lib/test-runner');
const config = require('../pipeline.config');

(async () => {
  const cfg = config.gates[2];
  const result = await runGate({
    gateNumber: 2,
    gateName:   cfg.name,
    command:    cfg.command,
    testFile:   path.join(cfg.testDir, 'test_e2e_pipeline.py'),
    sourceFiles: discoverPySrc(cfg.srcDir),
    maxFixes:   cfg.maxFixes,
    outputFile: path.join(__dirname, '../.pipeline/gate2.json'),
  });
  process.exit(result.passed ? 0 : 1);
})();

function discoverPySrc(srcDir) {
  const files = [];
  if (!fs.existsSync(srcDir)) return files;
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory() && entry.name !== '__pycache__') walk(full);
      else if (entry.name.endsWith('.py') && !entry.name.startsWith('test_')) files.push(full);
    }
  };
  walk(srcDir);
  return files.slice(0, 20);
}
