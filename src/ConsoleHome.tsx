import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Focusable, Navigation, GamepadButton, type GamepadEvent } from '@decky/ui';
import { backConsole, consoleCategories, consoleItem, initialConsoleNav, moveConsole, type ConsoleCategory, type ConsoleLayout, type ConsoleNav } from './ConsoleNavigation';
import type { ThemeGame } from './ThemeLayouts';
const memory = new Map<ConsoleLayout, ConsoleNav>();
const labels = { settings: 'Settings', library: 'Library', games: 'Games', downloads: 'Downloads' };
const routes = { settings: '/settings', library: '/library', downloads: '/downloads' };
function Icon({ kind }: { kind: ConsoleCategory }) {
  return <svg width="42" height="42" viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true">{kind === 'games' ? <><path d="M14 15h20c5 0 9 18 6 21-3 3-8-5-11-5H19c-3 0-8 8-11 5-3-3 1-21 6-21Z" /><path d="M13 23h10m-5-5v10" /><circle cx="31" cy="22" r="1.2" /><circle cx="35" cy="27" r="1.2" /></> : kind === 'library' ? <><rect x="7" y="10" width="9" height="28" /><rect x="20" y="10" width="9" height="28" /><path d="m33 11 7-1 4 27-7 1Z" /></> : kind === 'downloads' ? <><path d="M24 7v23m-9-9 9 10 9-10M9 32v8h30v-8" /></> : <><circle cx="24" cy="24" r="10" /><circle cx="24" cy="24" r="4" />{[0,45,90,135].map(a => <path key={a} transform={`rotate(${a} 24 24)`} d="M24 5v9m0 20v9" />)}</>}</svg>;
}
type Props = { layout: ConsoleLayout; games: ThemeGame[]; initialGame?: number; remember: (id: number) => void; focus: (node: HTMLElement) => boolean; exit: () => void; openHub: () => void; details: (game: ThemeGame) => ReactNode; motion: boolean; background?: string };
export function ConsoleHome(p: Props) {
  const root = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<ConsoleNav>(() => memory.get(p.layout) || initialConsoleNav(p.initialGame));
  const stateRef = useRef(state); stateRef.current = state;
  const items = { settings: [{ id: 'settings', name: 'Steam settings' }, { id: 'themes', name: 'Couchside themes' }, { id: 'default', name: 'Steam default' }], library: [{ id: 'library', name: 'All games' }], games: p.games.map(g => ({ id: String(g.appid), name: g.name })), downloads: [{ id: 'downloads', name: 'Manage downloads' }] };
  const lists = { settings: items.settings.map(x => x.id), library: items.library.map(x => x.id), games: items.games.map(x => x.id), downloads: items.downloads.map(x => x.id) };
  const activeCategory = p.layout === 'horizon' ? 'games' : state.category;
  const activeId = consoleItem({ ...state, category: activeCategory }, lists);
  const index = Math.max(0, lists[activeCategory].indexOf(activeId || ''));
  const game = activeCategory === 'games' ? p.games.find(g => String(g.appid) === activeId) : undefined;
  const categoryIndex = consoleCategories.indexOf(state.category);
  function commit(next: ConsoleNav) { memory.set(p.layout, next); stateRef.current = next; setState(next); const selected = next.selected.games; if (selected && /^\d+$/.test(selected)) p.remember(Number(selected)); }
  useEffect(() => { let attempts = 0; let timer: ReturnType<typeof setTimeout>; const restore = () => { if (root.current && p.focus(root.current)) return; if (++attempts < 15) timer = setTimeout(restore,100); }; timer=setTimeout(restore,0); return () => clearTimeout(timer); }, []);
  function open(id: string | undefined) {
    if (!id) return;
    if (/^\d+$/.test(id)) { p.remember(Number(id)); commit({ ...stateRef.current, selected: { ...stateRef.current.selected, games: id } }); Navigation.Navigate(`/library/app/${id}`); }
    else if (id === 'themes') p.openHub(); else if (id === 'default') p.exit(); else if (id in routes) Navigation.Navigate(routes[id as keyof typeof routes]);
  }
  function activate() { const s=stateRef.current; if (s.row==='bar') { if(p.layout==='crossbar'||s.category==='games')commit({...s,row:'items'});else open(s.category); } else open(consoleItem({...s,category:p.layout==='horizon'?'games':s.category},lists)); }
  function cancel() { const next=backConsole(p.layout,stateRef.current); if(next)commit(next);else p.exit(); }
  function button(event: GamepadEvent) {
    const key=event.detail.button;
    const directions: Partial<Record<GamepadButton, 'left' | 'right' | 'up' | 'down'>> = {
      [GamepadButton.DIR_LEFT]: 'left', [GamepadButton.DIR_RIGHT]: 'right',
      [GamepadButton.DIR_UP]: 'up', [GamepadButton.DIR_DOWN]: 'down',
    };
    const direction = directions[key];
    if (!direction && key!==GamepadButton.OK && key!==GamepadButton.CANCEL) return;
    event.preventDefault();event.stopPropagation();
    if(direction)commit(moveConsole(p.layout,stateRef.current,direction,lists));else if(key===GamepadButton.OK)activate();else cancel();
  }
  const art=(g: ThemeGame)=>`https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/${g.appid}/header.jpg`;
  const transition=p.motion?'transform 220ms cubic-bezier(.22,.75,.2,1), opacity 180ms ease':'none';
  const cross=p.layout==='crossbar';
  return <Focusable ref={root} preferredFocus noFocusRing onGamepadDirection={button} onOKButton={button} onCancelButton={button} onOKActionDescription="Open" onCancelActionDescription="Back" tabIndex={0} aria-label={`${cross?'Crossbar':'Horizon'} console navigation`} style={{ height:'calc(100vh - 100px)',overflow:'hidden',position:'relative',boxSizing:'border-box',padding:'68px 54px 30px',color:'#f5f7ff',background:p.background||(cross?'radial-gradient(ellipse at 68% 92%,#9f8545 0%,#51432c 38%,#252827 72%,#171d27 100%)':'radial-gradient(ellipse at 30% 10%,#1774bd 0%,#074987 42%,#09295d 78%,#081c42 100%)'),fontFamily:'sans-serif',outline:'none' }}>
    <style>{`.cs-console-wave{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;opacity:.32}.cs-console-wave.moving{animation:cs-wave-drift 18s ease-in-out infinite alternate}.cs-console-surface{position:relative;z-index:1}@keyframes cs-wave-drift{from{transform:translate3d(-1%,1%,0) scale(1.04)}to{transform:translate3d(1%,-1%,0) scale(1.08)}}.cs-console-click{font:inherit;color:inherit;background:none;border:0;cursor:pointer}.cs-console-click:focus-visible{outline:2px solid white}@media(prefers-reduced-motion:reduce){.cs-console-track{transition:none!important}.cs-console-wave{animation:none!important}}`}</style>
    <svg className={`cs-console-wave ${p.motion?'moving':''}`} viewBox="0 0 1280 720" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id={`cs-wave-${p.layout}`} x1="0" y1="0" x2="1" y2="1"><stop stopColor="white" stopOpacity=".02"/><stop offset=".5" stopColor="white" stopOpacity=".26"/><stop offset="1" stopColor="white" stopOpacity=".01"/></linearGradient></defs>
      <path d={cross?'M-100 440 C260 250 390 640 800 420 S1200 250 1400 420 L1400 520 C1050 350 870 600 550 510 S100 340 -100 550Z':'M-100 580 C230 300 400 600 760 310 S1200 180 1400 280 L1400 380 C1020 250 800 570 470 500 S100 400 -100 680Z'} fill={`url(#cs-wave-${p.layout})`}/>
      <path d={cross?'M-100 462 C260 275 390 665 800 445 S1200 275 1400 445':'M-100 580 C230 300 400 600 760 310 S1200 180 1400 280'} fill="none" stroke="white" strokeOpacity=".2" strokeWidth="1"/>
    </svg>
    <div className="cs-console-surface" style={{height:'100%'}}>
    <header style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:cross?44:30,opacity:.8}}><span style={{fontSize:18,fontWeight:600,letterSpacing:1}}>Couch<span style={{color:'#6bdfc4'}}>side</span></span><button tabIndex={-1} className="cs-console-click" onClick={p.exit}>Steam home</button></header>
    <div style={{height:cross?100:88,position:'relative',overflow:'hidden'}}>
      <div className="cs-console-track" style={{display:'flex',gap:0,position:'absolute',left:cross?210:24,transform:cross?`translateX(${-categoryIndex*140}px)`:'none',transition}}>{consoleCategories.map((category,i)=><button tabIndex={-1} className="cs-console-click" key={category} onClick={()=>{commit({...stateRef.current,category,row:'bar'});}} style={{width:140,flexShrink:0,textAlign:'center',opacity:state.category===category&&(cross||state.row==='bar')?1:.48,transform:state.category===category&&(cross||state.row==='bar')?'scale(1.18)':'scale(.82)',transition,padding:8,textShadow:state.category===category?'0 0 18px #fff8':'none'}}><Icon kind={category}/><div style={{marginTop:5,fontSize:15}}>{labels[category]}</div>{state.category===category&&state.row==='bar'&&<div style={{height:2,background:'white',marginTop:5}}/>}</button>)}</div>
    </div>
    {cross ? <div style={{position:'relative',height:'calc(100% - 215px)',minHeight:170,overflow:'hidden',marginTop:12}}>
      <div className="cs-console-track" style={{position:'absolute',left:210,top:50,transform:`translateY(${-index*100}px)`,transition,width:'70%'}}>{items[activeCategory].map((item,i)=><button tabIndex={-1} key={item.id} className="cs-console-click" onClick={()=>{commit({...stateRef.current,row:'items',selected:{...stateRef.current.selected,[activeCategory]:item.id}});open(item.id);}} style={{height:100,display:'flex',alignItems:'center',gap:22,width:'100%',textAlign:'left',opacity:i===index?1:Math.abs(i-index)>2?.22:.42,transform:i===index?'scale(1)':'scale(.88)',transformOrigin:'left center',transition}}>{activeCategory==='games'?<img alt="" src={art(p.games[i])} style={{width:138,height:78,objectFit:'cover',background:'#ffffff15',boxShadow:i===index?'0 5px 22px #0005':'none'}} onError={e=>{e.currentTarget.style.opacity='0'}}/>:<Icon kind={activeCategory}/>}<span style={{fontSize:i===index?29:21,fontWeight:i===index?400:300,textShadow:i===index?'0 0 12px #fff5':'none'}}>{item.name}{i===index&&state.row==='items'&&<small style={{display:'block',fontSize:12,opacity:.7,marginTop:5}}>{activeCategory==='games'?'Installed · Steam':'Open'}</small>}</span></button>)}</div>
      {!items[activeCategory].length&&<p style={{marginLeft:210}}>No installed games. Left/right switches categories.</p>}
    </div> : <><div style={{overflow:'hidden',height:310,marginTop:34}}><div className="cs-console-track" style={{display:'flex',gap:12,transform:`translateX(${48-index*182}px)`,transition,padding:'8px 0'}}>{p.games.map((g,i)=><button tabIndex={-1} key={g.appid} className="cs-console-click" onClick={()=>{commit({...stateRef.current,row:'items',selected:{...stateRef.current.selected,games:String(g.appid)}});open(String(g.appid));}} style={{flexShrink:0,width:170,alignSelf:'flex-start',padding:0,opacity:state.row==='bar'?.45:1,transform:i===index?'scale(1.34)':'scale(1)',transformOrigin:'top left',marginRight:i===index?58:0,transition,boxShadow:i===index&&state.row==='items'?'0 0 0 2px #fff,0 0 0 5px #75c7ff45,0 10px 30px #0004':'none',border:'0'}}><img alt="" src={art(g)} style={{width:170,height:170,objectFit:'cover',display:'block',background:'#ffffff15'}} onError={e=>{e.currentTarget.style.opacity='0'}}/>{i===index&&<div style={{padding:'8px 6px',fontSize:15,fontWeight:300,background:'#ffffff25'}}>Start</div>}</button>)}</div></div><h2 style={{margin:'12px 48px 14px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',fontSize:32,fontWeight:300,textShadow:'0 2px 8px #0004'}}>{state.row==='bar'?labels[state.category]:game?.name||'Your library starts here'}</h2>{state.row==='bar'&&<p style={{marginLeft:48,opacity:.7}}>A · Open {labels[state.category]} · Down · Return to games</p>}</>}
    {game && state.row==='items'&&<div style={{position:cross?'absolute':'relative',right:cross?0:undefined,bottom:cross?48:undefined,marginLeft:cross?undefined:48,maxWidth:cross?'34%':undefined}}>{p.details(game)}</div>}
    <footer style={{position:'absolute',bottom:0,left:0,fontSize:12,opacity:.65}}>{cross?'← → Categories · ↑ ↓ Items':'← → Browse · ↑ Functions · ↓ Games'} · A Open · B Back</footer>
    </div>
  </Focusable>;
}
