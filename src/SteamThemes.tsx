import {AppearanceFrame} from './AppearanceFrame';
import type {AppearancePack} from './AppearancePack';
import { ConsoleHome } from './ConsoleHome';
import { waveGames, waveSections, type WaveSection, type ThemeGame } from './ThemeLayouts';
import { CouchsideThemeHub } from './ThemeHub';
import { callable, routerHook } from '@decky/api';
import { DialogButton, Focusable, Navigation, NavEntryPositionPreferences, getGamepadNavigationTrees } from '@decky/ui';
import { Component, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';

// Original Couchside renderer. Theme packs are fixed layouts and settings;
// no remote scripts, CSS selectors or executable theme files are accepted.
type Settings = { installed: boolean; enabled: boolean; theme: string; accent: string; motion: boolean; appearance?: AppearancePack | null; studio?: { name: string; wallpaper: string; stats: boolean } | null };
type ThemeData = { revision?: string; settings: Settings; games: { appid: number; name: string }[] };
const wordmark = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMTYwIiBoZWlnaHQ9IjIxMCIgdmlld0JveD0iMCAwIDExNjAgMjEwIiByb2xlPSJpbWciIGFyaWEtbGFiZWw9IkNvdWNoc2lkZSI+CiAgPHN0eWxlPnRleHR7Zm9udC1mYW1pbHk6IlNwYWNlIE1vbm8iLHVpLW1vbm9zcGFjZSxTRk1vbm8tUmVndWxhcixNZW5sbyxtb25vc3BhY2U7Zm9udC13ZWlnaHQ6NzAwfTwvc3R5bGU+CiAgPHRleHQgeD0iMCIgeT0iMTUwIiBmb250LXNpemU9IjIwMCIgbGV0dGVyLXNwYWNpbmc9Ii02IiBmaWxsPSIjZTZlZGY3Ij5Db3VjaDx0c3BhbiBmaWxsPSIjMzRkMzk5Ij5zaWRlPC90c3Bhbj48L3RleHQ+Cjwvc3ZnPgo=';
const readTheme = callable<[], ThemeData>('get_steam_theme');
const reportTheme = callable<[string, string], boolean>('report_theme_ui');
const disabled: ThemeData = { settings: { installed: false, enabled: false, theme: 'couchside', accent: 'teal', motion: true }, games: [] };
let snapshot = disabled;
let paused = false;
const selectionKey = 'couchside.theme.selected-game.v1';
export function readThemeSelection(): number | undefined {
  try {
    const raw = localStorage.getItem(selectionKey);
    if (!raw || !/^[0-9]{1,16}$/.test(raw)) return undefined;
    const value = Number(raw);
    return Number.isSafeInteger(value) && value > 0 ? value : undefined;
  } catch { return undefined; }
}
export function rememberThemeSelection(appid: number) {
  if (!Number.isSafeInteger(appid) || appid <= 0) return;
  lastFocusedGame = appid;
  try { localStorage.setItem(selectionKey, String(appid)); } catch { /* Storage is optional. */ }
}
let lastFocusedGame = readThemeSelection();
const listeners = new Set<() => void>();
const notify = () => listeners.forEach(fn => fn());
const subscribe = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };
const useData = () => useSyncExternalStore(subscribe, () => snapshot);
export function pauseSteamTheme() { paused = true; snapshot = { ...snapshot }; notify(); Navigation.Navigate('/library/home'); }
export function openSteamThemeHub() { Navigation.Navigate('/couchside/theme-browser'); }
export function openSteamTheme() { paused = false; snapshot = { ...snapshot }; notify(); Navigation.Navigate('/couchside/themes'); }

class ThemeBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { void reportTheme('error', String(error.message).slice(0, 160)).catch(() => {}); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

// Ask Steam's registered navigation node to take focus; DOM focus alone
// can leave its controller selection pointing at an old layout.
export function focusRegisteredGame(element: HTMLElement) {
  const pending = (getGamepadNavigationTrees() || []).map((tree: any) => tree.Root ?? tree.m_Root);
  const seen = new Set<any>();
  while (pending.length && seen.size < 2048) {
    const node = pending.pop();
    if (!node || seen.has(node)) continue;
    seen.add(node);
    if ((node.Element ?? node.m_element) === element && typeof node.BTakeFocus === 'function') {
      node.BTakeFocus(2);
      return true;
    }
    const children = node.m_rgChildren;
    if (Array.isArray(children)) pending.push(...children);
  }
  return false;
}

type GameStats = { appid?: number; configured?: boolean; hours?: number; last_played?: number; achievements?: { unlocked: number; total: number; percent: number } };
const readGameStats = callable<[number], GameStats>('get_theme_game_stats');
function useGameStats(appid: number | undefined, enabled: boolean) {
  const [result, setResult] = useState<GameStats>({});
  useEffect(() => {
    setResult({});
    if (!enabled || !appid) return;
    let canceled = false;
    // Browse freely: only the settled highlight asks for cloud-backed metadata.
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout>;
    const load = () => { void readGameStats(appid).then(value => {
      if (canceled) return;
      if (value.appid === appid) setResult(value);
      // The agent's existing Steam caches warm asynchronously. Retry briefly,
      // without a perpetual poll or delaying controller input.
      if (++attempts < 3 && value.configured !== false && value.hours === undefined && !value.achievements)
        timer = setTimeout(load, 2000);
    }).catch(() => {}); };
    timer = setTimeout(load, 400);
    return () => { canceled = true; clearTimeout(timer); };
  }, [appid, enabled]);
  return result.appid === appid ? result : {};
}
function hoursLabel(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} h played` : undefined;
}
function GameStatsPanel({ game, stats, color }: { game: ThemeGame; stats: GameStats; color: string }) {
  const hours = hoursLabel(stats.hours);
  const a = stats.achievements;
  const achievements = a && Number.isInteger(a.total) && a.total > 0 && Number.isInteger(a.unlocked) && a.unlocked >= 0 && a.unlocked <= a.total;
  return <section aria-label={`${game.name} game stats`} style={{ marginTop: 16, color, fontSize: 15, lineHeight: 1.6 }}>
    {hours && <div>{hours}</div>}
    {achievements && <div>Achievements · {a.unlocked} / {a.total} · {Math.round(100 * a.unlocked / a.total)}%</div>}
    {typeof stats.last_played === 'number' && stats.last_played > 0 && <div>Last played · {new Date(stats.last_played * 1000).toLocaleDateString()}</div>}
    {!hours && !achievements && <div style={{ opacity: .75, fontSize: 13 }}>{stats.configured === false ? 'Enable Steam integration in Couchside for game stats.' : 'Game stats unavailable'}</div>}
  </section>;
}

function ConsoleGameDetails({ game, enabled }: { game: ThemeGame; enabled: boolean }) {
  const stats = useGameStats(game.appid, enabled);
  return enabled ? <GameStatsPanel game={game} stats={stats} color="#d8e5f5" /> : null;
}
function ThemeHome() {
  const { settings, games } = useData();
  if (settings.theme === 'crossbar' || settings.theme === 'horizon') {
    const background = ({ midnight: 'linear-gradient(135deg,#0c1128,#231b45)', ocean: 'linear-gradient(135deg,#073752,#0b7070)', forest: 'linear-gradient(135deg,#102d29,#294a32)', sunset: 'linear-gradient(135deg,#452c48,#995235)' } as Record<string,string>)[settings.studio?.wallpaper || ''];
    return <ConsoleHome layout={settings.theme} games={games} initialGame={lastFocusedGame} remember={rememberThemeSelection} focus={focusRegisteredGame} exit={pauseSteamTheme} openHub={openSteamThemeHub} motion={settings.motion} background={background} details={game => <ConsoleGameDetails game={game} enabled={settings.studio?.stats !== false} />} />;
  }
  return <StandardThemeHome />;
}
function StandardThemeHome() {
  const { settings, games } = useData();
  const gameNodes = useRef(new Map<number, HTMLDivElement>());
  const [focusedGame, setFocusedGame] = useState<number | null>(null);
  const crossbar = settings.theme === 'crossbar';
  const horizon = settings.theme === 'horizon';
  const wave = settings.theme === 'wave' || crossbar;
  const channels = settings.theme === 'channels';
  const dashboard = settings.theme === 'couchside';
  const [section, setSection] = useState<WaveSection>('all');
  const visibleGames = wave ? waveGames(games, section) : games;
  const preferredGame = visibleGames.some(game => game.appid === lastFocusedGame) ? lastFocusedGame : visibleGames[0]?.appid;
  const restoreFinished = useRef(false);
  useEffect(() => {
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout>;
    const restore = () => {
      if (restoreFinished.current) return;
      const node = preferredGame === undefined ? undefined : gameNodes.current.get(preferredGame);
      if (node) {
        node.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'auto' });
        focusRegisteredGame(node);
      }
      if (++attempts < 15 && !restoreFinished.current) timer = setTimeout(restore, 100);
    };
    timer = setTimeout(restore, 0);
    return () => clearTimeout(timer);
  }, []);
  useEffect(() => { void reportTheme('rendered', settings.theme).catch(() => {}); }, [settings.theme]);
  const accent = ({ teal: '#26d4be', blue: '#73b5ff', violet: '#b5a0ff', amber: '#ffcf7b' } as Record<string, string>)[settings.accent] || '#26d4be';
  const carousel = settings.theme === 'orbit' || horizon;
  const grid = settings.theme === 'shelf';
  const light = channels;
  const foreground = light ? '#142a43' : '#f0f4fa';
  const selected = visibleGames.find(game => game.appid === focusedGame) || visibleGames.find(game => game.appid === preferredGame);
  useEffect(() => {
    if (!horizon || !selected) return;
    const timer = setTimeout(() => gameNodes.current.get(selected.appid)?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'auto' }), settings.motion ? 220 : 0);
    return () => clearTimeout(timer);
  }, [selected?.appid, horizon, settings.motion]);
  const showStats = settings.studio?.stats !== false;
  const stats = useGameStats(selected?.appid, showStats && (wave || channels || horizon || settings.theme === 'couchside'));
  const studioBackground = ({ midnight: 'linear-gradient(135deg,#0c1128,#231b45)', ocean: 'linear-gradient(135deg,#073752,#0b7070)', forest: 'linear-gradient(135deg,#102d29,#294a32)', sunset: 'linear-gradient(135deg,#452c48,#995235)' } as Record<string, string>)[settings.studio?.wallpaper || ''];
  const art = (game: ThemeGame, poster = false) => `https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/${game.appid}/${poster ? 'library_600x900.jpg' : 'header.jpg'}`;
  const renderGame = (game: ThemeGame, index: number) => <DialogButton key={game.appid}
    ref={node => { if (node) gameNodes.current.set(game.appid, node); else gameNodes.current.delete(game.appid); }}
    preferredFocus={game.appid === preferredGame} className="cs-theme-game" data-selected={game.appid === selected?.appid ? "true" : "false"}
    onGamepadFocus={() => { restoreFinished.current = true; rememberThemeSelection(game.appid); setFocusedGame(game.appid); gameNodes.current.get(game.appid)?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'auto' }); }}
    onGamepadBlur={() => setFocusedGame(null)}
    onClick={() => { rememberThemeSelection(game.appid); Navigation.Navigate(`/library/app/${game.appid}`); }}
    style={{ flexShrink: 0, flexGrow: 0, boxSizing: 'border-box', width: horizon ? (game.appid === selected?.appid ? 252 : 170) : wave ? '100%' : channels ? 'calc(33.333% - 16px)' : grid ? 155 : !carousel && index === 0 ? 310 : 200,
      minWidth: channels ? 180 : undefined, display: wave ? 'flex' : 'block', alignItems: 'center', gap: wave ? 20 : undefined,
      padding: wave ? 10 : channels ? 10 : 0, borderRadius: crossbar || horizon ? 4 : channels ? 24 : wave ? 16 : grid ? 12 : 18, overflow: 'hidden',
      background: crossbar ? 'transparent' : channels ? '#ffffff' : wave ? '#142d3dcc' : '#19263a',
      border: `3px solid ${focusedGame === game.appid ? accent : crossbar ? 'transparent' : channels ? '#bdccdf' : '#30445c'}`,
      boxShadow: focusedGame === game.appid ? `0 0 0 3px ${channels ? '#e8eff8' : '#0c1321'}, 0 0 0 6px ${accent}` : channels ? '0 8px 20px #15314a12' : 'none',
      transition: settings.motion ? 'box-shadow 160ms ease,border-color 160ms ease' : 'none', textAlign: 'left', color: foreground }}>
      <div style={{ flexShrink: 0, width: crossbar ? 72 : wave ? 145 : '100%', height: horizon ? (game.appid === selected?.appid ? 246 : 164) : crossbar ? 64 : wave ? 76 : channels ? 100 : grid ? 100 : horizon ? 180 : carousel ? 230 : 150,
        background: `linear-gradient(140deg,${accent}22,#152238)`, borderRadius: wave || channels ? 12 : 0, overflow: 'hidden' }}>
        <img alt="" loading="lazy" src={art(game, carousel && !horizon)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} onError={e => { e.currentTarget.style.display = 'none'; }} />
      </div>
      <div style={{ padding: wave ? 4 : channels ? '12px 4px 4px' : 15, whiteSpace: 'normal', fontSize: wave ? 20 : channels ? 17 : grid ? 13 : 17, minHeight: wave ? 0 : channels ? 36 : 42, color: foreground }}>
        {horizon ? (game.appid === selected?.appid ? 'Start' : '') : game.name}{channels && <div style={{ marginTop: 6, fontSize: 12, color: '#4d627a', letterSpacing: 1 }}>{showStats && game.appid === selected?.appid && hoursLabel(stats.hours) ? hoursLabel(stats.hours) : 'GAME CHANNEL'}</div>}
      </div>
    </DialogButton>;
  return <Focusable className={crossbar ? "cs-console-crossbar" : horizon ? "cs-console-horizon" : undefined} preferredFocus navEntryPreferPosition={NavEntryPositionPreferences.PREFERRED_CHILD} flow-children="column"
    onCancel={pauseSteamTheme} onGamepadDirection={() => { restoreFinished.current = true; }}
    style={{ height: '100%', maxHeight: 'calc(100vh - 100px)', boxSizing: 'border-box', overflow: 'auto', position: 'relative', isolation: 'isolate',
      padding: channels ? '48px 32px 24px' : '40px 48px', background: studioBackground || (crossbar ? 'radial-gradient(ellipse at 75% 90%,#9a7332 0%,#4b3c22 42%,#171c2c 100%)' : horizon ? 'linear-gradient(150deg,#1554a0,#082451 65%,#07162f)' : channels ? '#e8eff8' : wave ? 'radial-gradient(ellipse at 80% 10%,#256b73 0%,#122d42 48%,#0b192c 100%)' : grid ? '#111821' : '#0c1321'), color: foreground, fontFamily: 'sans-serif' }}>
    <style>{`
      .cs-console-crossbar .cs-theme-header,.cs-console-horizon .cs-theme-header{margin-bottom:26px!important}
      .cs-console-crossbar .cs-theme-header h1,.cs-console-horizon .cs-theme-header h1,.cs-console-crossbar .cs-theme-header img+ h1+div,.cs-console-horizon .cs-theme-header img+ h1+div{display:none}
      .cs-console-crossbar .cs-theme-header img,.cs-console-horizon .cs-theme-header img{width:140px!important;height:26px!important}
      .cs-console-crossbar .cs-theme-game{border:0!important;box-shadow:none!important;border-radius:0!important;opacity:.6}
      .cs-console-crossbar .cs-theme-game[data-selected=true]{opacity:1;background:linear-gradient(90deg,#ffffff18,transparent)!important;text-shadow:0 0 16px #fff8}
      .cs-console-crossbar .cs-theme-game:focus,.cs-console-crossbar .cs-theme-game.gpfocus{outline:1px solid #fff9;outline-offset:0}
      .cs-console-crossbar .cs-console-categories button{font-weight:400!important;opacity:.7}
      .cs-console-crossbar .cs-console-categories button:focus,.cs-console-crossbar .cs-console-categories button.gpfocus{opacity:1;text-shadow:0 0 20px #fff;outline:1px solid #fff9}
      .cs-console-horizon .cs-theme-game{border-radius:0!important;border-color:transparent!important;background:transparent!important;box-shadow:none!important;transition:${settings.motion ? 'width 180ms ease' : 'none'}!important}
      .cs-console-horizon .cs-theme-game[data-selected=true]{border-color:#fff!important;box-shadow:0 0 20px #66b7ff55!important;background:#174a82!important}
      .cs-console-horizon .cs-theme-game[data-selected=true]>div:last-child{text-align:center;padding:8px!important;min-height:0!important}
      .cs-console-horizon .cs-theme-game[data-selected=false]>div:last-child{display:none}
      @media(prefers-reduced-motion:reduce){.cs-console-horizon .cs-theme-game{transition:none!important}}
    .cs-theme-game:focus,.cs-theme-game.gpfocus{outline:3px solid ${accent};outline-offset:3px}.cs-theme-game:hover{border-color:${accent}!important}@keyframes cs-wave-drift{from{transform:translateX(-3%) rotate(-7deg)}to{transform:translateX(3%) rotate(-3deg)}}@media(prefers-reduced-motion:reduce){.cs-theme-game{transition:none!important}.cs-wave-band{animation:none!important}}`}</style>
    {wave && <div aria-hidden="true" className="cs-wave-band" style={{ position: 'fixed', pointerEvents: 'none', zIndex: -1, bottom: '-25%', left: '-20%', width: '140%', height: '65%', borderRadius: '50%', borderTop: `2px solid ${accent}66`, background: `linear-gradient(180deg,${accent}22,transparent)`, animation: settings.motion ? 'cs-wave-drift 12s ease-in-out infinite alternate' : 'none' }} />}
    <header className="cs-theme-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 24, marginBottom: channels ? 18 : 26, ...(channels ? { background: '#152a43', padding: '12px 20px', borderRadius: 24, color: '#f0f4fa' } : {}) }}>
      <div><img src={wordmark} alt="Couchside" style={{ width: channels ? 140 : 170, height: channels ? 24 : 31 }} /><h1 style={{ margin: channels ? '4px 0' : '8px 0', fontSize: channels ? 26 : 32 }}>{crossbar ? 'Crossbar' : horizon ? 'Horizon' : wave ? 'Find your flow' : channels ? 'Pick a channel' : carousel ? 'Your next adventure' : grid ? 'Your game shelf' : 'Make yourself comfortable'}</h1><div style={{ color: '#b7c9db', fontSize: channels ? 14 : 16 }}>{crossbar ? 'Across your world. Into your games.' : horizon ? 'Your next game is right here.' : wave ? 'Your library, one direction at a time.' : channels ? 'A whole library of possibilities.' : 'Your games. Your space.'}</div></div>
      <DialogButton onClick={pauseSteamTheme} style={{ width: 150, flexGrow: 0, flexShrink: 0, background: '#19263a', color: '#f0f4fa', padding: 12, border: '1px solid #56718f', borderRadius: 10 }}>Steam home</DialogButton>
    </header>

    {crossbar && <Focusable flow-children="row" aria-label="Console categories" className="cs-console-categories" style={{ display: 'flex', gap: 20, margin: '12px 0 26px' }}>
      {[{ label: 'Games', icon: '✦', path: null }, { label: 'Library', icon: '▦', path: '/library' }, { label: 'Downloads', icon: '↓', path: '/downloads' }, { label: 'Settings', icon: '⚙', path: '/settings' }].map(item => <DialogButton key={item.label} onClick={() => { if (item.path) Navigation.Navigate(item.path); }} style={{ width: 140, flexGrow: 0, background: 'transparent', color: '#fff5df', border: '1px solid transparent', borderRadius: 4, padding: 12 }}><div style={{ fontSize: 30, marginBottom: 8 }}>{item.icon}</div>{item.label}</DialogButton>)}
    </Focusable>}
    {wave && !crossbar && <Focusable flow-children="row" style={{ display: 'flex', gap: 16, marginBottom: 24, flexWrap: 'wrap' }}>{waveSections.map(item => <DialogButton key={item.id} onClick={() => setSection(item.id)}
      style={{ width: 'auto', flexGrow: 0, padding: '12px 22px', color: '#f0f4fa', background: section === item.id ? '#246879' : '#142d3d', border: `2px solid ${section === item.id ? accent : '#426175'}`, borderRadius: 24 }}>
      {item.label} <span style={{ opacity: .7 }}>({waveGames(games, item.id).length})</span></DialogButton>)}</Focusable>}
    {!visibleGames.length && <div style={{ padding: 32, border: '1px solid #63768c', borderRadius: 18 }}><h2>{games.length ? 'No games in this group' : 'Your library starts here'}</h2><p>{games.length ? 'Choose another group to browse your installed library.' : 'Installed Steam games appear here. Use Steam’s library to install or browse games.'}</p>{!games.length && <DialogButton onClick={() => Navigation.Navigate('/library')}>Open Steam library</DialogButton>}</div>}
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 40 }}>
      <Focusable className="cs-theme-library" preferredFocus navEntryPreferPosition={NavEntryPositionPreferences.PREFERRED_CHILD} flow-children={wave || channels ? 'column' : 'row'}
        style={{ display: 'flex', flexDirection: wave || channels ? 'column' : 'row', gap: channels ? 24 : grid ? 14 : 22, flexWrap: carousel || wave ? 'nowrap' : 'wrap', overflowX: carousel ? 'auto' : undefined, overflowY: crossbar ? 'auto' : undefined, maxHeight: crossbar ? 'calc(100vh - 440px)' : undefined, alignItems: horizon ? 'flex-start' : undefined, minHeight: horizon ? 315 : undefined, padding: 10, width: wave || dashboard ? '60%' : '100%', boxSizing: 'border-box' }}>
        {channels ? Array.from({ length: Math.ceil(visibleGames.length / 3) }, (_, row) => <Focusable key={row} flow-children="row" style={{ display: 'flex', gap: 24, width: '100%', flexShrink: 0 }}>{visibleGames.slice(row * 3, row * 3 + 3).map(renderGame)}</Focusable>) : visibleGames.map(renderGame)}
      </Focusable>
      {(wave || dashboard) && selected && <aside aria-label="Highlighted game" style={{ position: 'sticky', top: 16, width: '34%', paddingTop: 10, flexShrink: 0 }}>
        <img alt="" src={art(selected)} style={{ width: '100%', aspectRatio: '16 / 9', objectFit: 'cover', borderRadius: 20, background: '#142d3d', border: '1px solid #527184' }} onError={e => { e.currentTarget.style.visibility = 'hidden'; }} />
        <div style={{ color: accent, letterSpacing: 3, fontSize: 12, marginTop: 22 }}>READY WHEN YOU ARE</div><h2 style={{ fontSize: 26, margin: '12px 0', lineHeight: 1.15, color: '#f0f4fa' }}>{selected.name}</h2>{showStats && <GameStatsPanel game={selected} stats={stats} color="#bed1de" />}<p style={{ color: '#bed1de', fontSize: 13, marginBottom: 0 }}>A opens the game page. B returns to Steam.</p>
      </aside>}
    </div>

    {horizon && selected && <section aria-label="Selected game" style={{ padding: '22px 10px', color: '#f0f4fa' }}><h2 style={{ fontSize: 30, margin: '0 0 8px' }}>{selected.name}</h2>{showStats && <GameStatsPanel game={selected} stats={stats} color="#c0d9f8" />}</section>}
    <footer style={{ margin: '24px 10px', color: channels ? '#4d627a' : '#a9b9cd', fontSize: 13 }}>Change themes from Couchside on your phone · A to open a game</footer>
  </Focusable>;
}

