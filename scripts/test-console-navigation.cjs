const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript');
const m={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/ConsoleNavigation.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,{module:m,exports:m.exports});
const {initialConsoleNav,moveConsole,consoleItem,backConsole}=m.exports;
const lists={settings:['settings','themes','default'],library:['library'],games:['101','202','303'],downloads:['downloads']};
let s=initialConsoleNav(202);
s=moveConsole('crossbar',s,'down',lists);assert.equal(consoleItem(s,lists),'303');
s=moveConsole('crossbar',s,'left',lists);assert.equal(s.category,'library');assert.equal(consoleItem(s,lists),'library');
s=moveConsole('crossbar',s,'right',lists);assert.equal(s.category,'games');assert.equal(consoleItem(s,lists),'303','per-category selection retained');
s=moveConsole('crossbar',s,'up',lists);assert.equal(consoleItem(s,lists),'202');
s=moveConsole('crossbar',s,'up',lists);s=moveConsole('crossbar',s,'up',lists);assert.equal(s.row,'bar');
s=moveConsole('crossbar',s,'down',lists);assert.equal(s.row,'items');assert.equal(consoleItem(s,lists),'101');
assert.equal(backConsole('crossbar',s).row,'bar');assert.equal(backConsole('crossbar',{...s,row:'bar'}),null);
s=initialConsoleNav(202);s=moveConsole('horizon',s,'right',lists);assert.equal(s.selected.games,'303');s=moveConsole('horizon',s,'up',lists);assert.equal(s.row,'bar');s=moveConsole('horizon',s,'right',lists);assert.equal(s.category,'downloads');s=moveConsole('horizon',s,'down',lists);assert.equal(s.row,'items');assert.equal(s.selected.games,'303');
assert.equal(backConsole('horizon',{...s,row:'bar'}).row,'items');assert.equal(backConsole('horizon',s),null);
const empty={...lists,games:[]};assert.equal(consoleItem(initialConsoleNav(),empty),undefined);assert.equal(moveConsole('crossbar',initialConsoleNav(),'left',empty).category,'library');
for(const layout of ['crossbar','horizon']){let state=initialConsoleNav();for(let n=0;n<200;n++){state=moveConsole(layout,state,['left','right','up','down'][n%4],empty);assert(lists[state.category]);}}
console.log('PASS Crossbar two-axis categories/items, per-category memory, Horizon function/game rows, Back and empty library');
