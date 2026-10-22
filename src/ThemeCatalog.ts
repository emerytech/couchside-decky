// Original Couchside layouts; previews captured from the Couchside renderer.
import couchside from './theme-previews/couchside.png';
import orbit from './theme-previews/orbit.png';
import shelf from './theme-previews/shelf.png';
import wave from './theme-previews/wave.png';
import channels from './theme-previews/channels.png';
import crossbar from './theme-previews/crossbar.png';
import horizon from './theme-previews/horizon.png';
export const themeCatalog = [
  { id: 'couchside', name: 'Couchside', description: 'Featured game and a spacious library.', preview: couchside },
  { id: 'orbit', name: 'Orbit', description: 'Large covers in a horizontal ribbon.', preview: orbit },
  { id: 'shelf', name: 'Shelf', description: 'A compact grid for your installed games.', preview: shelf },
  { id: 'wave', name: 'Wave', description: 'Category rail, flowing game list and highlighted artwork.', preview: wave },
  { id: 'channels', name: 'Channels', description: 'Bright rounded channels for your installed games.', preview: channels },
  { id: 'crossbar', name: 'Crossbar', description: 'Golden flowing backdrop, console categories and a vertical game list.', preview: crossbar },
  { id: 'horizon', name: 'Horizon', description: 'Deep blue console ribbon and selected-game stats.', preview: horizon },
] as const;
