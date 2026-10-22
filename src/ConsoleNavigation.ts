// Explicit console navigation. Steam's spatial focus never chooses a category.
export type ConsoleLayout = 'crossbar' | 'horizon';
export type ConsoleDirection = 'left' | 'right' | 'up' | 'down';
export const consoleCategories = ['settings', 'library', 'games', 'downloads'] as const;
export type ConsoleCategory = typeof consoleCategories[number];
export type ConsoleNav = { category: ConsoleCategory; row: 'bar' | 'items'; selected: Partial<Record<ConsoleCategory, string>> };
export type ConsoleLists = Record<ConsoleCategory, string[]>;
export function consoleItem(state: ConsoleNav, lists: ConsoleLists): string | undefined {
  const list = lists[state.category];
  return list.includes(state.selected[state.category] || '') ? state.selected[state.category] : list[0];
}
export function initialConsoleNav(game?: number): ConsoleNav {
  return { category: 'games', row: 'items', selected: game ? { games: String(game) } : {} };
}
export function moveConsole(layout: ConsoleLayout, state: ConsoleNav, direction: ConsoleDirection, lists: ConsoleLists): ConsoleNav {
  const horizontal = direction === 'left' || direction === 'right';
  if (horizontal && (layout === 'crossbar' || state.row === 'bar')) {
    const i = consoleCategories.indexOf(state.category);
    return { ...state, category: consoleCategories[Math.max(0, Math.min(consoleCategories.length - 1, i + (direction === 'left' ? -1 : 1)))] };
  }
  if (layout === 'horizon') {
    if (direction === 'up') return { ...state, row: 'bar' };
    if (direction === 'down') return { ...state, row: 'items' };
    const list = lists.games, current = state.selected.games;
    const index = Math.max(0, list.indexOf(current || ''));
    const next = Math.max(0, Math.min(list.length - 1, index + (direction === 'left' ? -1 : 1)));
    return { ...state, selected: { ...state.selected, games: list[next] } };
  }
  if (state.row === 'bar') return direction === 'down' ? { ...state, row: 'items' } : state;
  const list = lists[state.category], index = Math.max(0, list.indexOf(consoleItem(state, lists) || ''));
  if (direction === 'up' && index === 0) return { ...state, row: 'bar' };
  const next = Math.max(0, Math.min(list.length - 1, index + (direction === 'up' ? -1 : 1)));
  return { ...state, selected: { ...state.selected, [state.category]: list[next] } };
}
export function backConsole(layout: ConsoleLayout, state: ConsoleNav): ConsoleNav | null {
  if (layout === 'horizon') return state.row === 'bar' ? { ...state, row: 'items' } : null;
  return state.row === 'items' ? { ...state, row: 'bar' } : null;
}
