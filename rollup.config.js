import deckyPlugin from "@decky/rollup";

// @decky/rollup bundles src/index.tsx (+ its imports) into dist/index.js the way
// Decky Loader expects. Everything else (plugin.json, main.py, defaults/) is
// copied by Decky at package time.
export default deckyPlugin();
