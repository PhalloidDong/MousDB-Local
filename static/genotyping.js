/* 鉴定图片目录：输入为本机资料元数据/原图；输出为可检索卡片和大图查看弹窗。
 * 只展示用户上传的图片，不做OCR、序列解析或基因型判定。标题和备注始终HTML转义。
 */
"use strict";
window.GenotypingLibrary=(()=>{
  const names={protocol:"鉴定程序",result:"鉴定结果"},searches={protocol:"",result:""};
  const roots={protocol:document.getElementById("genotyping-protocol"),result:document.getElementById("genotyping-result")};
  const viewer=document.getElementById("genotyping-viewer");
  const documents={protocol:[],result:[]};

  async function load(category){
    /* 每个目录独立读取和搜索，切换目录不会把程序图片混进结果。 */
    documents[category]=await api(`/api/genotyping-documents?category=${category}`);
    const root=roots[category];
    root.innerHTML=`<div class="media-library-bar"><div><span class="media-eyebrow">${category==='protocol'?'GENOTYPING PROTOCOLS':'GENOTYPING RESULTS'}</span><p>${category==='protocol'?'按基因整理引物与PCR程序的原图':'按品系和日期归档，点开后查看原图'}</p></div><button type="button" class="primary" data-media-new="${category}">＋ 上传${category==='protocol'?'程序':'结果'}</button></div><div class="media-library-tools"><input type="search" data-media-search="${category}" aria-label="搜索${names[category]}" placeholder="${category==='result'?'搜索品系、日期或备注':'搜索标题、基因或备注'}" value="${esc(searches[category])}"><span data-media-count></span></div><div class="media-library-grid"></div>`;
    renderCards(category);
  }

  function renderCards(category){
    /* 程序目录保留封面；结果目录只显示品系和日期，不插入img或预加载原图。 */
    const root=roots[category],query=searches[category].trim().toLowerCase();
    const rows=documents[category].filter(d=>[d.title,d.gene,d.note,d.strain,d.record_date].join(' ').toLowerCase().includes(query));
    if(category==='result')return renderResults(rows,query);
    root.querySelector('[data-media-count]').textContent=`${rows.length} 张资料卡`;
    root.querySelector('.media-library-grid').innerHTML=rows.map(d=>`<button type="button" class="media-document-card" data-media-open="${d.id}"><span class="media-card-cover">${d.cover_url?`<img src="${d.cover_url}" alt="${esc(d.title)}的原图预览" loading="lazy">`:'<span class="media-cover-empty">＋<small>待添加图片</small></span>'}<span class="media-image-count">${d.image_count} 张图片</span></span><span class="media-card-info"><strong>${esc(d.title)}</strong><span>${esc(d.gene||'未填写基因名称')}</span><small>点击查看原图</small></span></button>`).join('')||`<div class="media-library-empty"><b>${query?'没有找到匹配的资料':'这里还没有'+names[category]}</b><p>${query?'换一个标题或基因名称试试':'点击上方上传按钮，添加第一张资料卡。'}</p></div>`;
  }

  function renderResults(rows,query){
    /* 同一品系、同一日期归为一个入口；日期倒序。未知分类单独标明，绝不猜测实验时间。 */
    const root=roots.result,groups=new Map();
    rows.forEach(d=>{
      const strain=String(d.strain||'').trim()||'未分类品系',day=d.record_date||'';
      if(!groups.has(strain))groups.set(strain,new Map());
      const dates=groups.get(strain);if(!dates.has(day))dates.set(day,[]);dates.get(day).push(d);
    });
    let total=0;
    const markup=[...groups].sort(([a],[b])=>a.localeCompare(b,'zh-CN')).map(([strain,dates])=>{
      const cards=[...dates].sort(([a],[b])=>b.localeCompare(a)).map(([day,docs])=>{
        total++;const count=docs.reduce((sum,d)=>sum+d.image_count,0);
        return `<button type="button" class="media-result-entry" data-media-result-open="${docs.map(d=>d.id).join(',')}"><span class="media-result-date">${esc(day||'日期未填写')}</span><span>${count} 张原图</span><small>点击查看 ↗</small></button>`;
      }).join('');
      return `<section class="media-result-group"><h2>${esc(strain)}</h2><div class="media-result-dates">${cards}</div></section>`;
    }).join('');
    root.querySelector('[data-media-count]').textContent=`${total} 组结果`;
    root.querySelector('.media-library-grid').classList.add('media-result-index');
    root.querySelector('.media-library-grid').innerHTML=markup||`<div class="media-library-empty"><b>${query?'没有找到匹配结果':'这里还没有鉴定结果'}</b><p>${query?'换一个品系或日期试试':'上传时填写品系和归档日期。'}</p></div>`;
  }

  function photoCards(d){
    /* 只有点开资料后才生成原图节点；图片仍来自同一私有数据接口。 */
    return d.images.map(photo=>`<figure class="media-photo-card"><a href="${photo.url}" target="_blank" rel="noopener" title="打开原图"><img src="${photo.url}" alt="${esc(d.title)}：${esc(photo.filename)}" loading="lazy"></a><figcaption><span>${esc(photo.filename)}</span><div><a href="${photo.url}" target="_blank" rel="noopener">查看原图 ↗</a><button type="button" data-media-remove-image="${photo.id}" data-media-document="${d.id}" data-media-category="${d.category}">移除</button></div></figcaption></figure>`).join('')||'<p class="media-library-empty">这张卡片还没有图片，可点击“编辑 / 补充图片”上传。</p>';
  }

  function actions(d){
    return `<div class="media-viewer-actions"><button type="button" data-media-edit="${d.id}">编辑 / 补充图片</button><button type="button" class="media-remove" data-media-archive="${d.id}" data-media-category="${d.category}">移除此卡片</button></div>`;
  }

  async function openDocument(id){
    const d=await api(`/api/genotyping-documents/${id}`);
    if(d.category==='result')return openResults([d.id]);
    document.getElementById('genotyping-viewer-body').innerHTML=`<header class="media-viewer-heading"><span class="media-eyebrow">${esc(names[d.category])}</span><h2>${esc(d.title)}</h2><p>${esc(d.gene)}</p>${actions(d)}</header>${d.note?`<p class="media-document-note">${esc(d.note)}</p>`:''}<div class="media-photo-list">${photoCards(d)}</div>`;
    if(!viewer.open)viewer.showModal();
  }

  async function openResults(ids){
    /* 一次打开该品系当日的全部批次。原图优先展示，逐鼠汇总折叠保留，避免长备注把图挤到下方。 */
    const docs=await Promise.all(ids.map(id=>api(`/api/genotyping-documents/${id}`))),first=docs[0];
    document.getElementById('genotyping-viewer-body').innerHTML=`<header class="media-viewer-heading"><span class="media-eyebrow">鉴定结果</span><h2>${esc(first.strain||'未分类品系')}</h2><p>${esc(first.record_date||'日期未填写')}</p></header>${docs.map(d=>`<section class="media-result-batch">${docs.length>1?`<h3>${esc(d.title)}</h3>`:''}${actions(d)}<div class="media-photo-list">${photoCards(d)}</div>${d.note?`<details class="media-result-summary"><summary>查看鉴定汇总与去留记录</summary><p class="media-document-note">${esc(d.note)}</p></details>`:''}</section>`).join('')}`;
    if(!viewer.open)viewer.showModal();
  }

  function form(category,item=null){
    /* 元数据与每张原图分别提交；中断后重试复用已建卡片，跳过本轮成功上传的File。
     * 上传中禁用重复保存和关闭，失败会保留输入，防止重复卡片或丢失用户已填信息。
     */
    let savedId=item?.id||null,saving=false;
    const uploaded=new Set();
    // 结果录入必须有品系和归档日期；未指定实验日期时默认本机今天作为归档日。
    const grouping=category==='result'?`${field('品系 / Line','strain','text',item?.strain||'','required maxlength="150"')}${field('归档日期','record_date','date',item?.record_date||new Intl.DateTimeFormat('sv-SE').format(new Date()),'required')}`:'';
    openEditor(`${item?'编辑':'上传'}${names[category]}`,`<div class="form-grid">${grouping}${field('标题','title','text',item?.title||'','required maxlength="200"')}${field('基因名称','gene','text',item?.gene||'','maxlength="150"')}<label class="field full"><span>图片（可多选）</span><input name="images" type="file" accept="image/jpeg,image/png,image/webp" multiple ${item?'':'required'}><small class="muted">JPG、PNG、WebP，每张不超过8 MB</small></label><label class="field full"><span>备注（可选）</span><textarea name="note" maxlength="20000">${esc(item?.note||'')}</textarea></label></div><p class="media-upload-progress" aria-live="polite"></p>`,async f=>{
      if(saving)throw new Error('正在上传，请稍候');
      const files=[...f.querySelector('[name=images]').files];
      for(const file of files){if(file.size>8*1024*1024)throw new Error(`${file.name}超过8 MB`);if(!/\.(?:jpe?g|png|webp)$/i.test(file.name))throw new Error('请上传JPG、PNG或WebP图片');}
      saving=true;
      const buttons=[...document.querySelectorAll('#editor button')];buttons.forEach(b=>b.disabled=true);
      try{
        const d=await api(savedId?`/api/genotyping-documents/${savedId}`:'/api/genotyping-documents',{method:savedId?'PUT':'POST',body:{category,title:f.elements.title.value,gene:f.elements.gene.value,note:f.elements.note.value,...(category==='result'?{strain:f.elements.strain.value,record_date:f.elements.record_date.value}:{})}});
        savedId=d.id;
        for(let index=0;index<files.length;index++){
          const file=files[index];if(uploaded.has(file))continue;
          f.querySelector('.media-upload-progress').textContent=`正在上传 ${index+1} / ${files.length} …`;
          const response=await fetch(`/api/genotyping-documents/${savedId}/images?filename=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream'},body:file});
          const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'图片上传失败');
          uploaded.add(file);
        }
      }finally{
        saving=false;buttons.forEach(b=>b.disabled=false);
        f.querySelector('.media-upload-progress').textContent='';
        if(savedId)await load(category); // 失败时也能看到已成功保存的图片，重新提交继续补充。
      }
    });
    const editor=document.getElementById('editor');
    const preventCancel=event=>{if(saving)event.preventDefault();};
    editor.addEventListener('cancel',preventCancel);
    editor.addEventListener('close',()=>editor.removeEventListener('cancel',preventCancel),{once:true});
  }

  // 页面级事件委托只绑定一次，刷新目录不会叠加上传/删除事件。
  document.addEventListener('click',async event=>{
    const b=event.target.closest('[data-media-new],[data-media-result-open],[data-media-open],[data-media-edit],[data-media-archive],[data-media-remove-image]');
    if(!b)return;
    try{
      if(b.dataset.mediaNew)return form(b.dataset.mediaNew);
      if(b.dataset.mediaResultOpen)return await openResults(b.dataset.mediaResultOpen.split(',').map(Number));
      if(b.dataset.mediaOpen)return await openDocument(Number(b.dataset.mediaOpen));
      if(b.dataset.mediaEdit){const d=await api(`/api/genotyping-documents/${b.dataset.mediaEdit}`);viewer.close();return form(d.category,d);}
      if(b.dataset.mediaArchive){
        if(!confirm('移除此资料卡？原图仍保留在本机历史数据中。'))return;
        b.disabled=true;await api(`/api/genotyping-documents/${b.dataset.mediaArchive}`,{method:'DELETE'});viewer.close();await load(b.dataset.mediaCategory);notify('已移除资料卡');
      }else if(b.dataset.mediaRemoveImage){
        if(!confirm('从此卡片移除这张图片？'))return;
        b.disabled=true;await api(`/api/genotyping-images/${b.dataset.mediaRemoveImage}`,{method:'DELETE'});await openDocument(Number(b.dataset.mediaDocument));await load(b.dataset.mediaCategory);
      }
    }catch(error){b.disabled=false;notify(error.message,'error');}
  });
  document.addEventListener('input',event=>{const category=event.target.dataset.mediaSearch;if(category){searches[category]=event.target.value;renderCards(category);}});
  return {load};
})();