function ThemeGate({ fallback }: { fallback: ReactNode }) {
  const { settings } = useData();
  if (!settings.installed || !settings.enabled || paused) return <>{fallback}</>;
  return <ThemeBoundary fallback={fallback}>{settings.appearance ? <AppearanceFrame pack={settings.appearance}><ThemeHome key={settings.theme}/></AppearanceFrame> : <ThemeHome key={settings.theme} />}</ThemeBoundary>;
}
function ThemeRoute() {
  const data = useData();
  return data.settings.enabled && !paused ? <ThemeBoundary fallback={<DialogButton onClick={pauseSteamTheme}>Return to Steam</DialogButton>}>{data.settings.appearance ? <AppearanceFrame pack={data.settings.appearance}><ThemeHome key={data.settings.theme}/></AppearanceFrame> : <ThemeHome key={data.settings.theme} />}</ThemeBoundary> : <DialogButton onClick={pauseSteamTheme}>Themes are off. Return to Steam</DialogButton>;
}

export function installSteamThemes() {
  let stopped = false;
  let busy = false;
  let lastSettings = '';
  const poll = async () => {
    if (stopped || busy) return;
    busy = true;
    const watchdog = setTimeout(() => { if (!stopped) { snapshot = disabled; notify(); } }, 8000);
    try {
      const value = await readTheme();
      if (stopped) return;
      if (!value || !value.settings || !Array.isArray(value.games)) throw new Error('Invalid theme state');
      const key = JSON.stringify([value.settings, value.revision]);
      const hadSettings = lastSettings !== '';
      const changed = key !== lastSettings;
      if (changed) { paused = false; lastSettings = key; }
      if (JSON.stringify(value) !== JSON.stringify(snapshot)) { snapshot = value; notify(); }
      // Apply is an explicit phone action: open our supported route even when
      // Steam's cached home element has not been re-patched yet.
      if (changed) {
        if (value.settings.installed && value.settings.enabled) Navigation.Navigate('/couchside/themes');
        else if (hadSettings) Navigation.Navigate('/library/home');
      }
    } catch { if (!stopped) { snapshot = disabled; notify(); } }
    finally { clearTimeout(watchdog); busy = false; }
  };
  routerHook.addRoute('/couchside/theme-browser', () => <ThemeBoundary fallback={<DialogButton onClick={pauseSteamTheme}>Return to Steam</DialogButton>}><CouchsideThemeHub /></ThemeBoundary>);
  routerHook.addRoute('/couchside/themes', ThemeRoute);
  // Preserve the original element and every route prop; unsupported route
  // shapes are left intact, with the dedicated Themes route still available.
  let patch: ReturnType<typeof routerHook.addPatch> | undefined;
  try { patch = routerHook.addPatch('/library/home', route => {
    if (!route.children) return route;
    return { ...route, children: <ThemeGate fallback={route.children} /> };
  }); } catch { /* Dedicated Themes route remains usable on unsupported Steam builds. */ }
  void poll();
  const timer = setInterval(() => { void poll(); }, 3000);
  return () => { stopped = true; clearInterval(timer); snapshot = disabled; paused = false; notify(); if (patch) routerHook.removePatch('/library/home', patch); routerHook.removeRoute('/couchside/themes'); routerHook.removeRoute('/couchside/theme-browser'); };
}
