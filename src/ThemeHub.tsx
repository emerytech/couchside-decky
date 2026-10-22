import { callable } from '@decky/api';
import { DialogButton, Focusable, TextField, ToggleField, Navigation, NavEntryPositionPreferences } from '@decky/ui';
import { useEffect, useRef, useState } from 'react';
import { themeCatalog } from './ThemeCatalog';

type ThemeId = typeof themeCatalog[number]['id'];
type Accent = 'teal' | 'blue' | 'violet' | 'amber';
type Settings = { installed: boolean; enabled: boolean; theme: ThemeId; accent: Accent; motion: boolean };
const read = callable<[], { settings: Settings }>('get_steam_theme');
const write = callable<[Partial<Settings>], { ok: boolean; error?: string }>('set_steam_theme');
const accents: Record<Accent, string> = { teal: '#26d4be', blue: '#73b5ff', violet: '#b5a0ff', amber: '#ffcf7b' };

/** Original TV theme browser. Draft state never writes until Apply is pressed. */
export function CouchsideThemeHub() {
  const [active, setActive] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const live = useRef(true);
  const saving = useRef(false);
  useEffect(() => {
    live.current = true;
    read().then(value => { if (live.current) { setActive(value.settings); setDraft(value.settings); } })
      .catch(() => { if (live.current) setMessage('Could not read themes. Return to Steam and try again.'); });
    return () => { live.current = false; };
  }, []);
  const visible = themeCatalog.filter(theme => `${theme.name} ${theme.description}`.toLowerCase().includes(query.trim().toLowerCase()));
  const selectedVisible = visible.some(theme => theme.id === draft?.theme);
  const apply = async (enabled: boolean) => {
    if (!draft || saving.current || (enabled && (!draft.installed || !selectedVisible))) return;
    saving.current = true; setBusy(true); setMessage('Saving…');
    try {
      const result = await write({ enabled, theme: draft.theme, accent: draft.accent, motion: draft.motion });
      if (!live.current) return;
      if (!result.ok) { setMessage(result.error || 'Could not save. Your previous theme is unchanged.'); return; }
      const value = { ...draft, enabled };
      setActive(value); setDraft(value);
      setMessage(enabled ? 'Theme applied.' : 'Steam default restored.');
      Navigation.Navigate(enabled ? '/couchside/themes' : '/library/home');
    } catch { if (live.current) setMessage('Service unavailable. Your previous theme is unchanged.'); }
    finally { saving.current = false; if (live.current) setBusy(false); }
  };
  const color = accents[draft?.accent || 'teal'];
  const pending = !!draft && !!active && (!active.enabled || draft.theme !== active.theme || draft.accent !== active.accent || draft.motion !== active.motion);
  return <Focusable preferredFocus navEntryPreferPosition={NavEntryPositionPreferences.PREFERRED_CHILD} flow-children="column" onCancel={() => Navigation.Navigate('/library/home')} style={{ height: 'calc(100vh - 80px)', display: 'flex', flexDirection: 'column', background: '#0c1321', color: '#f0f4fa', padding: '56px 36px 20px', boxSizing: 'border-box' }}>
    <style>{`.cs-hub-control:focus,.cs-hub-control.gpfocus{outline:3px solid #f0f4fa!important;outline-offset:3px;box-shadow:0 0 0 6px #0c1321!important}.cs-hub-control{flex-grow:0!important}`}</style>
    <header style={{ display: 'flex', justifyContent: 'space-between', gap: 24 }}><div style={{ flex: 1, minWidth: 0 }}><h1 style={{ margin: '0 0 8px' }}>Couchside Themes <span style={{ fontSize: 13, border: '1px solid #8edcfa', color: '#8edcfa', borderRadius: 6, padding: '3px 7px', verticalAlign: 'middle' }}>BETA</span></h1><p style={{ margin: '0 0 16px', color: '#b0bfd2' }}>Choose your home. Your Steam games stay where they are.</p></div><DialogButton className="cs-hub-control" style={{ width: 140, flexGrow: 0, flexShrink: 0, height: 48, alignSelf: 'flex-start' }} onClick={() => Navigation.Navigate('/library/home')}>Back</DialogButton></header>
    <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 8 }}>
      <TextField label="Search themes" value={query} onChange={event => setQuery(event.target.value)} />
      {!draft && <p>{message || 'Loading themes…'}</p>}
      {draft && <>
        {!draft.installed && <p>The optional themes add-on is not enabled on this box. Enable it from Couchside’s setup first.</p>}
        <DialogButton className="cs-hub-control" disabled={busy} onClick={() => void apply(false)}>Steam default{!active?.enabled ? ' · Active' : ''}</DialogButton>
        <Focusable flow-children="row" style={{ display: 'flex', flexWrap: 'wrap', gap: 20, padding: '20px 4px' }}>
          {visible.map(theme => <DialogButton className="cs-hub-control" key={theme.id} preferredFocus={theme.id === draft.theme} disabled={busy} onClick={() => setDraft({ ...draft, theme: theme.id })} style={{ width: 300, padding: 12, color: '#f0f4fa', background: '#172235', border: `3px solid ${draft.theme === theme.id ? color : '#33435b'}`, borderRadius: 14, textAlign: 'left' }}>
            <img alt={`${theme.name} sample library preview`} src={theme.preview} style={{ width: '100%', aspectRatio: '16 / 9', objectFit: 'contain' }} />
            <h2 style={{ margin: '10px 0', color: '#f0f4fa' }}>{theme.name}</h2><p style={{ color: '#c2ccdb' }}>{theme.description}</p>
            <div style={{ minHeight: 24, color }}>{active?.enabled && active.theme === theme.id ? 'Active' : ''}{draft.theme === theme.id && pending ? (active?.enabled && active.theme === theme.id ? ' · Changes not applied' : 'Selected, not applied') : ''}</div>
          </DialogButton>)}
        </Focusable>
        {!visible.length && <p>No themes match your search. Clear it to see your selection.</p>}
        <h3>Make it yours</h3><Focusable flow-children="row" style={{ display: 'flex', gap: 16, marginBottom: 16 }}>{(Object.keys(accents) as Accent[]).map(accent => <DialogButton className="cs-hub-control" disabled={busy} key={accent} onClick={() => setDraft({ ...draft, accent })} style={{ border: `3px solid ${accents[accent]}`, color: '#f0f4fa', background: '#172235' }}>{accent}{draft.accent === accent ? ' ✓' : ''}</DialogButton>)}</Focusable>
        <ToggleField label="Animations" description="Respect reduced motion; turn off theme transitions." checked={draft.motion} disabled={busy} onChange={motion => setDraft({ ...draft, motion })} />
        <p style={{ color: '#b0bfd2' }}>Original Couchside layouts. Previews use a sample library. Steam default is always available.</p>
      </>}
    </div>
    <footer style={{ paddingTop: 12, borderTop: '1px solid #33435b', flexShrink: 0 }}><div role="status" style={{ minHeight: 24 }}>{message || (pending ? 'Preview selection — press Apply to change the TV.' : '')}</div><DialogButton className="cs-hub-control" disabled={busy || !draft?.installed || !selectedVisible} onClick={() => void apply(true)} style={{ width: '100%', background: color, color: '#0c1321', padding: 14 }}>{busy ? 'Saving…' : !selectedVisible ? 'Select a visible theme' : 'Apply selected theme'}</DialogButton></footer>
  </Focusable>;
}
