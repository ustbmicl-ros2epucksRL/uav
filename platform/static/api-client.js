/* Shared HTTP and computation lifecycle, compatible with standalone and /network. */
(function (global) {
  'use strict';
  const root = global.location.pathname.startsWith('/network') ? '/network' : '';
  async function request(path, options = {}) {
    const response = await fetch(path, options);
    let data;
    try { data = await response.json(); }
    catch { throw Error(`服务响应无法解析（HTTP ${response.status}）`); }
    if (!response.ok) {
      const message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data);
      throw Error(`${message}${response.headers?.get('X-Request-ID') ? ` · 请求 ${response.headers.get('X-Request-ID')}` : ''}`);
    }
    return data;
  }
  function post(path, body = {}) {
    return request(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  }
  async function wait(job, onUpdate = () => {}) {
    for (;;) {
      onUpdate(job);
      if (job.status === 'succeeded') return job;
      if (['failed', 'cancelled', 'interrupted'].includes(job.status)) throw Error(job.error || (job.status === 'cancelled' ? '计算已取消' : '计算已中断'));
      await new Promise(resolve => setTimeout(resolve, 700));
      job = await request(`${root}/api/jobs/${job.id}`);
    }
  }
  global.SenseAPI = {root, request, post, wait};
})(window);
