// Execute the production renderer with Decky/React shims: no Steam session changed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
let payload = { settings: { installed: true, enabled: true, theme: 'couchside', accent: 'teal', motion: true }, games: [{ appid: 123, name: '<script>Game & friends</script>' }, { appid: 456, name: 'Second game' }] };
let fail = false, tick, route, patch, removed = 0;
const navigation = [];
const listeners = new Set();
const jsx = (type, props) => ({ type, props });
class Component { constructor(props) { this.props = props; } }
const react = { Component, useRef: initial => ({current: initial}), useState: initial => [typeof initial === "function" ? initial() : initial, () => {}], useEffect: fn => fn(), useSyncExternalStore: (_, get) => get() };
let navTrees = [];
const ui = { GamepadButton: { OK:1,CANCEL:2,DIR_UP:9,DIR_DOWN:10,DIR_LEFT:11,DIR_RIGHT:12 }, getGamepadNavigationTrees: () => navTrees, DialogButton: 'button', Focusable: 'focusable', NavEntryPositionPreferences: { PREFERRED_CHILD: 4 }, Navigation: { Navigate: p => navigation.push(p) } };
const api = { callable: () => async () => { if (fail) throw Error('offline'); return payload; }, routerHook: {
  addRoute: (path, fn) => { if (path === '/couchside/themes') route = fn; }, addPatch: (_, fn) => { patch = fn; return fn; },
  removeRoute: () => { removed++; }, removePatch: () => { removed++; },
} };
const layoutModule = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/ThemeLayouts.ts', 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { module: layoutModule, exports: layoutModule.exports });
const navModule={exports:{}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/ConsoleNavigation.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,{module:navModule,exports:navModule.exports});
const consoleModule={exports:{}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/ConsoleHome.tsx','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,{module:consoleModule,exports:consoleModule.exports,require:name=>name==='./ConsoleNavigation'?navModule.exports:name==='react'?react:name==='react/jsx-runtime'?{jsx,jsxs:jsx,Fragment:'fragment'}:ui,setTimeout:()=>1,clearTimeout:()=>{}});
const moduleShim = { exports: {} };
const storage = new Map();
const js = ts.transpileModule(fs.readFileSync('src/SteamThemes.tsx', 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText;
vm.runInNewContext(js, { module: moduleShim, exports: moduleShim.exports,
  require: name => name === './ConsoleHome' ? consoleModule.exports : name === './ThemeLayouts' ? layoutModule.exports : name === './ThemeHub' ? { CouchsideThemeHub: () => null } : name === 'react' ? react : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx, Fragment: 'fragment' } : name === '@decky/ui' ? ui : api,
  localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
  setInterval: fn => { tick = fn; return 1; }, clearInterval: () => {},
  setTimeout: () => 1, clearTimeout: () => {}, console, Set,
});
const expand = node => {
  if (node == null || typeof node !== 'object') return node;
  if (Array.isArray(node)) return node.map(expand);
  if (typeof node.type === 'function') {
    if (node.type.prototype instanceof Component) return expand(new node.type(node.props).render());
    return expand(node.type(node.props));
  }
  return { type: node.type, props: { ...node.props, children: expand(node.props?.children) } };
};
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const target = {}; let tookFocus = 0;
  navTrees = [{Root: {m_rgChildren: [{Element: target, BTakeFocus: () => { tookFocus++; }}]}}];
  assert.equal(moduleShim.exports.focusRegisteredGame(target), true);
  assert.equal(tookFocus, 1);
  assert.equal(moduleShim.exports.focusRegisteredGame({}), false);
  navTrees = [];
  const stop = moduleShim.exports.installSteamThemes(); await settle();
  const original = { type: 'original-steam-home', props: {} };
  const patched = patch({ children: original, exact: true });
  assert.equal(patched.exact, true);
  assert.equal(patch({ component: 'unrecognized' }).component, 'unrecognized');
  for (const theme of ['couchside', 'orbit', 'shelf', 'wave', 'crossbar', 'horizon', 'channels']) {
    payload = { ...payload, settings: { ...payload.settings, theme } }; tick(); await settle();
    const rendered = expand(route());
    assert.equal(rendered.type, 'focusable');
    const text = JSON.stringify(rendered);
    assert(text.includes('Game & friends')); assert(text.includes('/steam/apps/123/' + (theme === 'orbit' ? 'library_600x900.jpg' : 'header.jpg')));
  }
  for (const layout of ['crossbar', 'horizon']) {
    const remembered = [], opened = [];
    const root = consoleModule.exports.ConsoleHome({ layout, games: payload.games, initialGame:123, remember:id=>remembered.push(id), focus:()=>true, exit:()=>opened.push('exit'), openHub:()=>{}, details:()=>null, motion:false });
    assert.equal(root.props.onButtonDown, undefined, 'Use Steam semantic direction/action events');
    const press=(handler,button)=>{ let prevented=false,stopped=false; root.props[handler]({detail:{button},preventDefault(){prevented=true},stopPropagation(){stopped=true}}); assert(prevented&&stopped); };
    if(layout==='crossbar') { press('onGamepadDirection',10); press('onGamepadDirection',11); press('onGamepadDirection',12); }
    else { press('onGamepadDirection',12); press('onGamepadDirection',9); press('onGamepadDirection',11); press('onGamepadDirection',10); }
    press('onOKButton',1); assert.equal(navigation.at(-1),'/library/app/456');
    assert.equal(remembered.at(-1),456);
    press('onCancelButton',2);
    if(layout==='crossbar')press('onCancelButton',2);
    assert.equal(opened.at(-1),'exit');
  }
  const gameButtons = node => {
    if (!node || typeof node !== 'object') return [];
    if (Array.isArray(node)) return node.flatMap(gameButtons);
    return [...(node.props?.className === 'cs-theme-game' ? [node] : []), ...gameButtons(node.props?.children)];
  };
  let cards = gameButtons(expand(route()));
  assert.equal(cards[0].props.preferredFocus, true);
  let scrolled;
  cards[1].props.ref({ scrollIntoView: options => { scrolled = options; } });
  cards[1].props.onGamepadFocus();
  assert.equal(scrolled.block, 'nearest');
  assert.equal(scrolled.inline, 'nearest');
  assert.equal(cards[1].props.style.color, '#142a43', 'Channels uses dark readable titles on white tiles');
  cards[1].props.onClick();
  assert.equal(navigation.at(-1), '/library/app/456');
  cards = gameButtons(expand(route()));
  assert.equal(cards[1].props.preferredFocus, true, 'Return to selected game');
  assert.equal(cards[0].props.preferredFocus, false);
  payload = { ...payload, settings: { ...payload.settings, theme: 'orbit' } }; tick(); await settle();
  assert.equal(gameButtons(expand(route()))[1].props.preferredFocus, true, 'Same game across layout switch');
  moduleShim.exports.pauseSteamTheme();
  assert.equal(navigation.at(-1), '/library/home');
  assert(JSON.stringify(expand(patched.children)).includes('original-steam-home'));
  payload = { ...payload, revision: 'reapply-same-theme' }; tick(); await settle();
  assert.equal(expand(patched.children).type, 'focusable');
  moduleShim.exports.openSteamTheme();
  assert.equal(navigation.at(-1), '/couchside/themes');
  payload = { ...payload, settings: { ...payload.settings, enabled: false } }; tick(); await settle();
  assert(JSON.stringify(expand(patched.children)).includes('original-steam-home'));
  payload = { ...payload, settings: { ...payload.settings, enabled: true } }; tick(); await settle();
  fail = true; tick(); await settle();
  assert(JSON.stringify(expand(patched.children)).includes('original-steam-home'));
  assert.equal(moduleShim.exports.readThemeSelection(), 456, 'Game survives Steam UI module reload');
  storage.set('couchside.theme.selected-game.v1', '<script>');
  assert.equal(moduleShim.exports.readThemeSelection(), undefined);
  moduleShim.exports.rememberThemeSelection(-1);
  assert.equal(storage.get('couchside.theme.selected-game.v1'), '<script>');
  stop(); assert.equal(removed, 3);
  console.log('PASS: seven layouts, route preservation, pause/resume, disabled/offline fallback and cleanup');
})().catch(error => { console.error(error); process.exitCode = 1; });
