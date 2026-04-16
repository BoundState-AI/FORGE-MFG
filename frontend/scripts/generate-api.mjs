/**
 * Module:   API Schema Export + Type Generation
 * Purpose:  Regenerates openapi.json + TypeScript types from backend.
 * Rev:      0.2.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Usage:
 *   npm run generate-api
 *
 * This is the canonical regeneration step for the typed API client.
 * It extracts the OpenAPI spec from the FastAPI app in-process (no
 * running server required) and then generates TypeScript types via
 * openapi-typescript.
 *
 * Output files (both committed to the repo):
 *   src/api/openapi.json         — the raw OpenAPI 3.1 spec snapshot
 *   src/api/generated/schema.d.ts — TypeScript paths + components
 *
 * The generated file is consumed by src/api/typed-client.ts via the
 * openapi-fetch runtime.  Hand-written code should never reference the
 * generated schema file directly — always go through typed-client.
 */

import { spawnSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..', '..');
const frontendRoot = resolve(__dirname, '..');

const specPath = resolve(frontendRoot, 'src/api/openapi.json');
const schemaPath = resolve(frontendRoot, 'src/api/generated/schema.d.ts');

/**
 * Phase 1 — Extract OpenAPI spec from the FastAPI app.
 *
 * Why in-process extraction instead of starting a server:
 * - No database, S3, or Cognito dependencies needed.
 * - Runs in CI without spinning up docker-compose.
 * - The spec is deterministic from the route definitions alone.
 */
function extractOpenApiSpec() {
  console.log('Extracting OpenAPI spec from FastAPI app...');
  const python = process.platform === 'win32' ? 'python' : 'python3';
  const script = [
    'from forge.api.app import create_app',
    'from fastapi.openapi.utils import get_openapi',
    'import json, sys',
    'app = create_app()',
    'spec = get_openapi(title=app.title, version=app.version, routes=app.routes)',
    'sys.stdout.write(json.dumps(spec, indent=2))',
  ].join('; ');
  const result = spawnSync(python, ['-c', script], {
    cwd: repoRoot,
    encoding: 'utf-8',
    maxBuffer: 50 * 1024 * 1024,
  });
  if (result.status !== 0) {
    console.error('Failed to extract OpenAPI spec:');
    console.error(result.stderr);
    process.exit(1);
  }
  mkdirSync(dirname(specPath), { recursive: true });
  writeFileSync(specPath, result.stdout);
  const paths = JSON.parse(result.stdout).paths ?? {};
  console.log(`Wrote ${specPath} (${Object.keys(paths).length} paths)`);
}

/**
 * Phase 2 — Generate TypeScript types via openapi-typescript.
 */
function generateTypes() {
  console.log('Generating TypeScript types...');
  mkdirSync(dirname(schemaPath), { recursive: true });
  const result = spawnSync(
    'npx',
    ['openapi-typescript', specPath, '-o', schemaPath],
    { cwd: frontendRoot, stdio: 'inherit', shell: true },
  );
  if (result.status !== 0) process.exit(result.status ?? 1);
}

extractOpenApiSpec();
generateTypes();
console.log('Done.');
