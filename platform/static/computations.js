'use strict';
const api = window.SenseAPI, root = api.root, el = id => document.getElementById(id);
const scenes = {urban:'建筑区',corridor:'物流廊道',electromagnetic:'复杂电磁区'};
const labels = {queued:'排队中',running:'计算中',succeeded:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
let offset = 0, loading = false, lastList = ''; 
async function act(fn) {
  try { await fn(); await refresh(); }
  catch (error) { el('status').textContent = error.message; }
}
function button(text, callback) {
  const node = document.createElement('button'); node.textContent = text;
  node.onclick = async () => { node.disabled = true; try { await act(callback); } finally { node.disabled = false; } };
  return node;
}
async function refresh() {
  if (loading) return;
  loading = true;
  try {
    const data = await api.request(`${root}/api/jobs?limit=20&offset=${offset}${el('filter').value ? '&scene='+encodeURIComponent(el('filter').value) : ''}`);
    const signature = JSON.stringify([offset, el('filter').value, data.jobs]);
    if (signature === lastList) return;
    lastList = signature;
    el('jobs').replaceChildren(...data.jobs.map(job => {
      const row = document.createElement('tr');
      for (const text of [new Date(job.created_at).toLocaleString(), `${scenes[job.scene] || job.scene} / ${{volume:'效能场',plan:'方案比较',assessment:'任务指标验证'}[job.kind] || job.kind}`, labels[job.status] || job.status]) {
        const cell = document.createElement('td'); cell.textContent = text; row.append(cell);
      }
      const actions = document.createElement('td');
      actions.append(button('详情', async () => { el('detail').textContent = JSON.stringify(await api.request(`${root}/api/jobs/${job.id}`), null, 2); }));
      if (['queued','running'].includes(job.status)) actions.append(button('取消', () => api.post(`${root}/api/jobs/${job.id}/cancel`)));
      else actions.append(button('按原输入重跑', async () => { const next = await api.post(`${root}/api/jobs/${job.id}/retry`); el('status').textContent = `已提交重跑：${next.id}`; }));
      if (job.status === 'succeeded') actions.append(button(job.kind === 'assessment' ? '归档为规划方案' : '载入工作台', async () => { await api.post(`${root}/api/jobs/${job.id}/publish`); el('status').textContent = job.kind === 'assessment' ? '已归档到方案管理。' : '已载入工作台，设备配置未更改。'; }));
      const link = document.createElement('a'); link.href = `${root}/api/jobs/${job.id}/export`; link.textContent = '导出'; actions.append(link);
      row.append(actions); return row;
    }));
    el('previous').disabled = offset === 0; el('next').disabled = data.jobs.length < 20;
    el('page').textContent = `第 ${offset / 20 + 1} 页`;
    if (!data.jobs.length) el('status').textContent = '当前没有计算记录，可从规划工作台发起计算。';
  } catch (error) { el('status').textContent = error.message; }
  finally { loading = false; }
}
el('refresh').onclick = refresh;
el('filter').onchange = () => { offset = 0; refresh(); };
el('previous').onclick = () => { offset = Math.max(0, offset - 20); refresh(); };
el('next').onclick = () => { offset += 20; refresh(); };
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 3000);
