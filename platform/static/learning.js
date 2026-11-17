'use strict';
const $ = id => document.getElementById(id);
let course, current, library, switching = false, dirty = false, filter = 'all', saving = false;
let pdfDocument, pdfTask, pdfKey, renderVersion = 0;
const pdfLibrary = import(new URL('static/vendor/pdfjs/pdf.mjs', document.baseURI).href).then(lib => {
  lib.GlobalWorkerOptions.workerSrc = new URL('static/vendor/pdfjs/pdf.worker.mjs', document.baseURI).href;
  return lib;
});
const statusNames = {not_started:'未开始', studying:'学习中', completed:'已学完'};
const api = async (path, options) => {
  const response = await fetch(new URL(`api/learning/${path}`, document.baseURI), options);
  if (!response.ok) {
    let reason; try { reason = (await response.json()).detail; } catch (_) {}
    throw Error(typeof reason === 'string' ? reason : `请求失败（${response.status}），请重试`);
  }
  return response.json();
};
const notify = text => { $('message').textContent = text; };
function markDirty() { dirty = true; $('save-status').textContent = '有未保存修改'; }
function fileURL(download = false) {
  const url = new URL(`api/learning/files/${current.id}/${$('resource').value}`, document.baseURI);
  url.searchParams.set('course', course.id);
  const revision = resource()?.sha256;
  if (revision) url.searchParams.set('v', revision.slice(0,16));
  if (download) url.searchParams.set('download', 'true');
  return url;
}
function resource() { return current.resources.find(x => x.id === $('resource').value); }
function validPage() {
  const p = Number($('page').value);
  if (!Number.isInteger(p) || p < 1 || p > resource().pages) throw Error(`页码应为 1—${resource().pages} 的整数`);
  return p;
}
function showPDF() {
  const selected = resource();
  $('pages').textContent = `/ ${selected.pages} 页`;
  $('page').max = selected.pages;
  const url = fileURL(); url.hash = `page=${validPage()}&view=FitH&zoom=page-width`;
  $('pdf').dataset.src = url.href;
  $('previous').disabled = validPage() === 1;
  $('next').disabled = validPage() === selected.pages;
  renderPDF(fileURL().href, validPage(), selected.available);
  showTranslation();
  $('open-pdf').href = url.href;
  $('download-pdf').href = fileURL(true).href;
  $('source-pdf').href = selected.source || course.source;
  const chinese = current.resources.find(r=>r.id==='zh-study') || current.resources.find(r=>r.id==='zh');
  $('download-study').hidden = !chinese?.available;
  if (chinese?.available) {
    const download = new URL(`api/learning/files/${current.id}/${chinese.id}`,document.baseURI);
    download.searchParams.set('course',course.id); download.searchParams.set('download','true');
    if (chinese.sha256) download.searchParams.set('v',chinese.sha256.slice(0,16));
    $('download-study').href=download.href;
  }
  if (!selected.available) notify('这份本地资料暂不可用，请从课程原主页查看。');
}

async function renderPDF(url, pageNumber, available) {
  const version = ++renderVersion;
  $('pdf-status').textContent = available ? '正在加载讲义…' : '本地资料暂不可用';
  $('pdf-canvas').replaceChildren();
  if (!available) return;
  try {
    const lib = await pdfLibrary;
    if (version !== renderVersion) return;
    if (url !== pdfKey) {
      const oldTask = pdfTask; pdfTask = null; pdfKey = null;
      if (oldTask) await oldTask.destroy();
      if (version !== renderVersion) return;
      pdfKey = url; pdfDocument = null;
      pdfTask = lib.getDocument({url,
        cMapUrl:new URL('static/vendor/pdfjs/cmaps/',document.baseURI).href, cMapPacked:true,
        standardFontDataUrl:new URL('static/vendor/pdfjs/standard_fonts/',document.baseURI).href,
        wasmUrl:new URL('static/vendor/pdfjs/wasm/',document.baseURI).href});
    }
    const doc = pdfDocument || await pdfTask.promise;
    if (version !== renderVersion) return;
    pdfDocument = doc;
    const page = await doc.getPage(pageNumber);
    if (version !== renderVersion) return;
    const width = Math.max(200, $('pdf').clientWidth - 36);
    const viewport = page.getViewport({scale:width / page.getViewport({scale:1}).width});
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const canvas = document.createElement('canvas');
    canvas.width = Math.ceil(viewport.width * ratio); canvas.height = Math.ceil(viewport.height * ratio);
    canvas.style.width = `${viewport.width}px`;
    canvas.setAttribute('role','img'); canvas.setAttribute('aria-label',`${current.title}，第 ${pageNumber} 页`);
    await page.render({canvasContext:canvas.getContext('2d'), viewport, transform:[ratio,0,0,ratio,0,0]}).promise;
    if (version !== renderVersion) return;
    $('pdf-canvas').replaceChildren(canvas); $('pdf').scrollTop = 0;
    $('pdf-status').textContent = `第 ${pageNumber} / ${doc.numPages} 页`;
  } catch(e) {
    if (version !== renderVersion) return;
    pdfKey = null;
    $('pdf-status').textContent = '讲义加载未完成，请点击“跳转”重试，或在新窗口阅读。';
  }
}
let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (current) {try {showPDF();} catch (_) {}} }, 180);
});
for (const [id, delta] of [['previous',-1],['next',1]]) $(id).onclick = () => {
  try { if (saving || switching) return; const page = validPage() + delta; if (page < 1 || page > resource().pages) return;
    $('page').value = page; markDirty(); showPDF(); notify('');
  } catch(e) { notify(e.message); }
};

