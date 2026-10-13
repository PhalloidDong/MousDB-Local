/* 真实架位首页。
 * 输入：服务器的架子快照（固定72格、笼卡和revision）。
 * 输出：可拖动笼卡、架旁临时区、定位/建笼/状态菜单。
 * SortableJS只处理手势，最终位置以服务器返回结果为准。
 * 空格是物理笼位；卡片是实际cage；不把动物转入另一张卡。
 */
"use strict";
window.RackBoard=(()=>{
  const root=document.getElementById("rack-home");
  let model=null,racks=[],selected=null,sortables=[],busy=false,lastDrag=0;
  let search="",zoom=localStorage.getItem("mousdb-rack-zoom")||"100";
  const letters="ABCDEFGHI",sexLabel={M:"♂",F:"♀",U:"?"};
  const coordinate=(row,col)=>row==null?"临时区":`${letters[row-1]}${col}`;
  const address=c=>coordinate(c.row_index,c.column_index);
  const unique=values=>[...new Set(values.filter(Boolean))];

  function destroyDrag(){
    // 替换DOM前销毁旧实例，避免反复刷新后同一拖拽发送多个保存请求。
    sortables.forEach(s=>s.destroy());sortables=[];
  }

  async function load(rackId=null){
    /* 架子选择在本机保存；每次读最新快照，切换架子不携带旧revision。 */
    racks=(await api("/api/rack-layouts")).racks;
    const preferred=Number(rackId||selected||localStorage.getItem("mousdb-rack-id"));
    selected=racks.some(r=>r.id===preferred)?preferred:racks[0]?.id;
    if(!selected){
      destroyDrag();model=null;
      root.innerHTML='<article class="rack-onboarding"><span class="rack-eyebrow">YOUR ANIMAL ROOM</span><h2>建立第一座真实架子</h2><p>每座架子横向8列、纵向9行，共72个笼位。</p><button class="primary" data-rack-new>＋ 新建架子</button></article>';
      return;
    }
    model=await api(`/api/rack-layouts/${selected}`);
    localStorage.setItem("mousdb-rack-id",String(selected));render();
  }

  function card(c){
    /* 基因型有共同值时集中显示，差异值逐鼠显示；所有小鼠行保留，不截断数量。 */
    const strains=unique(c.mice.map(m=>m.strain_name));
    const lines=unique(c.mice.map(m=>m.line_name));
    const genotypes=unique(c.mice.map(m=>m.genotype_name));
    const text=[c.code,c.purpose,...c.mice.flatMap(m=>[m.code,m.ear_tag,m.sex,m.strain_name,m.line_name,m.genotype_name])].join(" ").toLowerCase();
    const mouseRows=c.mice.map(m=>`<div class="rack-mouse-row">
      <button type="button" class="rack-ear" data-rack-mouse="${m.id}" title="打开${esc(m.code)}档案">${esc(m.ear_tag||"未设耳号")}</button>
      <span class="rack-sex ${esc(m.sex)}" title="${m.sex==='M'?'雄':m.sex==='F'?'雌':'性别未知'}">${sexLabel[m.sex]||"?"}</span>
      <span class="rack-age" title="${m.age_is_range?'候选出生日期对应的周龄范围':'周龄'}">${esc(m.age_label)}</span>
      ${genotypes.length>1||lines.length>1?`<small class="rack-individual-genotype">${esc([m.line_name,m.genotype_name].filter(Boolean).join(" · ")||"基因型待确认")}</small>`:""}
    </div>`).join("");
    return `<article class="rack-card state-${esc(c.state)}" data-cage-id="${c.id}" data-searchtext="${esc(text)}" aria-label="${esc(c.code)}，${esc(c.state_label)}，${c.occupied}只小鼠">
      <div class="rack-card-handle" title="拖动整笼，调整位置"><span class="rack-grip" aria-hidden="true">⠿</span><button type="button" class="rack-cage-name" data-rack-rename="${c.id}" title="双击重命名；也可按Enter或F2" aria-label="重命名${esc(c.code)}">${esc(c.code)}</button><span class="rack-count">${c.occupied}/${c.capacity}</span></div>
      <div class="rack-card-tools"><button type="button" class="rack-state-chip" data-rack-state="${c.id}" title="设置笼状态">${esc(c.state_label)}<span>⌄</span></button><span class="rack-coordinate">${address(c)}</span><button type="button" class="rack-card-menu" data-rack-move="${c.id}" aria-label="移动${esc(c.code)}到指定位置" title="移动到指定位置">↗</button></div>
      <div class="rack-card-body">
        <div class="rack-genotype"><b>品系</b> ${esc(strains.join(" / ")||"待录入")}${lines.length?`<br><b>Line</b> ${esc(lines.join(" / "))}`:""}<br><b>基因型</b> ${esc(genotypes.join(" / ")||"待确认")}</div>
        ${c.mice.length?`<div class="rack-mouse-head"><span>耳号</span><span>性别</span><span>周龄</span></div><div class="rack-mouse-list">${mouseRows}</div>`:'<p class="rack-card-empty">这笼暂无在养小鼠</p>'}
      </div>
      <div class="rack-card-bottom"><button type="button" data-rack-open="${c.id}">笼卡详情</button><button type="button" data-rack-add-mouse="${c.id}" title="在此笼录入新鼠">＋ 小鼠</button></div>
    </article>`;
  }

  function render(){
    destroyDrag();if(!model)return;
    const placed=new Map(model.cards.filter(c=>c.row_index!=null).map(c=>[`${c.row_index}:${c.column_index}`,c]));
    let cells="";
    for(let row=1;row<=model.rack.rows;row++)for(let col=1;col<=model.rack.columns;col++){
      const c=placed.get(`${row}:${col}`),label=coordinate(row,col);
      cells+=`<div class="rack-dropzone rack-slot ${c?'occupied':'unoccupied'}" data-row="${row}" data-column="${col}" role="gridcell" aria-rowindex="${row}" aria-colindex="${col}" aria-label="${label}${c?' '+esc(c.code):' 空笼位'}">
        <span class="rack-slot-label">${label}</span>${c?card(c):`<button type="button" class="rack-empty-add" data-add-row="${row}" data-add-col="${col}" aria-label="在${label}添加笼"><span>＋</span><small>空笼位</small></button>`}
      </div>`;
    }
    const parked=model.cards.filter(c=>c.row_index==null);
    root.innerHTML=`<div class="rack-location-bar">
      <div><span class="rack-eyebrow">ANIMAL ROOM / LIVE RACK</span><div class="rack-current-location"><span class="rack-room-dot"></span>${esc(model.rack.room_name)}<span class="rack-slash">/</span><strong>${esc(model.rack.name)}</strong></div>${model.rack.note?`<p class="rack-location-note">${esc(model.rack.note)}</p>`:""}</div>
      <div class="rack-location-actions"><label>切换架子<select id="rack-select" aria-label="切换房间和架子">${racks.map(r=>`<option value="${r.id}" ${r.id===selected?'selected':''}>${esc(r.room_name)} / ${esc(r.name)}</option>`).join("")}</select></label><button data-rack-edit>编辑架子</button><button data-rack-new>＋ 架子</button></div>
    </div>
    <div class="rack-summary-strip"><div><b>8 × 9</b><span>横8列 · 竖9行</span></div><div><b>${model.placed}<small>/72</small></b><span>已定位</span></div><div><b>${model.parked}</b><span>待定位 / 临时停放</span></div><div><b>${model.mouse_count}</b><span>当前在笼小鼠</span></div><div class="rack-save-state" role="status">● 已保存</div></div>
    <div class="rack-control-band"><div class="rack-control-left"><div class="rack-search-wrap"><span aria-hidden="true">⌕</span><input id="rack-search" type="search" placeholder="查找耳号、基因型、笼号" value="${esc(search)}" aria-label="查找笼卡"></div>
    <div class="rack-legend" aria-label="笼状态图例">${Object.entries(model.states).map(([k,v])=>`<span><i class="legend-dot state-${esc(k)}"></i>${esc(v)}</span>`).join("")}</div></div>
    ${window.ExperimentPlans.markup()}<div class="rack-view-controls"><label class="rack-zoom-label">显示<select id="rack-zoom"><option value="75" ${zoom==='75'?'selected':''}>75%</option><option value="100" ${zoom==='100'?'selected':''}>100%</option><option value="125" ${zoom==='125'?'selected':''}>125%</option><option value="fit" ${zoom==='fit'?'selected':''}>适应宽度</option></select></label><button id="rack-undo" ${model.undo_id&&!busy?'':'disabled'}>↶ 撤销移位</button></div></div>
    <div class="rack-layout"><div class="rack-board-panel"><div class="rack-board-toolbar"><strong>真实架位</strong><span>上方为A行，左侧为第1列</span></div><div class="rack-board-scroll"><div class="rack-board-grid" role="grid" aria-label="横8列竖9行真实架子" aria-rowcount="${model.rack.rows}" aria-colcount="${model.rack.columns}">${cells}</div></div><p class="rack-board-help">拖到空格定位 · 拖到笼卡交换 · 拖到右侧暂存</p></div>
      <aside class="rack-parking-panel"><div class="rack-parking-title"><span class="rack-eyebrow">PARKING AREA</span><h2>待定位 / 临时停放 <small>${parked.length}</small></h2><p>架子放满时，可先把一笼拖到这里。刷新后仍会保留。</p></div><div class="rack-dropzone rack-parking" data-parking="true" aria-label="架旁临时停放区">${parked.map(card).join("")}<div class="rack-parking-placeholder">拖动笼卡到这里<br><small>尚无真实坐标的笼也会列在这里</small></div></div><button class="rack-new-parked" data-add-parking>＋ 新笼放到临时区</button></aside>
    </div>`;
    applyZoom();applySearch();bindDrag();window.ExperimentPlans.mount();
  }

  function applyZoom(){
    /* 输入：容器内宽、八列最小宽度和用户缩放档位；输出：铺满可用区域的等宽八列。
     * 容器clientWidth已扣掉滚动条，另扣两侧padding；不能把第9列删除后的旧宽度继续当成架宽。
     * 百分比缩放控制文字/卡高，列宽再按反向缩放补偿；窄窗口保留最小卡宽并水平滚动。
     * 这里只调整显示，不修改72个真实位置、小鼠归属或服务端坐标。
     */
    const grid=root.querySelector(".rack-board-grid"),viewport=root.querySelector(".rack-board-scroll");
    if(!grid||!viewport)return;
    const viewportStyle=getComputedStyle(viewport),gridStyle=getComputedStyle(grid);
    const available=viewport.clientWidth-parseFloat(viewportStyle.paddingLeft)-parseFloat(viewportStyle.paddingRight);
    if(available<=0)return; // 切换到其他页面时隐藏架子，不用零宽度覆盖已保存的显示。
    const columns=model.rack.columns;
    const minimumColumn=parseFloat(gridStyle.getPropertyValue("--rack-column-min"));
    const gap=parseFloat(gridStyle.columnGap);
    const minimumWidth=columns*minimumColumn+(columns-1)*gap;
    const scale=Math.max(.3,zoom==="fit"?Math.min(1,available/minimumWidth):Number(zoom)/100);
    grid.style.width=String(Math.max(minimumWidth,available/scale))+"px";
    grid.style.zoom=String(scale);
  }

  function applySearch(){
    // 只减淡不匹配卡片，绝不把格子从网格删除，否则会改变真实行列含义。
    root.querySelectorAll("[data-searchtext]").forEach(el=>el.classList.toggle("rack-dimmed",!!search&&!el.dataset.searchtext.includes(search.toLowerCase())));
  }

  function bindDrag(){
    if(typeof Sortable==="undefined"){notify("拖动组件未载入，可使用每张笼卡的移动菜单","warning");return;}
    root.querySelectorAll(".rack-dropzone").forEach(zone=>{
      sortables.push(new Sortable(zone,{
        group:"mousdb-physical-rack",sort:false,draggable:".rack-card",animation:120,
        handle:".rack-card",filter:"button,input,select,a,.rack-mouse-list",preventOnFilter:false,
        forceFallback:true,fallbackOnBody:true,fallbackTolerance:5,
        ghostClass:"rack-ghost",chosenClass:"rack-chosen",dragClass:"rack-dragging",
        emptyInsertThreshold:18,scroll:true,scrollSensitivity:60,scrollSpeed:12,
        delayOnTouchOnly:true,delay:160,touchStartThreshold:5,
        onStart:()=>{lastDrag=Date.now();root.classList.add("rack-is-dragging");},
        onMove:e=>{root.querySelectorAll(".rack-target").forEach(x=>x.classList.remove("rack-target"));e.to.classList.add("rack-target");return !busy;},
        onEnd:async e=>{
          lastDrag=Date.now();root.classList.remove("rack-is-dragging");
          root.querySelectorAll(".rack-target").forEach(x=>x.classList.remove("rack-target"));
          // 物理位置由释放点决定；列表的DOM排列只是拖动预览。
          const point=e.originalEvent?.changedTouches?.[0]||e.originalEvent;
          const hit=point&&Number.isFinite(point.clientX)?document.elementFromPoint(point.clientX,point.clientY)?.closest(".rack-dropzone"):null;
          const target=hit||e.to;
          if(!target||!root.contains(target)){render();return;}
          const source=model.cards.find(c=>c.id===Number(e.item.dataset.cageId));
          const row=target.dataset.parking?null:Number(target.dataset.row);
          const col=target.dataset.parking?null:Number(target.dataset.column);
          if(source&&source.row_index===row&&source.column_index===col){render();return;}
          await move(Number(e.item.dataset.cageId),row,col);
        }
      }));
    });
  }

  async function save(path,payload,success){
    /* 保存期间禁用手势。失败后回读服务器，防止前端预览误当成真实移位。 */
    if(busy)return;
    busy=true;sortables.forEach(s=>s.option("disabled",true));
    const saved=root.querySelector(".rack-save-state");if(saved)saved.textContent="● 正在保存…";
    try{
      model=await api(path,{method:"POST",body:{...payload,expected_revision:model.rack.revision}});
      busy=false;render();notify(success);
    }catch(error){
      busy=false;await load(selected);notify(error.message,"error");throw error;
    }
  }

  async function move(cageId,row,col){
    try{await save(`/api/rack-layouts/${selected}/move`,{cage_id:cageId,row,column:col},row==null?"已移到架旁临时区":"已保存笼位 "+coordinate(row,col));}
    catch(_error){/* save已回读并提示；拖动事件不会产生未处理Promise错误。 */}
  }

  async function renameCage(cageId){
    /* 只编辑笼号：使用稳定cage_id保存，鼠所属笼及实际坐标不会改变。 */
    const c=await api(`/api/cages/${cageId}`);
    openEditor('重命名笼',`${field('笼号','code','text',c.code,'required')}<p class="muted">修改显示的笼编号，原小鼠和笼位保留。</p>`,async form=>{
      await api(`/api/cages/${cageId}`,{method:'PUT',body:{code:formData(form).code}});
    });
    const input=document.querySelector('#editor [name=code]');input.focus();input.select();
  }

  root.addEventListener('dblclick',event=>{
    const name=event.target.closest('[data-rack-rename]');if(!name||busy)return;
    event.preventDefault();renameCage(Number(name.dataset.rackRename)).catch(error=>notify(error.message,'error'));
  });
  root.addEventListener('keydown',event=>{
    const name=event.target.closest('[data-rack-rename]');
    if(!name||busy||!['Enter','F2'].includes(event.key))return;
    event.preventDefault();renameCage(Number(name.dataset.rackRename)).catch(error=>notify(error.message,'error'));
  });

  function moveMenu(cageId){
    const c=model.cards.find(x=>x.id===cageId);if(!c)return;
    openEditor(`移动 ${c.code}`,`<p>当前位置：${address(c)}。目标已有笼卡时，两笼交换位置。</p><div class="form-grid"><label class="field"><span>行（从上到下）</span><select name="row">${[...letters].map((x,i)=>`<option value="${i+1}" ${c.row_index===i+1?'selected':''}>${x}</option>`).join("")}</select></label><label class="field"><span>列（从左到右）</span><select name="column">${Array.from({length:model.rack.columns},(_,i)=>`<option value="${i+1}" ${c.column_index===i+1?'selected':''}>${i+1}</option>`).join("")}</select></label><label class="full"><input type="checkbox" name="parking" ${c.row_index==null?'checked':''}> 移到架旁临时区</label></div>`,async form=>{
      const d=formData(form),parking=!!form.querySelector("[name=parking]").checked;
      await save(`/api/rack-layouts/${selected}/move`,{cage_id:cageId,row:parking?null:Number(d.row),column:parking?null:Number(d.column)},"笼位已保存");
    });
  }

  function stateMenu(cageId){
    const c=model.cards.find(x=>x.id===cageId);if(!c)return;
    openEditor(`设置 ${c.code} 的状态`,`<p>自动状态根据笼内在养小鼠和繁育记录显示。也可选择本笼当前用途。</p><label class="field"><span>笼状态</span><select name="card_state"><option value="">自动判断</option>${Object.entries(model.states).map(([k,v])=>`<option value="${k}" ${c.card_state===k?'selected':''}>${v}</option>`).join("")}</select></label>`,async form=>{
      try{model=await api(`/api/rack-layouts/${selected}/cards/${cageId}`,{method:"PUT",body:{card_state:formData(form).card_state,expected_revision:model.rack.revision}});render();}
      catch(error){await load(selected);throw error;}
    });
  }

  async function addCage(row,col){
    /* 点击的是空的物理槽位，提交会原子创建cage、全部随附小鼠及该位置。
     * 鼠号来自原系统自动编号；基因型使用原有可输入/富文本字典控件。
     */
    const all=await api("/api/cages");let next=Math.max(0,...all.map(c=>Number((c.code.match(/\d+/)||[0])[0])))+1;
    while(all.some(c=>c.code===`笼${next}`))next++;
    openEditor(`新建笼 · ${coordinate(row,col)}`,`<p class="muted">${esc(model.rack.room_name)} / ${esc(model.rack.name)} / ${coordinate(row,col)}</p><div class="form-grid">${field("笼号","code","text",`笼${next}`,"required")}${field("容量","capacity","number",5,"min=1 required")}${field("用途","purpose")}${field("备注","note")}</div><h3>同时录入小鼠（可选）</h3><div id="rack-new-mice"></div><button type="button" id="rack-add-mouse-row">＋ 添加一只小鼠</button>`,async form=>{
      const d=formData(form);d.mice=[...form.querySelectorAll(".rack-new-mouse-row")].map(el=>Object.fromEntries([...el.querySelectorAll("[name]")].map(input=>[input.name,input.value])));
      try{model=await api(`/api/rack-layouts/${selected}/cages`,{method:"POST",body:{row,column:col,cage:d,expected_revision:model.rack.revision}});render();}
      catch(error){await load(selected);throw error;}
    });
    document.getElementById("rack-add-mouse-row").onclick=()=>{
      const el=document.createElement("div");el.className="rack-new-mouse-row form-grid";
      el.innerHTML=`${field("耳号","ear_tag")}${selectField("性别","sex",'<option value="M">雄</option><option value="F">雌</option><option value="U">待确认</option>')}${field("出生日期","dob","date")}${richPicker("strains","strain")}${richPicker("lines","line")}${richPicker("genotypes","genotype")}<button type="button" class="rack-remove-mouse">移除此行</button>`;
      document.getElementById("rack-new-mice").append(el);setupRichPickers(el);el.querySelector(".rack-remove-mouse").onclick=()=>el.remove();
    };
  }

  function rackEditor(edit=false){
    const r=edit?model.rack:{};
    openEditor(edit?"编辑真实架子":"新建真实架子",`<p class="muted">固定横8列×竖9行，共72个真实笼位。新增架子后可切换查看。</p><div class="form-grid">${selectField("房间","room_id",options(state.refs.rooms,r.room_id||model?.rack.room_id,"— 请选择房间 —"))}${field("架子名称","name","text",r.name||"","required")}<label class="field full"><span>位置说明（如楼层、朝向）</span><textarea name="note">${esc(r.note||"")}</textarea></label></div>`,async form=>{
      const result=await api(edit?`/api/rack-layouts/${selected}`:"/api/rack-layouts",{method:edit?"PUT":"POST",body:formData(form)});
      selected=result.rack.id;localStorage.setItem("mousdb-rack-id",String(selected));
    });
  }

  root.addEventListener("click",e=>{
    if(Date.now()-lastDrag<200||busy)return;
    const b=e.target.closest("button");if(!b)return;
    const d=b.dataset;
    try{
      if(d.rackNew!==undefined)return rackEditor();
      if(d.rackEdit!==undefined)return rackEditor(true);
      if(d.rackMove)return moveMenu(Number(d.rackMove));
      if(d.rackState)return stateMenu(Number(d.rackState));
      if(d.rackOpen)return cageDetail(Number(d.rackOpen));
      if(d.rackMouse)return mouseDetail(Number(d.rackMouse));
      if(d.rackAddMouse)return mouseForm({cage_id:Number(d.rackAddMouse)});
      if(d.addRow)return addCage(Number(d.addRow),Number(d.addCol)).catch(error=>notify(error.message,"error"));
      if(d.addParking!==undefined)return addCage(null,null).catch(error=>notify(error.message,"error"));
      if(b.id==="rack-undo")return save(`/api/rack-layouts/${selected}/undo`,{},"已撤销上一次移位").catch(()=>{});
    }catch(error){notify(error.message,"error");}
  });
  root.addEventListener("change",e=>{
    if(e.target.id==="rack-select"){selected=Number(e.target.value);load(selected).catch(error=>notify(error.message,"error"));}
    if(e.target.id==="rack-zoom"){zoom=e.target.value;localStorage.setItem("mousdb-rack-zoom",zoom);applyZoom();}
  });
  root.addEventListener("input",e=>{if(e.target.id==="rack-search"){search=e.target.value;applySearch();}});
  function highlightPhysicalTarget(e){
    if(!root.classList.contains("rack-is-dragging"))return;
    const point=e.touches?.[0]||e;
    const zone=document.elementFromPoint(point.clientX,point.clientY)?.closest(".rack-dropzone");
    root.querySelectorAll(".rack-target").forEach(x=>x.classList.remove("rack-target"));
    if(zone&&root.contains(zone))zone.classList.add("rack-target");
  }
  window.addEventListener("mousemove",highlightPhysicalTarget);
  window.addEventListener("touchmove",highlightPhysicalTarget,{passive:true});
  // 各个缩放档位都重新分配列宽，窗口变大后也不会留下多余空带。
  window.addEventListener("resize",applyZoom);
  return {load};
})();
