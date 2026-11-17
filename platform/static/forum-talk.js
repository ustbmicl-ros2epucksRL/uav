import * as pdfjs from './vendor/pdfjs/pdf.mjs';
pdfjs.GlobalWorkerOptions.workerSrc = new URL('./vendor/pdfjs/pdf.worker.mjs', import.meta.url).href;
const $ = id => document.getElementById(id);
let doc, metadata, current = 1, renderId = 0, pendingTask;
const asset = name => new URL(`./talks/${name}`, import.meta.url).href;
function pageNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(1, Math.min(doc.numPages, Math.trunc(n))) : current;
}
function sync() {
  $('page').value = current;
  $('catalog').value = current;
  $('prev').disabled = current === 1;
  $('next').disabled = current === doc.numPages;
  $('notes-content').textContent = metadata.pages[current - 1]?.notes || '本页暂无讲稿。';
  const url = new URL(location.href); url.hash = `page=${current}`;
  history.replaceState(null, '', url);
}
async function render(value = current) {
  if (!doc) return;
  current = pageNumber(value); sync();
  const id = ++renderId;
  pendingTask?.cancel();
  $('status').textContent = '正在显示…';
  try {
    const page = await doc.getPage(current);
    if (id !== renderId) return;
    const area = $('stage').getBoundingClientRect(), base = page.getViewport({scale:1});
    const scale = Math.min(area.width / base.width, area.height / base.height);
    const viewport = page.getViewport({scale}), ratio = Math.min(devicePixelRatio || 1, 2);
    const canvas = document.createElement('canvas');
    canvas.width = Math.ceil(viewport.width * ratio); canvas.height = Math.ceil(viewport.height * ratio);
    canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`;
    canvas.setAttribute('role', 'img');canvas.setAttribute('aria-label', `第 ${current} 页：${metadata.pages[current - 1]?.title || ''}`);
    pendingTask = page.render({canvasContext:canvas.getContext('2d'),viewport,transform:[ratio,0,0,ratio,0,0]});
    await pendingTask.promise;
    if (id !== renderId) return;
    $('canvas-host').replaceChildren(canvas); $('status').textContent = '';
  } catch (error) {
    if (id !== renderId || error.name === 'RenderingCancelledException') return;
    $('status').textContent = '本页加载失败，请重新翻页，或下载 PDF。';
    console.error(error);
  }
}
async function fullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else {
      $('notes').hidden = true; $('notes-toggle').setAttribute('aria-expanded', 'false');
      await $('player').requestFullscreen();
      $('stage').focus();
    }
  } catch { $('status').textContent = '浏览器暂不支持全屏，可使用浏览器全屏或下载 PDF。'; }
}
$('prev').onclick = () => render(current - 1);
$('next').onclick = () => render(current + 1);
$('page').onchange = event => render(event.target.value);
$('catalog').onchange = event => render(event.target.value);
$('fullscreen').onclick = fullscreen;
$('notes-toggle').onclick = () => {
  $('notes').hidden = !$('notes').hidden;
  $('notes-toggle').setAttribute('aria-expanded', String(!$('notes').hidden));
};
document.addEventListener('keydown', event => {
  if (!doc || event.altKey || event.ctrlKey || event.metaKey || event.target.closest('input,select,textarea')) return;
  if (event.key === ' ' && event.target.closest('button,a')) return;
  let target;
  if (['ArrowRight','ArrowDown','PageDown',' '].includes(event.key)) target = current + 1;
  if (['ArrowLeft','ArrowUp','PageUp'].includes(event.key)) target = current - 1;
  if (event.key === 'Home') target = 1;
  if (event.key === 'End') target = doc.numPages;
  if (target !== undefined) {event.preventDefault();render(target);}
  if (event.key.toLowerCase() === 'f') {event.preventDefault();fullscreen();}
});
let resizeTimer;
new ResizeObserver(() => {clearTimeout(resizeTimer);resizeTimer = setTimeout(() => render(), 100);}).observe($('stage'));
document.addEventListener('fullscreenchange', () => {$('fullscreen').textContent = document.fullscreenElement ? '退出全屏' : '全屏演讲';});
try {
  const response = await fetch(asset('embodied-2026.json'), {cache:'no-cache'});
  if (!response.ok) throw Error('讲稿索引无法加载');
  metadata = await response.json();
  $('version').textContent = metadata.version;
  $('download').href = asset(metadata.pdf);
  doc = await pdfjs.getDocument({url:asset(metadata.pdf),cMapUrl:new URL('./vendor/pdfjs/cmaps/',import.meta.url).href,cMapPacked:true,standardFontDataUrl:new URL('./vendor/pdfjs/standard_fonts/',import.meta.url).href,wasmUrl:new URL('./vendor/pdfjs/wasm/',import.meta.url).href}).promise;
  $('total').textContent = doc.numPages; $('page').max = doc.numPages;
  metadata.pages.slice(0, doc.numPages).forEach(p => {$('catalog').add(new Option(`${p.page}. ${p.title}`, p.page));});
  await render(new URLSearchParams(location.hash.slice(1)).get('page') || 1);
} catch(error) {$('status').textContent = '演讲文件加载失败，请刷新重试或下载 PDF。';console.error(error);}
