'use strict';

(async function openPlanningReplay() {
    const element = id => document.getElementById(id);
    const params = new URLSearchParams(location.search);
    const experimentId = params.get('twin_experiment');
    const panel = element('replay-panel');
    const status = element('replay-status');
    const play = element('replay-play');
    const slider = element('replay-index');
    let timer = null;

    function pause() {
        clearTimeout(timer);
        timer = null;
        play.textContent = '播放';
    }

    try {
        if (!/^[a-f0-9]{32}$/.test(experimentId || '')) throw Error('缺少有效的合成实验编号。');
        const response = await fetch('/network/api/twin-experiments/' + experimentId);
        if (!response.ok) throw Error(response.status === 404 ? '未找到所选实验档案。' : '实验档案读取失败。');
        const record = await response.json();
        const track = record.inputs?.trajectory;
        const points = track?.points;
        const times = track?.times;
        if (record.id !== experimentId || record.evidence_level !== 'synthetic_twin' ||
            !['urban', 'corridor', 'electromagnetic'].includes(record.inputs?.scene?.id)) {
            throw Error('此入口需要已归档的合成数字孪生实验档案。');
        }
        if (!Array.isArray(points) || !points.length || !Array.isArray(times) || times.length !== points.length ||
            points.some(p => !Array.isArray(p) || p.length !== 3 || !p.every(Number.isFinite)) ||
            times.some((t, i) => !Number.isFinite(t) || (i > 0 && t <= times[i - 1]))) {
            throw Error('档案缺少完整参考轨迹。');
        }
        function indexParam(name, fallback) {
            if (!params.has(name)) return fallback;
            const value = params.get(name);
            if (!/^\d+$/.test(value) || Number(value) >= points.length) throw Error('选段超出实验档案的采样范围。');
            return Number(value);
        }
        const start = indexParam('start', 0);
        const end = indexParam('end', points.length - 1);
        if (start > end) throw Error('选段起点晚于终点。');
        const query = new URLSearchParams({experiment:experimentId, start, end});
        element('back-planning').href = '/network/twin?' + query;
        const assistant = element('replay-assistant');
        assistant.href = '/?platform=planning&assistant=1&' + query;
        if (window.parent !== window && window.frameElement?.dataset.frame === 'station') {
            assistant.onclick = event => {
                event.preventDefault();
                window.parent.postMessage({type:'workbench-twin-segment', action:'assistant', experimentId, start, end}, location.origin);
            };
        }

        const deployments = record.result.deployments;
        const rows = deployments.map(deployment => {
            const name = document.createElement('dt');
            name.textContent = deployment.label;
            const sample = document.createElement('dd');
            element('replay-comparison').append(name, sample);
            return {timeline:deployment.timeline, sample};
        });
        panel.hidden = false;
        const map = L.map('replay-map', {crs:L.CRS.Simple, minZoom:-5, maxZoom:3, attributionControl:false});
        for (const building of record.inputs.scene.buildings) {
            L.rectangle([[building.y, building.x], [building.y + building.d, building.x + building.w]], {
                color:'#536a7d', weight:1, fillColor:'#344a5a', fillOpacity:0.85, interactive:false,
            }).addTo(map);
        }
        const path = points.map(([x, y]) => [y, x]);
        const segment = path.slice(start, end + 1);
        L.polyline(path, {color:'#a9b8c6', weight:2, dashArray:'5 6', interactive:false}).addTo(map);
        L.polyline(segment, {color:'#55c9ee', weight:5, interactive:false}).addTo(map);
        const marker = L.circleMarker(path[start], {radius:7, color:'#fff', weight:2, fillColor:'#ffd17c', fillOpacity:1}).addTo(map);
        map.fitBounds(L.latLngBounds(segment), {padding:[25,25], maxZoom:1});
        new ResizeObserver(() => map.invalidateSize()).observe(element('replay-map'));

        status.textContent = `合成孪生 · 只读回放 · ${times[start]}–${times[end]} s，共 ${end - start + 1} 个采样点`;
        element('replay-track').textContent = `${record.inputs.scene.name} · ${track.id}`;
        element('replay-id').textContent = '实验 ' + record.id;
        element('replay-threshold').textContent = `档案定位门槛 ${record.inputs.environment.precision_m} m`;
        slider.min = start;
        slider.max = end;
        slider.disabled = play.disabled = start === end;
        function show(index) {
            const [x, y, z] = points[index];
            slider.value = index;
            marker.setLatLng(path[index]);
            element('replay-time').textContent = times[index] + ' s';
            element('replay-position').textContent = `局部坐标 X ${x} m · Y ${y} m · 高度 ${z} m`;
            for (const {timeline, sample} of rows) {
                const point = timeline?.[index];
                if (!point || point.t !== times[index]) {
                    sample.textContent = '该采样点缺少理论记录';
                    continue;
                }
                const bound = Number.isFinite(point.crlb_m) ? ` · 理论误差界 ${point.crlb_m.toFixed(1)} m` : '';
                sample.textContent = point.cause + bound;
            }
        }
        function advance() {
            const index = Number(slider.value);
            if (index >= end) return pause();
            const delay = (times[index + 1] - times[index]) * 1000;
            timer = setTimeout(() => { show(index + 1); advance(); }, delay);
        }
        slider.oninput = () => { pause(); show(Number(slider.value)); };
        play.onclick = () => {
            if (timer !== null) return pause();
            if (Number(slider.value) === end) show(start);
            play.textContent = '暂停';
            advance();
        };
        window.addEventListener('pagehide', pause);
        document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); });
        show(start);
    } catch (error) {
        panel.hidden = true;
        status.textContent = error.message;
    }
})();
