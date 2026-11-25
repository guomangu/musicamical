// v4.0 strudel_loader.mjs — hooks de resolution ESM pour rendre @strudel/* utilisable
// en Node.js headless pur. Le bundle dist de @strudel/core importe "@kabelsalat/web"
// dont le "main" pointe vers un build CJS sans export nomme detectable : on le mappe
// vers son bundle ESM (dist/index.mjs) qui exporte bien SalatRepl.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const require = createRequire(import.meta.url);

function pkgFile(pkg, rel) {
  try {
    return path.join(path.dirname(require.resolve(`${pkg}/package.json`)), rel);
  } catch {
    return null;
  }
}

const KABELSALAT_ESM = pkgFile('@kabelsalat/web', 'dist/index.mjs');

export async function resolve(specifier, context, nextResolve) {
  if (specifier === '@kabelsalat/web' && KABELSALAT_ESM) {
    return { url: pathToFileURL(KABELSALAT_ESM).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
