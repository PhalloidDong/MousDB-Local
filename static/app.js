/* MousDB Local 单页界面。
 *
 * 浏览器只负责收集输入和展示结果。编号、父母性别、死亡移笼和事务回滚等
 * 关键规则全部由后端再次检查，避免网页被绕过时破坏数据。
 */
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? "").replace(/[&<>'"]/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[ch]));
const state = { page: "rack", refs: null, miceAll: [], cages: [], csvText: "", previewValid: false };

async function api(path, options = {}) {
  const config = {...options, headers: {"Content-Type":"application/json", ...(options.headers || {})}};
  if (config.body && typeof config.body !== "string") config.body = JSON.stringify(config.body);
  const response = await fetch(path, config);
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result.data;
}

function notify(message, type = "success") {
  const box = $("#notice"); box.innerHTML = `<div class="notice ${esc(type)}">${esc(message)}</div>`;
  window.setTimeout(() => box.replaceChildren(), 4500);
}

const titles = {
  rack:["动物房架位","与真实笼位一一对应"],
  dashboard:["概览","colony 当前状态与待办"], mice:["小鼠信息","非死亡小鼠档案"], dead:["死亡小鼠","保留完整历史的死亡记录"],
  protocols:["鉴定程序","引物与PCR程序原图"], results:["鉴定结果","鉴定结果原图资料"], breeding:["繁育管理","交配、产仔与离乳谱系"], import:["批量导入","预检后原子提交"], settings:["品系和备份","受控词表、导出和一致性备份"]
};

async function showPage(page) {
  document.body.classList.toggle("rack-page-active",page === "rack");
  state.page = page; $$(".page").forEach(x => x.classList.toggle("active", x.id === `page-${page}`));
  $$("#navigation button").forEach(x => x.classList.toggle("active", x.dataset.page === page));
  $("#page-title").textContent = titles[page][0]; $("#page-subtitle").textContent = titles[page][1];
  try { await loaders[page](); } catch (error) { notify(error.message, "error"); }
}

async function bootstrap() {
  const data = await api("/api/bootstrap"); state.refs = data.references;
  fillFilters();
}

function options(items, selected = "", blank = "— 未设置 —", label = "name") {
  return `<option value="">${esc(blank)}</option>` + items.map(x => `<option value="${x.id}" ${String(x.id)===String(selected)?"selected":""}>${esc(x[label])}${x.active===0?"（已停用）":""}</option>`).join("");
}

/*
 * 富文本品系选择器
 *
 * 原来的 <select> 只能从已有词条中选择，无法输入类似 “Col1a1-Cre⁺/⁻”、
 * “α-SMA²” 或带格式的实验室自定义名称。这里使用浏览器原生 contenteditable
 * 实现一个轻量选择器：输入框本身可以编辑，下面同时显示匹配的旧词条；提交时
 * 发送纯文本和经过后端白名单再次清洗的 HTML。这样既支持搜索/复用，也不会让
 * 用户的格式输入绕过后端安全检查。
 */
const RICH_ALLOWED_TAGS = new Set(["B", "STRONG", "I", "EM", "U", "SUP", "SUB"]);
const RICH_SYMBOLS = ["α", "β", "γ", "δ", "Δ", "μ", "Ω", "λ", "κ", "χ", "±", "×", "÷", "≥", "≤", "−", "∞", "°", "·", "⁺", "⁻", "ᵐ", "ᵖ", "₊", "₋", "ₓ"];

function sanitizeRichClient(markup) {
  /* 这是前端展示前的第二道防线；后端 db.py 仍是最终可信的清洗位置。 */
  const template = document.createElement("template");
  template.innerHTML = String(markup || "");
  const clean = node => {
    [...node.childNodes].forEach(child => {
      if (child.nodeType === Node.ELEMENT_NODE) {
        const element = /** @type {HTMLElement} */ (child);
        if (!RICH_ALLOWED_TAGS.has(element.tagName)) {
          while (element.firstChild) node.insertBefore(element.firstChild, element);
          element.remove();
        } else {
          [...element.attributes].forEach(attribute => element.removeAttribute(attribute.name));
          clean(element);
        }
      } else if (child.nodeType === Node.COMMENT_NODE) {
        child.remove();
      }
    });
  };
  clean(template.content);
  return template.innerHTML;
}

function richPlainText(editor) {
  /* innerText 能把用户误输入的换行转换成空格，词条不会被保存成多行。 */
  return String(editor.innerText || editor.textContent || "").replace(/\s+/g, " ").trim();
}

function setRichEditor(editor, markup, plain = "") {
  editor.innerHTML = sanitizeRichClient(markup || esc(plain));
}

function richPicker(kind, prefix, selectedId = "") {
  const label = {strains:"strain（可搜索或直接输入）", lines:"line（位于 genotype 前，可搜索或直接输入）", genotypes:"genotype（可搜索或直接输入）"}[kind] || prefix;
  const symbols = RICH_SYMBOLS.map(symbol => `<button type="button" class="rich-symbol" data-rich-symbol="${esc(symbol)}">${esc(symbol)}</button>`).join("");
  return `<div class="field rich-picker" data-rich-picker data-rich-kind="${kind}" data-rich-prefix="${prefix}" data-rich-selected="${esc(selectedId || "")}">
    <span>${label}</span>
    <div class="rich-toolbar" role="toolbar" aria-label="${label} 格式工具">
      <button type="button" class="rich-tool" data-rich-command="superscript" title="上标（Ctrl/Cmd+Shift+=）">上标</button>
      <button type="button" class="rich-tool" data-rich-command="subscript" title="下标（Ctrl/Cmd+=）">下标</button>
      <button type="button" class="rich-tool" data-rich-command="italic" data-rich-whole="true" title="斜体（Ctrl/Cmd+I；未选中文字时切换整个词条）"><i>斜体</i></button>
      <button type="button" class="rich-tool" data-rich-symbol-toggle title="插入常用特殊符号">Ω 符号</button>
      <div class="rich-symbol-menu" data-rich-symbol-menu hidden>${symbols}</div>
    </div>
    <div class="rich-editor" contenteditable="true" role="textbox" aria-multiline="false" data-rich-editor data-placeholder="直接输入或从下方匹配项选择"></div>
    <input type="hidden" name="${prefix}_id" value="${esc(selectedId || "")}" data-rich-id>
    <input type="hidden" name="${prefix}_name" value="" data-rich-name>
    <input type="hidden" name="${prefix}_html" value="" data-rich-html>
    <div class="rich-suggestions" data-rich-suggestions hidden></div>
    <small class="rich-help">支持特殊符号；选中文字后可点上标/下标/斜体。快捷键：Ctrl/Cmd+Shift+= 上标，Ctrl/Cmd+= 下标，Ctrl/Cmd+I 斜体。</small>
  </div>`;
}

function execRichCommand(editor, command, selectAllWhenCollapsed = false) {
  editor.focus();
  const selection = window.getSelection();
  if (selectAllWhenCollapsed && selection && selection.rangeCount && selection.isCollapsed) {
    const range = document.createRange(); range.selectNodeContents(editor); selection.removeAllRanges(); selection.addRange(range);
  }
  /* execCommand 仍是浏览器原生 contenteditable 的兼容实现，Windows Edge/Chrome 均支持。 */
  document.execCommand(command, false, null);
  editor.dispatchEvent(new Event("input", {bubbles:true}));
}

function insertRichSymbol(editor, symbol) {
  editor.focus();
  if (!document.execCommand("insertText", false, symbol)) {
    const selection = window.getSelection();
    if (selection && selection.rangeCount) {
      const range = selection.getRangeAt(0); range.deleteContents(); range.insertNode(document.createTextNode(symbol));
      range.collapse(false); selection.removeAllRanges(); selection.addRange(range);
    }
  }
  editor.dispatchEvent(new Event("input", {bubbles:true}));
}

/*
 * 统一收起所有符号面板。
 * 旧实现只切换 hidden 属性，但 CSS 中的 display:flex 会覆盖浏览器默认的
 * [hidden] 规则，所以菜单看起来“关不掉”。现在每次切换、选择符号、切换
 * 输入框或点击弹窗其他位置都会调用这个函数，并由 CSS 明确强制隐藏。
 */
function closeRichSymbolMenus(except = null) {
  $$('[data-rich-symbol-menu]').forEach(menu => {
    if (!except || !except.contains(menu)) menu.hidden = true;
  });
}

function setupRichPicker(picker) {
  const kind = picker.dataset.richKind; const prefix = picker.dataset.richPrefix;
  const items = state.refs?.[kind] || []; const editor = $("[data-rich-editor]", picker);
  const idInput = $("[data-rich-id]", picker); const nameInput = $("[data-rich-name]", picker); const htmlInput = $("[data-rich-html]", picker);
  const suggestions = $("[data-rich-suggestions]", picker); const selected = items.find(item => String(item.id) === String(picker.dataset.richSelected || ""));
  let dirty = false;
  setRichEditor(editor, selected?.name_html, selected?.name || "");

  const sync = () => {
    const html = sanitizeRichClient(editor.innerHTML); const plain = richPlainText(editor);
    // 只有真正发生清洗变化时才重设 innerHTML，避免每次按键都把光标跳到末尾。
    if (editor.innerHTML !== html) editor.innerHTML = html;
    nameInput.value = plain; htmlInput.value = html;
    if (dirty) idInput.value = "";
  };
  const hideSuggestions = () => { suggestions.hidden = true; };
  const showSuggestions = () => {
    const query = richPlainText(editor).toLocaleLowerCase();
    const matches = items.filter(item => !query || String(item.name || "").toLocaleLowerCase().includes(query)).slice(0, 30);
    suggestions.replaceChildren();
    matches.forEach(item => {
      const button = document.createElement("button"); button.type = "button"; button.className = "rich-option"; button.dataset.richOptionId = item.id;
      const content = document.createElement("span"); content.className = "rich-option-content"; content.innerHTML = sanitizeRichClient(item.name_html || esc(item.name));
      const hint = document.createElement("small"); hint.textContent = item.active === 0 ? "已停用（仅历史复用）" : "已有词条";
      button.append(content, hint); suggestions.append(button);
    });
    const direct = document.createElement("div"); direct.className = "rich-direct-hint"; direct.textContent = matches.length ? "继续输入可创建或匹配新词条；保存时自动去重。" : "当前没有匹配项，保存时会创建新词条。"; suggestions.append(direct);
    suggestions.hidden = false;
  };
  const choose = item => {
    dirty = false; idInput.value = item.id; nameInput.value = item.name || ""; htmlInput.value = sanitizeRichClient(item.name_html || esc(item.name));
    setRichEditor(editor, htmlInput.value, item.name); hideSuggestions(); editor.focus();
  };

  editor.addEventListener("input", () => { dirty = true; sync(); showSuggestions(); });
  editor.addEventListener("focus", () => { closeRichSymbolMenus(picker); showSuggestions(); });
  editor.addEventListener("blur", () => window.setTimeout(hideSuggestions, 180));
  picker.addEventListener("mousedown", event => {
    const tool = event.target.closest("button");
    if (tool) event.preventDefault(); // 点击工具时保留编辑器里的文字选区。
  });
  picker.addEventListener("click", event => {
    const option = event.target.closest("[data-rich-option-id]");
    if (option) { const item = items.find(x => String(x.id) === String(option.dataset.richOptionId)); if (item) choose(item); return; }
    const command = event.target.closest("[data-rich-command]");
    if (command) { execRichCommand(editor, command.dataset.richCommand, command.dataset.richWhole === "true"); return; }
    const symbol = event.target.closest("[data-rich-symbol]");
    if (symbol) { insertRichSymbol(editor, symbol.dataset.richSymbol); closeRichSymbolMenus(); return; }
    const toggle = event.target.closest("[data-rich-symbol-toggle]");
    if (toggle) {
      const menu = $("[data-rich-symbol-menu]", picker);
      const wasHidden = menu.hidden;
      closeRichSymbolMenus(picker);
      menu.hidden = !wasHidden;
      return;
    }
  });
  editor.addEventListener("keydown", event => {
    if (!(event.ctrlKey || event.metaKey)) return;
    if (event.key === "i" || event.key === "I") { event.preventDefault(); execRichCommand(editor, "italic", true); }
    else if (event.key === "=" && event.shiftKey) { event.preventDefault(); execRichCommand(editor, "superscript"); }
    else if (event.key === "=") { event.preventDefault(); execRichCommand(editor, "subscript"); }
  });
  sync();
}

function setupRichPickers(root = document) { $$("[data-rich-picker]", root).forEach(setupRichPicker); }

/* 点击当前富文本控件以外的位置时，立即隐藏符号菜单，避免它覆盖后续字段。 */
document.addEventListener("pointerdown", event => {
  const target = event.target instanceof Element ? event.target : null;
  const picker = target?.closest("[data-rich-picker]");
  const menu = target?.closest("[data-rich-symbol-menu]");
  const toggle = target?.closest("[data-rich-symbol-toggle]");
  if (!picker || (!menu && !toggle)) closeRichSymbolMenus();
});

/* 根据控件类型取得可匹配的候选项。父鼠/母鼠只从当前小鼠列表中筛选。 */
function matchItems(picker) {
  const kind = picker.dataset.matchKind;
  const sex = picker.dataset.matchSex;
  const items = kind === "cages" ? (state.cages || []) : (state.miceAll || []);
  return sex ? items.filter(item => item.sex === sex) : items;
}

/* 把数据库对象转换成用户容易辨认的显示文本，同时保留系统编号。 */
function matchLabel(item, kind) {
  if (kind === "cages") {
    return [item.room_name, item.rack_name, item.code].filter(Boolean).join(" / ") || `Cage #${item.id}`;
  }
  const place = [item.room_name, item.rack_name, item.cage_code].filter(Boolean).join(" / ");
  return [item.code, item.ear_tag ? `耳标 ${item.ear_tag}` : "无耳标", place ? `位置 ${place}` : "未入笼"].join(" · ");
}

/* 搜索文本包含编号、耳标和位置，用户不需要记住只能搜索哪一列。 */
function matchSearchText(item, kind) {
  if (kind === "cages") return [item.id, item.code, item.room_name, item.rack_name].filter(Boolean).join(" ").toLocaleLowerCase();
  return [item.id, item.code, item.ear_tag, item.room_name, item.rack_name, item.cage_code, item.owner, item.project].filter(Boolean).join(" ").toLocaleLowerCase();
}

/*
 * 关系字段的 HTML 模板。
 * 文本框用于输入关键词，隐藏 ID 用于真正提交外键；这样既能快速检索，
 * 又不会把用户随手输入的文字误当成不存在的房间或小鼠关系。
 */
function matchPicker(kind, prefix, selectedId = "", sex = "") {
  const label = {
    cage: "房间/笼位（可输入或从下方匹配）",
    father: "父鼠（可输入或从下方匹配）",
    mother: "母鼠（可输入或从下方匹配）"
  }[prefix] || prefix;
  const selected = matchItems({dataset:{matchKind:kind, matchSex:sex}}).find(item => String(item.id) === String(selectedId || ""));
  const selectedLabel = selected ? matchLabel(selected, kind) : "";
  return `<div class="field match-picker" data-match-picker data-match-kind="${esc(kind)}" data-match-prefix="${esc(prefix)}" data-match-sex="${esc(sex)}">
    <span>${label}</span>
    <input class="match-input" type="text" data-match-input value="${esc(selectedLabel)}" placeholder="输入编号、耳标、房间或笼号" autocomplete="off">
    <input type="hidden" name="${prefix}_id" value="${esc(selectedId || "")}" data-match-id>
    <input type="hidden" name="${prefix}_input" value="${esc(selectedLabel)}" data-match-text>
    <div class="match-suggestions" data-match-suggestions hidden></div>
    <small class="match-help">直接输入关键词后，从下方匹配项点击选择；未选择匹配项不会保存关系。</small>
  </div>`;
}

function setupMatchPicker(picker) {
  const kind = picker.dataset.matchKind;
  const input = $("[data-match-input]", picker);
  const idInput = $("[data-match-id]", picker);
  const textInput = $("[data-match-text]", picker);
  const suggestions = $("[data-match-suggestions]", picker);
  const hide = () => { suggestions.hidden = true; };
  const render = () => {
    const query = input.value.trim().toLocaleLowerCase();
    const matches = matchItems(picker).filter(item => !query || matchSearchText(item, kind).includes(query)).slice(0, 30);
    suggestions.replaceChildren();
    matches.forEach(item => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "match-option";
      option.dataset.matchOptionId = item.id;
      const content = document.createElement("span");
      content.textContent = matchLabel(item, kind);
      const hint = document.createElement("small");
      hint.textContent = item.sex || (item.active === 0 ? "已停用" : "可用");
      option.append(content, hint);
      suggestions.append(option);
    });
    const hint = document.createElement("div");
    hint.className = "rich-direct-hint";
    hint.textContent = matches.length ? "点击一项完成关联；继续输入可缩小范围。" : "没有匹配项，请检查编号、耳标或房间/笼号。";
    suggestions.append(hint);
    suggestions.hidden = false;
  };
  const choose = item => {
    idInput.value = item.id;
    input.value = matchLabel(item, kind);
    textInput.value = input.value;
    hide();
  };

  input.addEventListener("input", () => {
    /* 一旦用户重新编辑文本，旧 ID 就失效，必须重新点击候选项。 */
    idInput.value = "";
    textInput.value = input.value;
    render();
  });
  input.addEventListener("focus", render);
  input.addEventListener("blur", () => window.setTimeout(hide, 180));
  picker.addEventListener("mousedown", event => {
    if (event.target.closest("button")) event.preventDefault();
  });
  picker.addEventListener("click", event => {
    const option = event.target.closest("[data-match-option-id]");
    if (!option) return;
    const item = matchItems(picker).find(x => String(x.id) === String(option.dataset.matchOptionId));
    if (item) choose(item);
  });
}

function setupMatchPickers(root = document) { $$('[data-match-picker]', root).forEach(setupMatchPicker); }

function fillFilters() {
  const strain = `<option value="">全部 strain</option>` + state.refs.strains.map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join("");
  const line = `<option value="">全部 line</option>` + state.refs.lines.map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join("");
  $("#mouse-strain-filter").innerHTML = strain; $("#dead-strain-filter").innerHTML = strain;
  $("#mouse-line-filter").innerHTML = line; $("#dead-line-filter").innerHTML = line;
}

function badge(status) { return `<span class="badge ${esc(status)}">${esc(status)}</span>`; }
function locationText(m) { return [m.room_name,m.rack_name,m.cage_code].filter(Boolean).map(esc).join(" / ") || "—"; }
function richMarkup(markup, plain) { return sanitizeRichClient(markup || esc(plain || "—")); }

async function loadDashboard() {
  const d = await api("/api/dashboard");
  $("#metrics").innerHTML = [["current_mice","当前在笼小鼠"],["occupied_cages","已占用 cage"],["active_matings","活跃繁育"],["pending_weaning","待离乳窝次"]].map(([k,t])=>`<div class="metric"><b>${d[k]}</b><span>${t}</span></div>`).join("");
  $("#pending-litters").innerHTML = d.litters.length ? d.litters.map(x=>`<div class="litter-row"><b>${esc(x.code)}</b> · 出生 ${esc(x.birth_date)} · ${x.male_count+x.female_count+x.unknown_count} 只待离乳</div>`).join("") : "暂无待离乳窝次";
  $("#recent-audit").innerHTML = d.recent_audit.length ? d.recent_audit.map(x=>`<div class="audit-row"><b>${esc(x.summary)}</b><br><small>${esc(x.created_at)}</small></div>`).join("") : "暂无操作记录";
}

async function loadMice(scope = "alive") {
  const dead = scope === "dead"; const prefix = dead ? "dead" : "mouse";
  const query = new URLSearchParams({scope, search:$(`#${prefix}-search`).value, strain_id:$(`#${prefix}-strain-filter`).value, line_id:$(`#${prefix}-line-filter`).value});
  if (!dead) query.set("status",$("#mouse-status-filter").value);
  const rows = await api(`/api/mice?${query}`); if (!dead) state.miceAll = await api("/api/mice?scope=all");
  const columns = dead ? ["编号","性别","出生日期","死亡日期","死亡周龄","strain","line","genotype","死亡原因","负责人"] : ["编号","耳标","性别","周龄","strain","line","genotype","位置","状态","负责人"];
  const body = rows.map(m=>`<tr><td><button class="code-link" data-mouse="${m.id}">${esc(m.code)}</button></td>${dead ?
    `<td>${m.sex}</td><td>${esc(m.dob||"—")}</td><td>${esc(m.death_date||"—")}</td><td>${esc(m.age_label||"待确认")}</td><td><span class="rich-render">${richMarkup(m.strain_name_html,m.strain_name)}</span></td><td><span class="rich-render">${richMarkup(m.line_name_html,m.line_name)}</span></td><td><span class="rich-render">${richMarkup(m.genotype_name_html,m.genotype_name)}</span></td><td>${esc(m.death_reason||"—")}</td><td>${esc(m.owner||"—")}</td>` :
    `<td>${esc(m.ear_tag||"—")}</td><td>${m.sex}</td><td>${esc(m.age_label||"待确认")}</td><td><span class="rich-render">${richMarkup(m.strain_name_html,m.strain_name)}</span></td><td><span class="rich-render">${richMarkup(m.line_name_html,m.line_name)}</span></td><td><span class="rich-render">${richMarkup(m.genotype_name_html,m.genotype_name)}</span></td><td>${locationText(m)}</td><td>${badge(m.status)}</td><td>${esc(m.owner||"—")}</td>`}</tr>`).join("");
  $(`#${dead?"dead":"mice"}-table`).innerHTML = `<table><thead><tr>${columns.map(x=>`<th>${x}</th>`).join("")}</tr></thead><tbody>${body||`<tr><td colspan="${columns.length}">没有符合条件的记录</td></tr>`}</tbody></table>`;
  $$(`[data-mouse]`, $(`#${dead?"dead":"mice"}-table`)).forEach(b=>b.onclick=()=>mouseDetail(Number(b.dataset.mouse)));
}

async function mouseDetail(id) {
  const m=await api(`/api/mice/${id}`); const fields=[["系统编号",m.code],["耳标",m.ear_tag],["性别",m.sex],["状态",m.status],["出生日期",m.dob],["周龄",m.age_label],["位置",locationText(m)],["strain",m.strain_name],["line",m.line_name],["genotype",m.genotype_name],["父鼠",m.father_code],["母鼠",m.mother_code],["负责人",m.owner],["项目",m.project],["死亡日期",m.death_date],["死亡原因",m.death_reason],["转出去向",m.transfer_destination],["备注",m.comment]];
  $("#detail-body").innerHTML=`<h2>${esc(m.code)} 完整档案</h2><div class="detail-grid">${fields.map(([k,v])=>`<div><b>${k}</b>${esc(v??"—")}</div>`).join("")}</div><h3>事件</h3>${m.events.map(x=>`<div class="audit-row"><b>${esc(x.event_date)} · ${esc(x.title)}</b><br>${esc(x.details)}</div>`).join("")||"暂无"}<h3>笼位历史</h3>${m.movements.map(x=>`<div class="audit-row">${esc(x.moved_at)} · ${esc(x.from_cage_code||"空")} → ${esc(x.to_cage_code||"空")} · ${esc(x.note)}</div>`).join("")||"暂无"}<div class="quick-actions"><button id="edit-this">编辑档案</button><button id="event-this">记录事件</button><button id="plan-this">新增计划</button></div>`;
  $("#detail").showModal(); $("#edit-this").onclick=()=>{ $("#detail").close(); mouseForm(m); }; $("#event-this").onclick=()=>{ $("#detail").close(); eventForm(m); }; $("#plan-this").onclick=()=>{ $("#detail").close(); window.ExperimentPlans.form(m); };
}

function field(label,name,type="text",value="",extra="") { return `<label class="field"><span>${label}</span><input name="${name}" type="${type}" value="${esc(value??"")}" ${extra}></label>`; }
function selectField(label,name,html) { return `<label class="field"><span>${label}</span><select name="${name}">${html}</select></label>`; }
function formData(form) { return Object.fromEntries(new FormData(form).entries()); }

function openEditor(title, html, save) {
  $("#editor-title").textContent=title; $("#editor-body").innerHTML=html; const dialog=$("#editor"), form=$("#editor-form");
  // 动态弹窗插入 DOM 后再初始化富文本选择器，避免直接绑定到尚未存在的元素。
  setupRichPickers($("#editor-body"));
  // 房间/笼位、父鼠、母鼠使用同样的“输入后从候选项选择”机制。
  setupMatchPickers($("#editor-body"));
  form.onsubmit=async event=>{event.preventDefault(); try{await save(form); state.miceAll=[]; dialog.close(); await bootstrap(); await loaders[state.page](); notify("保存成功");}catch(error){notify(error.message,"error");}};
  dialog.showModal();
}

async function mouseForm(mouse=null) {
  if (!state.miceAll.length) state.miceAll=await api("/api/mice?scope=all");
  state.cages = await api("/api/cages");
  const m=mouse||{};
  const alive=state.miceAll.filter(x=>x.id!==m.id); const male=alive.filter(x=>x.sex==="M"),female=alive.filter(x=>x.sex==="F");
  const html=`<p class="muted">系统编号：${m.code?`<b>${esc(m.code)}</b>（不可修改）`:"保存时自动分配"}</p><div class="form-grid">
    ${field("耳标","ear_tag","text",m.ear_tag)}${selectField("性别","sex",["M","F","U"].map(x=>`<option ${m.sex===x?"selected":""}>${x}</option>`).join(""))}${field("出生日期","dob","date",m.dob)}${selectField("状态","status",["Active","Experimental","Transferred","Dead"].map(x=>`<option ${m.status===x?"selected":""}>${x}</option>`).join(""))}
    ${richPicker("strains","strain",m.strain_id)}${richPicker("lines","line",m.line_id)}${richPicker("genotypes","genotype",m.genotype_id)}${matchPicker("cages","cage",m.cage_id)}
    ${matchPicker("mice","father",m.father_id,"M")}${matchPicker("mice","mother",m.mother_id,"F")}${field("负责人","owner","text",m.owner)}${field("项目","project","text",m.project)}${field("死亡日期","death_date","date",m.death_date)}${field("死亡原因","death_reason","text",m.death_reason)}${field("转出日期","transfer_date","date",m.transfer_date)}${field("转出去向","transfer_destination","text",m.transfer_destination)}<label class="field full"><span>备注</span><textarea name="comment">${esc(m.comment||"")}</textarea></label></div>`;
  openEditor(m.id?`编辑 ${m.code}`:"录入小鼠",html,async form=>{const data=formData(form); await api(m.id?`/api/mice/${m.id}`:"/api/mice",{method:m.id?"PUT":"POST",body:data});});
}

function eventForm(m) {
  /* 预期操作必须保存为计划，实际操作仍用原事件接口，避免把计划误写成实验事实。 */
  openEditor(`为 ${m.code} 记录事件`,`<div class="form-grid">${selectField("记录性质","record_mode","<option value=\"actual\">已执行事件</option><option value=\"planned\">预期实验计划</option>")}${selectField("类型","event_type",["观察","给药","取材","实验","配鼠","检栓","成像","备注"].map(x=>`<option>${x}</option>`).join(""))}${field("日期","event_date","date",new Intl.DateTimeFormat('sv-SE').format(new Date()),"required")}${field("标题","title","text","","required")}<label class="field full"><span>详情</span><textarea name="details"></textarea></label></div>`,async form=>{
    const d=formData(form);
    if(d.record_mode==='planned'){
      const kind=['取材','配鼠','检栓','成像'].includes(d.event_type)?d.event_type:/Tam/i.test(d.title)?'注射Tam':/Bleo/i.test(d.title)?'注射Bleo':'实验';
      await api('/api/plans',{method:'POST',body:{title:d.title,plan_type:kind,plan_date:d.event_date,details:d.details,mouse_ids:[m.id]}});
    }else{delete d.record_mode;await api(`/api/mice/${m.id}/events`,{method:'POST',body:d});}
  });
}

async function cageDetail(id) {
  const c=await api(`/api/cages/${id}`);
  $("#detail-body").innerHTML=`<h2>Cage ${esc(c.code)}</h2><p>${esc(c.room_name)} / ${esc(c.rack_name)} · ${c.occupied}/${c.capacity} · ${esc(c.purpose||"未设置用途")}</p>${c.genotyping?.pending?`<p class="warning-text">待鉴定幼鼠 · 出生日期：${esc(c.genotyping.birth_dates.join('、')||'未提供')} · 数量：${c.genotyping.pup_count==null?'未提供':c.genotyping.pup_count}<br>${esc(c.genotyping.note)}</p>`:''}${c.note?`<p class="muted">${esc(c.note)}</p>`:''}<h3>当前小鼠</h3>${c.mice.map(m=>`<div class="audit-row"><button class="code-link" data-mouse="${m.id}">${esc(m.ear_tag||"无耳标")} · ${esc(m.code)}</button> · ${m.sex} · <span class="rich-render">${richMarkup(m.line_name_html,m.line_name||"未设置 line")}</span> · <span class="rich-render">${richMarkup(m.genotype_name_html,m.genotype_name)}</span> · ${esc(m.age_label||"待确认")}</div>`).join("")||"空 cage"}<div class="quick-actions"><button id="edit-cage">编辑 cage</button><button id="delete-cage" class="cage-delete-button" ${c.occupied||c.genotyping?.pending?'disabled':''} title="${c.genotyping?.pending?'先核对待鉴定幼鼠并清除标记后才能删除':c.occupied?'先移出在养小鼠后才能删除':'删除空笼卡，历史记录保留'}">删除该笼</button></div>${c.occupied?'<p class="muted">本笼仍有在养小鼠，请先移笼再删除。</p>':''}`;
  $("#detail").showModal();
  $$(`[data-mouse]`,$("#detail")).forEach(x=>x.onclick=()=>{$("#detail").close();mouseDetail(Number(x.dataset.mouse));});
  $("#edit-cage").onclick=()=>{$("#detail").close();cageForm(c);};
  $("#delete-cage").onclick=async()=>{
    // 点击后再次由服务器检查空笼，避免打开详情后有人新录鼠造成误删。
    if(!window.confirm(`删除空笼“${c.code}”？笼位将释放，历史记录保留。`))return;
    const button=$("#delete-cage");button.disabled=true;
    try{
      await api(`/api/cages/${id}`,{method:'DELETE'});
      $("#detail").close();state.cages=[];state.miceAll=[];
      await loaders[state.page]();notify('已删除空笼卡，历史记录保留');
    }catch(error){button.disabled=false;notify(error.message,'error');}
  };
}


async function cageForm(cage=null){const racks=state.refs.racks.map(x=>({...x,label:`${x.room_name}/${x.name}`}));const c=cage||{};const html=`<div class="form-grid">${selectField("Rack","rack_id",options(racks,c.rack_id,"— 请选择 —","label"))}${field("笼号","code","text",c.code,"required")}${field("容量","capacity","number",c.capacity||5,"min=1 required")}${field("用途","purpose","text",c.purpose)}${field("笼卡颜色","color","text",c.color)}<label class="full"><input type="checkbox" name="genotyping_pending" ${c.genotyping?.pending?"checked":""}> 有待鉴定幼鼠（出生记录保留；不自动增加个体）</label><label class="field full"><span>备注</span><textarea name="note">${esc(c.note||"")}</textarea></label></div>${c.id?"":`<hr><h3>同时录入小鼠（可选）</h3><div id="cage-mice"></div><button type="button" id="add-cage-mouse">＋ 添加一行小鼠</button>`}`;
  openEditor(c.id?`编辑 cage ${c.code}`:"新建 Cage",html,async form=>{const data=formData(form);data.genotyping={...(c.genotyping||{}),pending:form.querySelector("[name=genotyping_pending]").checked};delete data.genotyping_pending;if(!c.id)data.mice=$$(".cage-mouse-row",form).map(r=>Object.fromEntries($$("[name]",r).map(input=>[input.name,input.value])));await api(c.id?`/api/cages/${c.id}`:"/api/cages",{method:c.id?"PUT":"POST",body:data});});
  if(!c.id)$("#add-cage-mouse").onclick=()=>{const row=document.createElement("div");row.className="cage-mouse-row form-grid";row.innerHTML=`${field("耳标","ear_tag")}${selectField("性别","sex","<option>M</option><option>F</option><option>U</option>")}${field("出生日期","dob","date")}${richPicker("strains","strain")}${richPicker("lines","line")}${richPicker("genotypes","genotype")}<button type="button" class="remove-row">移除此行</button>`;$("#cage-mice").append(row);setupRichPickers(row);$(".remove-row",row).onclick=()=>row.remove();};
}

async function loadBreeding(){const rows=await api("/api/matings");$("#mating-list").innerHTML=rows.map(m=>`<article class="mating-card"><h3>${esc(m.code)}</h3><p>${badge(m.status)} · 开始 ${esc(m.start_date)} · ${m.female_ids.length} 只雌鼠</p><p>${esc(m.project||m.purpose||"未设置项目")}</p><button data-litter="${m.id}">报告产仔</button>${m.litters.filter(x=>x.status==="Born").map(x=>`<button data-wean="${x.id}">离乳 ${esc(x.code)}</button>`).join("")}</article>`).join("")||"尚无繁育记录";$$(`[data-litter]`).forEach(x=>x.onclick=()=>litterForm(Number(x.dataset.litter)));$$(`[data-wean]`).forEach(x=>x.onclick=()=>weanForm(Number(x.dataset.wean)));}

async function matingForm(){const all=await api("/api/mice?scope=alive"),males=all.filter(x=>x.sex==="M"),females=all.filter(x=>x.sex==="F");openEditor("建立繁育",`<div class="form-grid">${field("交配编号","code","text","","required")}${field("开始日期","start_date","date",new Date().toISOString().slice(0,10),"required")}${selectField("雄鼠","male_id",options(males,"","— 请选择 —","code"))}<label class="field"><span>雌鼠（Ctrl 可多选）</span><select name="female_ids" multiple size="5">${females.map(x=>`<option value="${x.id}">${esc(x.code)} · ${esc(x.ear_tag||"无耳标")}</option>`).join("")}</select></label>${richPicker("strains","offspring_strain")}${richPicker("lines","offspring_line")}${field("项目","project")}${field("目的","purpose")}</div>`,async form=>{const data=formData(form);data.female_ids=$$("[name=female_ids] option:checked",form).map(x=>x.value);await api("/api/matings",{method:"POST",body:data});});}
async function litterForm(matingId){const m=(await api("/api/matings")).find(x=>x.id===matingId),all=await api("/api/mice?scope=all"),females=all.filter(x=>m.female_ids.includes(x.id));openEditor("报告产仔",`<div class="form-grid">${field("窝号","code","text","","required")}${field("出生日期","birth_date","date",new Date().toISOString().slice(0,10),"required")}${selectField("母鼠","mother_id",options(females,"","— 必须选择 —","code"))}${field("雄仔数","male_count","number",0,"min=0")}${field("雌仔数","female_count","number",0,"min=0")}${field("未知性别","unknown_count","number",0,"min=0")}${field("死亡数","dead_count","number",0,"min=0")}</div>`,f=>api(`/api/matings/${matingId}/litters`,{method:"POST",body:formData(f)}));}
async function weanForm(litterId){const cages=(await api("/api/cages")).map(x=>({...x,name:`${x.room_name}/${x.rack_name}/${x.code}`}));openEditor("整窝离乳",`<p class="warning-text">系统会按窝次数量逐只连续编号，并继承父母与后代 strain/line。</p><div class="form-grid">${field("离乳日期","weaned_date","date",new Date().toISOString().slice(0,10),"required")}${richPicker("genotypes","genotype")}${selectField("雄鼠 cage","male_cage_id",options(cages))}${selectField("雌鼠 cage","female_cage_id",options(cages))}${selectField("未知性别 cage","unknown_cage_id",options(cages))}</div>`,f=>api(`/api/litters/${litterId}/wean`,{method:"POST",body:formData(f)}));}

function loadImport() {}
async function previewImport(){const file=$("#csv-file").files[0];if(!file)throw new Error("请先选择 CSV 文件");state.csvText=await file.text();const d=await api("/api/import/preview",{method:"POST",body:{csv_text:state.csvText,auto_create:$("#auto-create").checked}});state.previewValid=d.valid;$("#import-result").innerHTML=d.rows.map(x=>`<div class="import-row"><b>第 ${x.row} 行 → ${esc(x.predicted_code)}</b>${x.errors.map(e=>`<div class="error-text">错误：${esc(e)}</div>`).join("")}${x.warnings.map(w=>`<div>提示：${esc(w)}</div>`).join("")}</div>`).join("");$("#commit-import").classList.toggle("hidden",!d.valid);}
async function commitImport(){if(!state.previewValid)return;const d=await api("/api/import/commit",{method:"POST",body:{csv_text:state.csvText,auto_create:$("#auto-create").checked}});notify(`已原子导入 ${d.count} 只小鼠`);state.previewValid=false;$("#commit-import").classList.add("hidden");}

function loadSettings(){const names={rooms:"Room",strains:"Strain",lines:"Line",genotypes:"Genotype"};$("#reference-list").innerHTML=Object.entries(names).map(([kind,label])=>`<h3>${label} <button data-new-ref="${kind}">＋</button></h3><div>${state.refs[kind].map(x=>`<button data-edit-ref="${kind}:${x.id}" class="badge">${esc(x.name)}${x.active===0?"（停用）":""}</button>`).join(" ")||"—"}</div>`).join("");$$(`[data-new-ref]`).forEach(x=>x.onclick=()=>referenceForm(x.dataset.newRef));$$(`[data-edit-ref]`).forEach(x=>x.onclick=()=>{const[k,id]=x.dataset.editRef.split(":");referenceForm(k,state.refs[k].find(v=>v.id===Number(id)));});}
function referenceForm(kind,item=null){const label={rooms:"Room",strains:"Strain",lines:"Line",genotypes:"Genotype"}[kind];const nameEditor=kind==="rooms"?field("名称","name","text",item?.name,"required"):richPicker(kind,"name",item?.id);openEditor(`${item?"编辑":"新建"} ${label}`,`<div class="form-grid">${nameEditor}${kind==="rooms"?field("建筑","building","text",item?.building):field("说明","description","text",item?.description)}${item?`<label><input name="active" type="checkbox" value="1" ${item.active?"checked":""}> 启用</label>`:""}</div>`,async f=>{const d=formData(f);if(kind!=="rooms"){d.name=d.name_name;d.name_html=d.name_html;delete d.name_id;delete d.name_name;}if(item)d.active=$("[name=active]",f).checked;await api(`/api/references/${kind}${item?`/${item.id}`:""}`,{method:item?"PUT":"POST",body:d});});}

const loaders={rack:()=>window.RackBoard.load(),dashboard:loadDashboard,mice:()=>loadMice("alive"),dead:()=>loadMice("dead"),protocols:()=>window.GenotypingLibrary.load("protocol"),results:()=>window.GenotypingLibrary.load("result"),breeding:loadBreeding,import:loadImport,settings:loadSettings};

document.addEventListener("DOMContentLoaded",async()=>{
  $$(`[data-page]`).forEach(x=>x.addEventListener("click",()=>showPage(x.dataset.page)));
  $$(`[data-action=new-mouse]`).forEach(x=>x.onclick=()=>mouseForm());
  $("[data-action=new-mating]").onclick=matingForm;
  $$("dialog .cancel, dialog .dialog-close").forEach(x=>x.addEventListener("click",()=>x.closest("dialog").close()));
  $("#refresh").onclick=()=>showPage(state.page);$("#preview-import").onclick=()=>previewImport().catch(e=>notify(e.message,"error"));$("#commit-import").onclick=()=>commitImport().catch(e=>notify(e.message,"error"));
  ["mouse-search","mouse-strain-filter","mouse-line-filter","mouse-status-filter"].forEach(id=>$("#"+id).addEventListener("change",()=>loadMice("alive")));
  ["dead-search","dead-strain-filter","dead-line-filter"].forEach(id=>$("#"+id).addEventListener("change",()=>loadMice("dead")));
  try{await bootstrap();await loadDashboard();await showPage("rack");}catch(error){notify(error.message,"error");}
});
