const fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript'),assert=require('node:assert/strict');
const m={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/ThemeLayouts.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,{module:m,exports:m.exports});
const games=[{appid:1,name:'  Astro'},{appid:2,name:'Moon'},{appid:3,name:'Nova'},{appid:4,name:'Zebra'},{appid:5,name:'42 racers'},{appid:6,name:'Élan'}];
const ids=section=>Array.from(m.exports.waveGames(games,section),g=>g.appid);
assert.deepEqual(ids('am'),[1,2]);assert.deepEqual(ids('nz'),[3,4]);assert.deepEqual(ids('other'),[5,6]);assert.equal(m.exports.waveGames(games,'all'),games);
assert.equal(m.exports.waveGames([], 'am').length,0);assert.equal(games[0].name,'  Astro','Filtering never mutates game data');
assert.equal(new Set([...ids('am'),...ids('nz'),...ids('other')]).size,games.length,'Every game belongs to a visible group');
console.log('PASS: Wave letter boundaries, numbers/unicode, empty groups and immutable library');