function renderList() {
  const query = $('search').value.trim().toLowerCase();
  const list = course.lessons.filter(l => (filter === 'all' || filter === l.kind) && `${l.title} ${l.module} ${l.goal}`.toLowerCase().includes(query));
  $('count').textContent = `${list.length} 份资料`;
  $('lessons').replaceChildren();
  for (const lesson of list) {
    const button = document.createElement('button'); button.className = 'lesson';
    button.setAttribute('aria-current', String(lesson.id === current?.id));
    for (const [cls, text] of [['meta', `${lesson.kind === 'lecture' ? '讲义' : '教程'} ${lesson.id.slice(-2)} · ${lesson.module}`], ['name',lesson.title], ['state',`${statusNames[course.records[lesson.id]?.status || 'not_started']} · ${lesson.resources[0].pages} 页 · ${lesson.resources.some(r=>(r.id==='zh-study'||r.id==='zh')&&r.available)?'中文可读':'中文制作中'}`]]) {
      const span = document.createElement('span'); span.className = cls; span.textContent = text; button.append(span);
    }
    button.onclick = () => choose(lesson.id); $('lessons').append(button);
  }
  if (!list.length) $('lessons').textContent = '没有匹配的章节，请调整关键词。';
  const lectures = course.lessons.filter(l => l.kind === 'lecture');
  const completed = lectures.filter(l => course.records[l.id]?.status === 'completed').length;
  $('completed').textContent = `${completed} / ${lectures.length}`;
  $('progress').max = lectures.length; $('progress').value = completed;
}
function choose(id) {
  if (saving || switching) return;
  if (dirty && !confirm('当前学习记录尚未保存，是否放弃修改并切换章节？')) return;
  current = course.lessons.find(l => l.id === id) || course.lessons[0]; dirty = false;
  const record = course.records[current.id] || {status:'not_started',resource:'original',page:1,notes:''};
  $('module').textContent = current.module; $('lesson-title').textContent = current.title; $('goal').textContent = current.goal;
  $('resource').replaceChildren(...current.resources.map(r => new Option(r.label, r.id)));
  $('resource').value = record.resource; previousResource = record.resource; $('page').value = record.page;
  if ($('reading-mode').value !== 'pdf' && resource().id !== 'original') {
    const aligned = sourcePage(); $('resource').value = 'original'; previousResource = 'original'; $('page').value = aligned;
  }
  $('study-status').value = record.status; $('notes').value = record.notes;
  $('save-status').textContent = record.updated_at ? `上次保存：${new Date(record.updated_at).toLocaleString()}` : '尚无学习记录';
  notify(''); showPDF(); renderList();
  const url = new URL(location.href); url.searchParams.set('lesson',current.id); url.searchParams.set('course',course.id); history.replaceState(null,'',url);
}
$('search').oninput = () => course && renderList();
for (const button of document.querySelectorAll('[data-filter]')) button.onclick = () => {
  filter = button.dataset.filter;
  for (const b of document.querySelectorAll('[data-filter]')) b.setAttribute('aria-pressed', String(b === button));
  if (course) renderList();
};
let previousResource = 'original';
function sourcePage(selected = resource(), number = Number($('page').value)) {
  if (!selected?.source_page_map) return number;
  const entries = Object.entries(selected.source_page_map).map(([a,b]) => [Number(a),Number(b)]).sort((a,b)=>a[1]-b[1]);
  return entries.filter(([,p])=>p<=number).at(-1)?.[0] || 1;
}
$('resource').onchange = () => {
  const old = current.resources.find(r=>r.id===previousResource);
  const originalPage = sourcePage(old);
  const selected = resource();
  $('page').value = selected.source_page_map?.[originalPage] || Math.min(originalPage,selected.pages);
  previousResource = selected.id;
  $('reading-mode').value = 'pdf';
  markDirty(); showPDF();
};
$('page').oninput = markDirty; $('study-status').onchange = markDirty; $('notes').oninput = markDirty;
$('jump').onclick = () => { try { showPDF(); notify(''); } catch(e) { notify(e.message); } };
$('save').onclick = async () => {
  if (!current || saving || switching) return;
  try {
    const payload = {status:$('study-status').value, resource:$('resource').value, page:validPage(), notes:$('notes').value};
    saving = true; $('save').disabled = true;
    for (const id of ['resource','page','study-status','notes','reading-mode','refresh-translation']) $(id).disabled = true;
    const record = await api(`records/${current.id}?course=${encodeURIComponent(course.id)}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    course.records[current.id] = record; dirty = false; renderList(); notify('');
    $('save-status').textContent = `已保存 · ${new Date(record.updated_at).toLocaleTimeString()}`;
  } catch(e) { notify(`保存未完成：${e.message}。笔记仍保留在当前页面。`); }
  finally { saving = false; $('save').disabled = false; for (const id of ['resource','page','study-status','notes','reading-mode','refresh-translation']) $(id).disabled = false; }
};
window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

function latestLesson() {
  return Object.entries(course.records).sort((a,b) => b[1].updated_at.localeCompare(a[1].updated_at))[0]?.[0] || course.lessons[0]?.id;
}
async function loadCourse(id, lessonId) {
  if (saving || switching) return;
  if (dirty && !confirm('当前学习记录尚未保存，是否放弃修改并切换课程？')) {
    $('course-select').value = course.id; return;
  }
  switching = true; $('course-select').disabled = true;
  for (const id of ['resource','page','study-status','notes','reading-mode','refresh-translation','save']) $(id).disabled = true;
  try {
    const loaded = await api(`catalog?course=${encodeURIComponent(id)}`);
    if (!loaded.lessons.length) throw Error('该课程暂时没有本地讲义');
    course = loaded; dirty = false;
    $('course-select').value = course.id;
    $('course-title').textContent = course.title;
    $('course-description').textContent = course.description;
    $('course-label').textContent = course.english_title || 'HKU DATA8010 & ELEC8111';
    $('course-meta').textContent = `${course.instructor} · ${course.term} · ${course.lessons.length} 个学习单元`;
    $('availability').textContent = course.availability; $('course-source').href = course.source;
    $('export-records').href = new URL(`api/learning/export?course=${encodeURIComponent(course.id)}`,document.baseURI).href;
    $('continue').textContent = Object.keys(course.records).length ? '继续上次学习 →' : '开始学习 →';
    $('continue').onclick = () => choose(latestLesson());
    $('search').value = ''; filter = 'all';
    for (const b of document.querySelectorAll('[data-filter]')) b.setAttribute('aria-pressed',String(b.dataset.filter === 'all'));
    switching = false;
    choose(lessonId || latestLesson());
  } catch(e) {
    notify(`课程加载失败：${e.message}。请重试。`);
    if (course) $('course-select').value = course.id;
  } finally {
    switching = false; $('course-select').disabled = false;
    for (const id of ['resource','page','study-status','notes','reading-mode','refresh-translation','save']) $(id).disabled = false;
  }
}
$('course-select').onchange = () => loadCourse($('course-select').value);
$('global-search').oninput = () => {
  const query = $('global-search').value.trim().toLowerCase();
  const container = $('global-results');container.replaceChildren(); container.hidden = !query;
  if (!query || !library) return;
  let count = 0;
  for (const item of library.courses) for (const lesson of item.lessons) {
    if (!`${item.title} ${item.instructor} ${item.english_title} ${lesson.title} ${lesson.module} ${lesson.goal}`.toLowerCase().includes(query)) continue;
    const button = document.createElement('button');button.textContent = `${item.instructor} / ${lesson.title}`;
    button.onclick = () => loadCourse(item.id,lesson.id);container.append(button);count++;
  }
  if (!count) container.textContent = '没有找到本地讲义，可在下方其他课程入口中继续查找。';
};
(async () => {
  try {
    library = await api('courses');
    $('course-select').replaceChildren(...library.courses.map(c => new Option(`${c.instructor} · ${c.title}`,c.id)));
    const count = library.courses.reduce((sum,c) => sum+c.resource_count,0);
    const units = library.courses.reduce((sum,c)=>sum+c.lessons.length,0);
    $('library-count').textContent = `${library.courses.length} 门课程 · ${units} 个学习单元 · ${count} 份 PDF（含中英文版本）`;
    for (const reference of library.references) {
      const item = document.createElement('div');item.className = 'reference-item';
      const a = document.createElement('a');a.href = reference.source;a.target = '_blank';a.rel = 'noopener';a.textContent = reference.title+' ↗';
      const state = document.createElement('strong');state.textContent = reference.status;
      const text = document.createElement('p');text.textContent = reference.description;
      item.append(a,document.createElement('br'),state,text);$('references').append(item);
    }
    const params = new URLSearchParams(location.search);
    const requested = params.get('course');
    await loadCourse(library.courses.some(c=>c.id===requested)?requested:'hku-data8010',params.get('lesson'));
  } catch(e) { notify(`课程资料库加载失败：${e.message}。请刷新重试。`); }
})();

let studyVersion = 0;
async function showTranslation() {
  const version = ++studyVersion;
  const mode = $('reading-mode').value;
  $('reading-panes').className = mode;
  $('translation-pane').hidden = mode === 'pdf';
  if (mode === 'pdf') return;
  for (const id of ['translation-title','translation-text','translation-summary','translation-annotation']) $(id).textContent = '';
  $('translation-quality').textContent = '正在载入对应原页的中文译稿…';
  try {
    const data = await api(`study/${current.id}?course=${encodeURIComponent(course.id)}&page=${sourcePage()}`);
    if (version !== studyVersion) return;
    $('translation-quality').textContent = `原第 ${data.source_page} 页 · ${data.quality}`;
    if (!data.available) { $('translation-text').textContent = '此页尚未完成。可继续阅读英文原稿，稍后点击“刷新译稿”。'; return; }
    const content = data.content;
    $('translation-title').textContent = content.title;
    $('translation-text').textContent = content.translation;
    $('translation-summary').textContent = content.summary;
    $('translation-annotation').textContent = content.annotation;
  } catch(e) { if (version === studyVersion) $('translation-quality').textContent = '译稿加载失败：'+e.message; }
}
$('reading-mode').onchange = () => {
  if (!current) return;
  if ($('reading-mode').value !== 'pdf' && resource().id !== 'original') {
    const page = sourcePage(); $('resource').value = 'original'; previousResource = 'original';
    $('page').value = page; markDirty();
  }
  showPDF();
};
$('refresh-translation').onclick = async () => {
  if (!current || saving || switching) return;
  const courseId = course.id, lessonId = current.id;
  try {
    const updated = await api(`catalog?course=${encodeURIComponent(courseId)}`);
    if (course.id !== courseId || current.id !== lessonId) return;
    const selected = $('resource').value;
    current.resources = updated.lessons.find(l=>l.id===lessonId).resources;
    $('resource').replaceChildren(...current.resources.map(r=>new Option(r.label,r.id)));
    $('resource').value = selected;
    showPDF(); updateTranslationProgress();
  } catch(e) { notify(e.message); }
};
async function updateTranslationProgress() {
  try {
    const state = await api('translation-status');
    const label = {running:'正在制作',completed:'制作完成',failed:'制作暂停，已有译稿仍可阅读',not_started:'尚未开始'}[state.state] || state.state;
    if (state.state === 'completed') {
      $('translation-progress').textContent = '已收录讲义的中文学习版已全部生成，包含逐页译文、学习总结和批注。可切换中英文对照，或下载中文 PDF。';
      return;
    }
    $('translation-progress').textContent = state.total_documents ? `中文学习版：${label} · 新增 ${state.completed_documents}/${state.total_documents} 份 PDF · ${state.completed_pages}/${state.total_source_pages} 页译稿。港大第 1 讲已有中文版本。${state.state==='running'?(state.source_page?' 当前：'+state.title+'，原第 '+state.source_page+' 页。':' 正在准备讲义…'):''}` : `中文学习版：${label}`;
  } catch (_) { $('translation-progress').textContent = '暂时无法获取翻译进度，已有资料仍可阅读。'; }
}
updateTranslationProgress();
setInterval(updateTranslationProgress,20000);
