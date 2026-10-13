/* 实验计划的录入、管理和首页提醒。
 * 输入：后端计划及本机今天/明天日期；输出：红框区域的提醒卡和计划表单。
 * 只有明确日期且未完成的计划进入提醒。已读不等于实验完成；不使用浏览器UTC日期。
 * 不申请系统通知权限；提醒在网站内显示，刷新/跨日后仍从数据库读取。
 */
"use strict";
window.ExperimentPlans=(()=>{
  let all=[],types=[],loading=false,lastSignature="";
  const statusNames={pending:"待执行",completed:"已完成",cancelled:"已取消"};
  const localDay=()=>new Intl.DateTimeFormat('sv-SE').format(new Date());
  const ears=p=>p.mice.map(m=>m.ear_tag||m.code).join('、');
  const locations=p=>[...new Set(p.mice.filter(m=>m.cage_code).map(m=>`${m.rack_name} / ${m.cage_code}`))].join('；');

  function markup(){
    return '<section id="rack-plan-reminders" class="rack-plan-reminders" aria-label="今天和明天实验提醒" aria-live="polite"><div class="plan-reminder-heading"><strong>实验提醒</strong><div><button type="button" data-plan-add>＋ 计划</button><button type="button" data-plan-list>全部计划</button></div></div><div class="plan-reminder-items">正在读取计划……</div></section>';
  }

  async function refresh(announce=true){
    /* 只更新提醒区域，不重建架子，因此每分钟刷新不会打断笼卡拖动。 */
    const box=document.getElementById('rack-plan-reminders');
    if(!box||loading)return;
    loading=true;
    try{
      const data=await api('/api/plans/reminders');
      if(!box.isConnected)return;
      const signature=JSON.stringify(data);
      // 数据没变时保留DOM和键盘焦点；跨日或计划变更才替换卡片。
      if(signature!==lastSignature||!box.dataset.ready){
        lastSignature=signature;box.dataset.ready='1';
        box.querySelector('.plan-reminder-items').innerHTML=data.items.length?data.items.map(p=>`<button type="button" class="plan-reminder-item ${p.when}" data-plan-view="${p.ids.join(',')}" title="${esc(p.title+'；'+ears(p))}"><span class="plan-day">${p.when==='today'?'今天':'明天'}${p.plan_time?' '+esc(p.plan_time):''}</span><span class="plan-item-content"><b>${esc(p.title)}</b><small>${esc(ears(p)||'样本 / 实验室任务')}${p.mice.length?' · '+p.mice.length+'只':''}</small></span></button>`).join(''):'<p class="plan-empty">今天和明天暂无待执行计划</p>';
        const todayCount=data.items.filter(p=>p.when==='today').length,tomorrowCount=data.items.length-todayCount;
        box.querySelector('strong').textContent=data.items.length?`实验提醒 · 今天${todayCount} / 明天${tomorrowCount}`:'实验提醒';
        box.classList.toggle('has-due-plans',!!data.items.length);
        // 同一天同一组计划只弹一次提示；常驻卡片仍一直保留。
        const toastKey=data.today+'|'+data.items.map(p=>p.ids.join(',')).join('|');
        if(announce&&data.items.length&&sessionStorage.getItem('mousdb-plan-toast')!==toastKey){
          sessionStorage.setItem('mousdb-plan-toast',toastKey);
          notify(`实验提醒：今天${todayCount}项，明天${tomorrowCount}项。`, 'info');
        }
      }
    }catch(error){
      if(box.isConnected)box.querySelector('.plan-reminder-items').textContent='提醒读取失败：'+error.message;
    }finally{loading=false;}
  }

  function mount(){refresh().catch(error=>notify(error.message,'error'));}

  async function openList(ids=null){
    /* 独立计划弹窗，不挤占原小鼠档案弹窗；所有日期可编辑，已完成/取消可回查。 */
    const data=await api('/api/plans');all=data.plans;types=data.types;
    const dialog=document.getElementById('plan-dialog');
    dialog.dataset.ids=ids?ids.join(','):'';
    const content=document.getElementById('plan-dialog-content');
    content.innerHTML='<div class="plan-list-toolbar"><select id="plan-status-filter" aria-label="筛选计划状态"><option value="pending">待执行</option><option value="all">全部</option><option value="completed">已完成</option><option value="cancelled">已取消</option></select><button type="button" data-plan-add>＋ 新增计划</button></div><div id="plan-list-rows"></div>';
    document.getElementById('plan-status-filter').onchange=()=>renderList(ids);
    renderList(ids);if(!dialog.open)dialog.showModal();
  }

  function renderList(ids){
    const filter=document.getElementById('plan-status-filter').value;
    const rows=all.filter(p=>(filter==='all'||p.status===filter)&&(!ids||ids.includes(p.id)));
    document.getElementById('plan-list-rows').innerHTML=rows.map(p=>`<article class="plan-list-row"><div class="plan-list-date"><b>${esc(p.plan_date)}</b><small>${esc(p.plan_time||'未指定时刻')}</small></div><div class="plan-list-content"><b>${esc(p.title)}</b><span class="plan-status">${statusNames[p.status]}</span><p>${esc(ears(p)||'样本 / 实验室任务')}${p.mice.length?' · '+p.mice.length+'只':''}</p>${locations(p)?`<small>${esc(locations(p))}</small>`:''}${p.details?`<details><summary>备注</summary><p>${esc(p.details)}</p></details>`:''}</div><div class="plan-row-actions"><button type="button" data-plan-edit="${p.id}">编辑</button>${p.status==='pending'?`<button type="button" data-plan-complete="${p.id}">已完成</button>`:''}</div></article>`).join('')||'<p class="plan-empty">没有符合条件的计划</p>';
  }

  async function form(mouse=null,plan=null){
    /* 同一时间、同一操作填写全体耳号；后端自动并入同批计划，统一编辑和完成。
     * 不填耳号则是具体的样本/实验室任务，按任务标题区分。 */
    document.getElementById('plan-dialog').close();
    if(!types.length)types=(await api('/api/plans')).types;
    const p=plan||{};
    openEditor(p.id?'编辑实验计划':'新增实验计划',`<div class="form-grid">${field('任务标题','title','text',p.title||'','required')}${selectField('任务类型','plan_type',types.map(t=>`<option value="${esc(t)}" ${p.plan_type===t?'selected':''}>${esc(t)}</option>`).join(''))}${field('计划日期','plan_date','date',p.plan_date||localDay(),'required')}${field('时间（可空）','plan_time','time',p.plan_time||'')}${field('耳号（多只用顿号或逗号分隔）','ear_tags','text',p.id?ears(p):mouse?.ear_tag||'')}${selectField('执行状态','status',Object.entries(statusNames).map(([k,v])=>`<option value="${k}" ${(p.status||'pending')===k?'selected':''}>${v}</option>`).join(''))}<label class="field full"><span>计划备注</span><textarea name="details">${esc(p.details||'')}</textarea></label></div><p class="muted">同一日期、时刻和操作的鼠共用一个计划，请填写全体耳号。前一天和当天会在首页提醒；标记完成会应用于本批全部小鼠。</p>`,async f=>{
      const payload=formData(f);
      // 从无耳号档案进入时使用可靠的内部ID；避免把空耳号当作无关联任务。
      if(mouse&&!payload.ear_tags){payload.mouse_ids=[mouse.id];delete payload.ear_tags;}
      await api(p.id?`/api/plans/${p.id}`:'/api/plans',{method:p.id?'PUT':'POST',body:payload});
      lastSignature='';
    });
  }

  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-plan-add],[data-plan-list],[data-plan-view],[data-plan-edit],[data-plan-complete]');
    if(!button)return;
    try{
      if(button.hasAttribute('data-plan-add'))await form();
      else if(button.hasAttribute('data-plan-list'))await openList();
      else if(button.hasAttribute('data-plan-view'))await openList(button.dataset.planView.split(',').map(Number));
      else if(button.hasAttribute('data-plan-edit'))await form(null,all.find(p=>p.id===Number(button.dataset.planEdit)));
      else{
        button.disabled=true;
        await api(`/api/plans/${Number(button.dataset.planComplete)}`,{method:'PUT',body:{status:'completed'}});
        lastSignature='';await refresh(false);
        const ids=document.getElementById('plan-dialog').dataset.ids;
        await openList(ids?ids.split(',').map(Number):null);
      }
    }catch(error){notify(error.message,'error');}
    finally{button.disabled=false;}
  });
  // 页面开着时每分钟检查跨日；回到网站时立即检查。网站关闭时不产生系统推送。
  setInterval(()=>{if(!document.hidden&&state.page==='rack')refresh();},60000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&state.page==='rack')refresh();});
  return {markup,mount,refresh,openList,form};
})();
