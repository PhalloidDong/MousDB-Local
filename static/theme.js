/* 本机主题控制器。
 * 输入：用户的light/dark/system选择及操作系统主题；输出：html的data-theme和原生控件配色。
 * 参考next-themes的属性主题、首屏初始化、localStorage和跨标签同步思路，原生实现无需框架。
 * 此脚本在CSS前同步执行，首屏即应用正确主题；偏好只保存在本机浏览器，不写鼠库。
 */
"use strict";
window.MousDBTheme=(()=>{
  const key="mousdb-theme",choices=new Set(["light","dark","system"]);
  const root=document.documentElement,system=window.matchMedia("(prefers-color-scheme: dark)");
  let choice="system";
  // 浏览器禁用存储时仍可在当前标签切换；读取失败不影响程序和主题按钮。
  try{const saved=localStorage.getItem(key);if(choices.has(saved))choice=saved;}catch(error){/* 使用系统主题兜底。 */}
  function apply(){
    const resolved=choice==="system"?(system.matches?"dark":"light"):choice;
    root.dataset.theme=resolved;root.style.colorScheme=resolved;
    const meta=document.querySelector('meta[name="color-scheme"]');if(meta)meta.content=resolved;
    const control=document.getElementById("theme-choice");if(control)control.value=choice;
  }
  function set(value){
    // 限制可选值，避免损坏的存储内容成为DOM属性或CSS选择器。
    if(!choices.has(value))return;
    choice=value;try{localStorage.setItem(key,choice);}catch(error){/* 当前标签仍使用新选择。 */}
    apply();
  }
  apply();
  document.addEventListener("DOMContentLoaded",()=>{
    const control=document.getElementById("theme-choice");
    if(control){control.value=choice;control.addEventListener("change",()=>set(control.value));}
  },{once:true});
  const followSystem=()=>{if(choice==="system")apply();};
  if(system.addEventListener)system.addEventListener("change",followSystem);
  else system.addListener(followSystem); // 兼容较早的macOS Safari。
  window.addEventListener("storage",event=>{
    // 同源标签页更改或清空偏好时同步；不重新绘制架子，因此保留滚动位置和编辑内容。
    if(event.key!==key&&event.key!==null)return;
    choice=choices.has(event.newValue)?event.newValue:"system";apply();
  });
  return {set,get:()=>choice};
})();
