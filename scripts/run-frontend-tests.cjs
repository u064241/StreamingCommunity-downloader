#!/usr/bin/env node
// Single entry point for the frontend tests (tests/*.cjs).
// Used locally and in CI: `node scripts/run-frontend-tests.cjs`.
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

const root = path.resolve(__dirname, '..');
const files = fs.readdirSync(path.join(root, 'tests'))
  .filter((name) => name.endsWith('.cjs'))
  .sort()
  .map((name) => path.join('tests', name));

if (files.length === 0) {
  console.error('No frontend test files found in tests/.');
  process.exit(1);
}

console.log(`Running ${files.length} frontend test files.`);
const result = spawnSync(process.execPath, ['--test', ...files], {cwd: root, stdio: 'inherit'});
if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
