/**
 * Module:   API Schema Export
 * Purpose:  Fetches OpenAPI JSON from running backend for client generation.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Usage: node scripts/generate-api.mjs
 *
 * Why this script: The backend exposes /openapi.json in non-production
 * environments.  This script fetches it and saves it locally so a
 * future Orval or openapi-generator step can consume it without
 * requiring a running backend during CI.
 */

import { writeFileSync } from 'fs';

const API_URL = process.env.API_URL || 'http://localhost:8000';

async function main() {
  console.log(`Fetching OpenAPI schema from ${API_URL}/openapi.json ...`);
  const res = await fetch(`${API_URL}/openapi.json`);
  if (!res.ok) {
    console.error(`Failed: ${res.status} ${res.statusText}`);
    process.exit(1);
  }
  const schema = await res.json();
  const outPath = 'src/api/openapi.json';
  writeFileSync(outPath, JSON.stringify(schema, null, 2));
  console.log(`Wrote ${outPath} (${schema.paths ? Object.keys(schema.paths).length : '?'} paths)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
