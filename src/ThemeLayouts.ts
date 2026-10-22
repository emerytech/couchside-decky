// Original grouping rules: only installed library data, no invented genre/recency metadata.
export type ThemeGame = { appid: number; name: string };
export const waveSections = [
  { id: 'all', label: 'All games' },
  { id: 'am', label: 'A–M' },
  { id: 'nz', label: 'N–Z' },
  { id: 'other', label: 'Numbers & more' },
] as const;
export type WaveSection = typeof waveSections[number]['id'];
export function waveGames(games: ThemeGame[], section: WaveSection): ThemeGame[] {
  if (section === 'all') return games;
  return games.filter(game => {
    const letter = game.name.trim().charAt(0).toUpperCase();
    return section === 'am' ? /^[A-M]$/.test(letter) : section === 'nz' ? /^[N-Z]$/.test(letter) : !/^[A-Z]$/.test(letter);
  });
}
