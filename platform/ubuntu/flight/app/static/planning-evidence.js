'use strict';
(function () {
    const select = document.getElementById('sensing-plan');
    const status = document.getElementById('sensing-link-status');
    let plans = [];
    async function request(path, body) {
        const response = await fetch(path, {method:body === undefined ? 'GET' : 'POST', headers:{'Content-Type':'application/json'}, body:body === undefined ? undefined : JSON.stringify(body)});
        const result = await response.json();
        if (!response.ok) throw Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
        return result;
    }
    function showPlan() {
        const plan = plans.find(item => item.id === select.value);
        document.getElementById('sensing-plan-context').textContent = plan ? `${plan.task_name} · 方案 ${plan.id} · 新增 ${plan.selected.length} 台设备` : '在申请书指标页完成验算并归档方案后，可关联本地仿真航迹。';
        document.getElementById('sensing-assessment-link').href = plan ? `/network/acceptance?task=${encodeURIComponent(plan.task_id)}&assessment=${plan.assessment_job_id}` : '/network/acceptance';
    }
    async function refresh() {
        try {
            const previous = select.value || new URLSearchParams(location.search).get('assessment');
            plans = (await request('/network/api/workflow/plans')).filter(plan => plan.assessment_job_id);
            select.replaceChildren(...plans.map(plan => {
                const option = document.createElement('option');
                option.value = plan.id;
                option.textContent = plan.task_name + ' · ' + plan.id.slice(0,8);
                return option;
            }));
            if (plans.some(plan => plan.id === previous)) select.value = previous;
            showPlan();
        } catch (error) {status.textContent = error.message;}
    }
    select.onchange = showPlan;
    document.getElementById('sensing-plan-refresh').onclick = refresh;
    document.getElementById('sensing-link-trace').onclick = async event => {
        const button = event.currentTarget;
        button.disabled = true;
        try {
            if (window.externalMode || window.dynamicReplay) throw Error('请选择本地仿真模式。外部飞控记录可通过示范数据导入流程关联。');
            const vehicleId = document.getElementById('vehicle-select')?.value || state.selected;
            const plan = plans.find(item => item.id === select.value);
            if (!plan || !vehicleId) throw Error('请选择规划方案和本地仿真飞行器');
            const result = await request(`/api/vehicles/${encodeURIComponent(vehicleId)}/planning-evidence`, {assessment_id:plan.assessment_job_id, note:document.getElementById('sensing-run-note').value});
            status.textContent = `已关联 ${result.track.count} 个仿真航迹点。记录 ${result.id}，可在规划指标页查看和导出。`;
        } catch (error) {status.textContent = error.message;}
        finally {button.disabled = false;}
    };
    refresh();
})();
