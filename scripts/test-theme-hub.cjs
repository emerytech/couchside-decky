const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),ts=require('typescript');
const code=ts.transpileModule(fs.readFileSync('src/ThemeHub.tsx','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText;
const catalog=[{id:'couchside',name:'Couchside',description:'Dashboard',preview:'own-preview'},{id:'orbit',name:'Orbit',description:'Ribbon',preview:'own-preview'}];
const settle=()=>new Promise(r=>setImmediate(r));
async function run(query,ok=true,withPacks=false){
 let n=0;const calls=[],nav=[];const current={installed:true,enabled:false,theme:'orbit',accent:'blue',motion:false};
 if(withPacks){current.appearance=null;current.appearance_packs=[{name:'Forest'}];}
 const states=[current,current,query,false,''];const jsx=(type,props)=>({type,props});const m={exports:{}};
 vm.runInNewContext(code,{module:m,exports:m.exports,require:name=>name==='react'?{useRef:x=>({current:x}),useEffect:()=>{},useState:()=>[states[n++],()=>{}]}:name==='react/jsx-runtime'?{jsx,jsxs:jsx}:name==='./AppearanceFrame'?{AppearanceFrame:'appearance-frame'}:name==='./ThemeCatalog'?{themeCatalog:catalog}:name==='@decky/api'?{callable:key=>async patch=>{calls.push([key,patch]);return{ok,error:'Refused'}}}:{NavEntryPositionPreferences:{PREFERRED_CHILD:4},Focusable:'div',TextField:'input',ToggleField:'toggle',DialogButton:'button',Navigation:{Navigate:p=>nav.push(p)}}});
 const tree=m.exports.CouchsideThemeHub();const flat=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(flat):[node,...flat(node.props?.children)];
 if(withPacks){assert.equal(tree.type,'appearance-frame','default preview retains frame when packs exist');const group=flat(tree).find(x=>x.props?.['flow-children']&&Array.isArray(x.props.children)&&x.props.children.some(c=>c?.type==='button'&&Array.isArray(c.props.children)&&c.props.children[0]==='Layout default'));assert(group,'appearance list exists');assert.equal(group.props['flow-children'],'column','D-pad Down follows the visible pack list');assert.equal(group.props.style.flexDirection,'column');}
 const apply=flat(tree).find(x=>x.type==='button'&&['Apply selected theme','Select a visible theme'].includes(x.props.children));
 assert(apply);apply.props.onClick();apply.props.onClick();await settle();await settle();return {calls,nav,apply};
}
(async()=>{
 let x=await run('no matches');assert(x.apply.props.disabled);assert.equal(x.calls.length,0);assert.equal(x.nav.length,0);
 x=await run('couchside');assert(x.apply.props.disabled);assert.equal(x.calls.length,0);
 x=await run(' ORBIT ');assert.equal(x.apply.props.disabled,false);assert.equal(x.calls.length,1,'double Apply must not duplicate writes');assert.equal(x.calls[0][0],'set_steam_theme');assert.equal(x.calls[0][1].theme,'orbit');assert.deepEqual(x.nav,['/couchside/themes']);
 x=await run('',false);assert.equal(x.nav.length,0,'failed save must not navigate or pretend success');
 await run('',true,true);
 console.log('PASS: hub search visibility, draft payload, duplicate Apply and refused save');
})().catch(e=>{console.error(e);process.exitCode=1});
