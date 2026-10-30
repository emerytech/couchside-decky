const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),ts=require('typescript');
function load(file,imports={}){const m={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,{module:m,exports:m.exports,require:n=>imports[n]});return m.exports;}
const pack=load('src/AppearancePack.ts');
const jsx=(type,props)=>({type,props});const frame=load('src/AppearanceFrame.tsx',{'./AppearancePack':pack,'react/jsx-runtime':{jsx,jsxs:jsx}});
const p=JSON.parse(JSON.stringify(pack.APPEARANCE_PACKS[0]));p.backgrounds={home:{angle:90,stops:['#123456','#abcdef']},library:{angle:180,stops:['#223344','#556677']},settings:{angle:135,stops:['#001122','#334455']}};
const tree=frame.AppearanceFrame({pack:p,children:'child'});
assert.equal(tree.props.style['--ap-bg'],'linear-gradient(90deg,#123456,#abcdef)');
assert.equal(tree.props.style['--ap-library'],'linear-gradient(180deg,#223344,#556677)');
assert.equal(frame.AppearanceFrame({pack:p,children:'child',section:'settings'}).props.style['--ap-bg'],'linear-gradient(135deg,#001122,#334455)');
assert.throws(()=>frame.AppearanceFrame({pack:{...p,colors:{...p.colors,accent:'url(x)'}},children:'child'}));
console.log('PASS: appearance values validated before rendering; gradients map to fixed sections');
