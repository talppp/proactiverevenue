const Anthropic = require('@anthropic-ai/sdk');
const fs = require('fs');
const path = require('path');

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

const SYSTEM_PROMPT = `You are an expert software engineer fixing failing automated tests.
The project uses Python (FastAPI, pytest, asyncio). Analyze the failing test output and source code,
identify the root cause, and return a minimal fix.

Respond ONLY with a valid JSON object — no markdown, no prose outside the JSON:
{
  "rootCause": "one sentence describing the bug",
  "confidence": 0.0,
  "patches": [
    {
      "file": "relative/path/to/source.py",
      "description": "what this patch fixes",
      "old": "exact string to replace (must appear exactly once in the file)",
      "new": "replacement string"
    }
  ]
}

Rules:
- Never modify test files (test_*.py, conftest.py) — only fix source/implementation files
- Make the smallest possible change that eliminates the root cause
- Preserve Python indentation exactly (4 spaces)
- Each patch "old" must be unique within its file
- confidence: 1.0 = certain this fixes it, 0.0 = guessing
- Prefer fixing app/**/*.py source files, not scripts or build helpers`;

async function fixBug({ errorOutput, testFile, sourceFiles, gateName, attempt, model = 'claude-sonnet-4-6' }) {
  const testCode = fs.existsSync(testFile) ? fs.readFileSync(testFile, 'utf8').slice(0, 2000) : '(not found)';

  const sourceSections = (sourceFiles || [])
    .filter(f => fs.existsSync(f))
    .slice(0, 6)
    .map(f => `### ${path.relative(process.cwd(), f)}\n\`\`\`python\n${fs.readFileSync(f, 'utf8').slice(0, 1500)}\n\`\`\``)
    .join('\n\n');

  const response = await client.messages.create({
    model,
    max_tokens: 4096,
    system: SYSTEM_PROMPT,
    messages: [{
      role: 'user',
      content: `## Gate: ${gateName}  |  Fix Attempt: ${attempt + 1}

### Failing Test: ${testFile}
\`\`\`python
${testCode}
\`\`\`

### Error Output:
\`\`\`
${errorOutput.slice(0, 2500)}
\`\`\`

### Source Files:
${sourceSections || '(no source files found — patches should reference files under app/)'}`,
    }],
  });

  const raw = response.content[0].text
    .trim()
    .replace(/^```[a-z]*\n?/m, '')
    .replace(/\n?```$/m, '')
    .trim();

  try {
    return JSON.parse(raw);
  } catch {
    const match = raw.match(/\{[\s\S]*\}/);
    if (!match) throw new Error(`Non-JSON from Claude: ${raw.slice(0, 120)}`);
    return JSON.parse(match[0]);
  }
}

function applyPatches(patches, projectRoot = process.cwd()) {
  const results = [];
  for (const patch of (patches || [])) {
    const filePath = path.resolve(projectRoot, patch.file);

    if (!fs.existsSync(filePath)) {
      results.push({ file: patch.file, status: 'skipped', reason: 'file not found' });
      continue;
    }

    let content = fs.readFileSync(filePath, 'utf8');
    const escaped = patch.old.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const count = (content.match(new RegExp(escaped, 'g')) || []).length;

    if (count === 0) {
      results.push({ file: patch.file, status: 'skipped', reason: 'patch target not found in file' });
      continue;
    }
    if (count > 1) {
      results.push({ file: patch.file, status: 'skipped', reason: `ambiguous — ${count} matches` });
      continue;
    }

    fs.writeFileSync(filePath + '.bak', content);
    fs.writeFileSync(filePath, content.replace(patch.old, patch.new));
    results.push({ file: patch.file, status: 'applied', description: patch.description });
    console.log(`    ✓ Patched: ${patch.file} — ${patch.description}`);
  }
  return results;
}

module.exports = { fixBug, applyPatches };
