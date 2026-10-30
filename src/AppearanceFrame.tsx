import {type ReactNode,type CSSProperties} from 'react';
import {parseAppearancePack,type AppearancePack} from './AppearancePack';
export function AppearanceFrame({pack,children,section='home'}:{pack:AppearancePack;children:ReactNode;section?:'home'|'library'|'settings'}){
 const p=parseAppearancePack(pack),c=p.colors,g=p.backgrounds[section];
 const library=p.backgrounds.library;
 const libraryBackground=library?`linear-gradient(${library.angle}deg,${library.stops.join(',')})`:'transparent';
 const background=g?`linear-gradient(${g.angle}deg,${g.stops.join(',')})`:c.background;
 const style={'--ap-bg':background,'--ap-library':libraryBackground,'--ap-surface':c.surface,'--ap-text':c.text,'--ap-muted':c.muted,'--ap-accent':c.accent,'--ap-focus':c.focus} as CSSProperties;
 return <div className="cs-appearance" style={style}><style>{`
 .cs-appearance{height:100%;color:var(--ap-text)}
 .cs-appearance>div{background:var(--ap-bg)!important;color:var(--ap-text)!important}
 .cs-appearance :is(h1,h2,h3,span,button,div){color:var(--ap-text)!important}
 .cs-appearance :is(p,footer,small){color:var(--ap-muted)!important}
 .cs-appearance :is(button,input){background:var(--ap-surface)!important;color:var(--ap-text)!important}
 .cs-appearance :is(button:focus,button.gpfocus,.cs-theme-game[data-selected="true"],button[data-selected="true"]){outline:3px solid var(--ap-focus)!important;outline-offset:2px;border-color:var(--ap-focus)!important;box-shadow:none!important}
 .cs-appearance :is(.cs-theme-library,.cs-console-track){background:var(--ap-library)!important}
 .cs-appearance .cs-theme-header span{color:var(--ap-accent)!important}
 .cs-appearance .cs-wave-band,.cs-appearance .cs-console-wave{opacity:.12!important}
 `}</style>{children}</div>;
}
