/* 主题行为验收：只使用内存中的浏览器替身，不读取SQLite或用户偏好。
 * 验证首屏主题、手动覆盖系统、跨标签同步及存储不可用时的降级。
 */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/theme.js'),'utf8');
function browser({saved=null,dark=false,blocked=false,legacy=false}={}){
  const data=new Map(saved==null?[]:[['mousdb-theme',saved]]),events={},domEvents={},listeners={};
  const control={value:'',addEventListener:(name,fn)=>listeners[name]=fn};
  const root={dataset:{},style:{}},meta={content:''};
  const media={matches:dark,addListener:fn=>listeners.system=fn};
  if(!legacy)media.addEventListener=(name,fn)=>listeners.system=fn;
  const window={matchMedia:()=>media,addEventListener:(name,fn)=>events[name]=fn};
  const document={documentElement:root,querySelector:()=>meta,getElementById:()=>control,addEventListener:(name,fn)=>domEvents[name]=fn};
  const localStorage={getItem:k=>{if(blocked)throw Error('disabled');return data.get(k)||null;},setItem:(k,v)=>{if(blocked)throw Error('disabled');data.set(k,v);}};
  vm.runInNewContext(source,{window,document,localStorage});
  return {root,meta,data,media,control,api:window.MousDBTheme,mount:()=>domEvents.DOMContentLoaded(),change:v=>{control.value=v;listeners.change();},system:v=>{media.matches=v;listeners.system();},storage:(key,newValue)=>events.storage({key,newValue})};
}
// CSS读取之前已解析系统主题；用户明确选择不被系统后续变更覆盖。
const first=browser({dark:true});assert.equal(first.root.dataset.theme,'dark');assert.equal(first.root.style.colorScheme,'dark');
first.mount();assert.equal(first.control.value,'system');first.change('light');first.system(true);assert.equal(first.root.dataset.theme,'light');assert.equal(first.data.get('mousdb-theme'),'light');
// 新标签/刷新复用已存选择；选择system后恢复监听，跨标签变更和清空偏好均同步。
const reloaded=browser({saved:first.data.get('mousdb-theme'),dark:true});assert.equal(reloaded.root.dataset.theme,'light');reloaded.mount();
reloaded.change('system');assert.equal(reloaded.root.dataset.theme,'dark');reloaded.system(false);assert.equal(reloaded.root.dataset.theme,'light');
reloaded.storage('other-key','dark');assert.equal(reloaded.root.dataset.theme,'light');reloaded.storage('mousdb-theme','dark');assert.equal(reloaded.control.value,'dark');
reloaded.storage(null,null);assert.equal(reloaded.control.value,'system');assert.equal(reloaded.root.dataset.theme,'light');
// 无效存储内容不写入DOM；浏览器阻止localStorage时仍可切换；旧Safari监听兼容。
const invalid=browser({saved:'broken',dark:true});assert.equal(invalid.root.dataset.theme,'dark');invalid.api.set('invalid');assert.equal(invalid.api.get(),'system');
const blocked=browser({blocked:true});blocked.mount();blocked.change('dark');assert.equal(blocked.root.dataset.theme,'dark');assert.equal(blocked.meta.content,'dark');
const legacy=browser({legacy:true});legacy.system(true);assert.equal(legacy.root.dataset.theme,'dark');
console.log('Theme behavior checks passed');
