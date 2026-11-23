(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const origin = location.origin;
    const platforms = new Map([
        ['station', {label:'区域地面站', path:'/station'}],
        ['planning', {label:'感知网络规划', path:'/network/'}],
        ['service', {label:'低空服务管理', path:'/service/'}],
    ]);
    const skillNames = new Map([
        ['scene_snapshot', '场景与设备快照'],
        ['diagnose_coverage', '覆盖诊断'],
        ['planning_records', '规划任务记录'],
        ['assessment_results', '历史验算结果'],
        ['twin_experiment', '数字孪生对照'],
        ['proposal_kpi', '申报指标核对'],
        ['proposal_search', '申请书原文检索'],
        ['flight_algorithm_runs', '在研：航路算法实验'],
        ['flight_compute', '在研：云边端执行'],
        ['flight_records', '在研：飞行档案'],
        ['service_tasks', '在研：低空服务任务'],
    ]);
    const frames = new Map();
    const navMedia = matchMedia('(max-width:820px)');
    const stage = $('business-stage');
    const chat = $('chat-frame');
    let active, stationReady = false, chatReady = false, pendingAlgorithm, pendingSkill;
    let pendingSegment;

    function notify(message) {
        $('shell-status').textContent = message;
    }
    function setNav(open, returnFocus = false) {
        const visible = open && navMedia.matches;
        $('workspace').classList.toggle('nav-open', visible);
        $('nav-toggle').setAttribute('aria-expanded', String(visible));
        $('nav-scrim').hidden = !visible;
        $('main-workspace').inert = visible;
        if (visible) $('platform-list').querySelector('[aria-current]').focus();
        else if (returnFocus && navMedia.matches) $('nav-toggle').focus();
    }
    function setChat(open) {
        $('chat-dock').classList.toggle('collapsed', !open);
        $('chat-toggle').setAttribute('aria-expanded', String(open));
        $('chat-toggle-label').textContent = open ? '收起' : '展开';
    }
    function sendChatContext() {
        if (!chatReady) return;
        const context = {type:'workbench-context', workspace:active};
        if (active === 'planning') {
            try {
                const scene = frames.get('planning')?.contentDocument?.querySelector('select#scene')?.value;
                if (['urban','corridor','electromagnetic'].includes(scene)) context.scene = scene;
            } catch { /* Pages outside the current origin provide no planning context. */ }
        }
        chat.contentWindow.postMessage(context, origin);
    }
    function sendSkill() {
        if (!chatReady || !pendingSkill) return;
        chat.contentWindow.postMessage({type:'workbench-skill', skill:pendingSkill.name, question:pendingSkill.question, workflow:'platform'}, origin);
        pendingSkill = null;
    }
    function sendSegment() {
        if (!chatReady || !pendingSegment) return;
        chat.contentWindow.postMessage({type:'workbench-twin-segment', ...pendingSegment}, origin);
        pendingSegment = null;
    }
    function validSegment(id, start, end) {
        return typeof id === 'string' && /^[a-f0-9]{32}$/.test(id) &&
            Number.isSafeInteger(start) && start >= 0 && Number.isSafeInteger(end) && end >= start;
    }
    function openSegment(id, start, end, action, addHistory = true) {
        if (!validSegment(id, start, end) || !['assistant','station','planning'].includes(action)) return;
        const station = action === 'station';
        const key = station ? 'station' : 'planning';
        const target = new URL(station ? '/station' : '/network/twin', origin);
        target.searchParams.set(station ? 'twin_experiment' : 'experiment', id);
        target.searchParams.set('start', start);
        target.searchParams.set('end', end);
        // Keep the planning frame and its frozen result when simply opening its evidence in Chat.
        if (action !== 'assistant' || !active) switchPlatform(key, target, false);
        if (action === 'assistant') {
            pendingSegment = {experimentId:id, start, end};
            setChat(true);
            if (isFullscreen()) leaveFullscreen();
            sendSegment();
        }
        if (addHistory) {
            const url = new URL(location.href);
            url.searchParams.set('platform', active);
            url.searchParams.set('experiment', id);
            url.searchParams.set('start', start);
            url.searchParams.set('end', end);
            if (action === 'assistant') url.searchParams.set('assistant','1');
            else url.searchParams.delete('assistant');
            if (url.href !== location.href) history.pushState(null,'',url);
        }
    }
    function sendAlgorithm() {
        if (!stationReady || !pendingAlgorithm || active !== 'station') return;
        frames.get('station').contentWindow.postMessage({type:'workbench-algorithm', algorithm:pendingAlgorithm}, origin);
        pendingAlgorithm = null;
    }
    function embeddedUrl(key, target) {
        if (!platforms.has(key)) return null;
        let url;
        try { url = new URL(target || platforms.get(key).path, location.href); }
        catch { return null; }
        if (url.origin !== origin || url.username || url.password) return null;
        if (key === 'station') {
            if (!['/', '/station', '/station/'].includes(url.pathname)) return null;
            url.pathname = '/station';
        } else {
            const prefix = platforms.get(key).path;
            if (url.pathname === prefix.slice(0,-1)) url.pathname = prefix;
            if (!url.pathname.startsWith(prefix)) return null;
        }
        url.searchParams.set('workbench', '1');
        return url;
    }
    function currentFrameUrl(frame) {
        try { return frame.contentWindow.location.href; } catch { return ''; }
    }
    function navigateInside(key, url) {
        if (!embeddedUrl(key,url)) return false;
        switchPlatform(key, url, true);
        return true;
    }
    function bindFrame(frame) {
        window.bindWorkspaceBusinessLinks?.(frame, (workspace, url) => {
            if (frames.get(active) !== frame) return false;
            return navigateInside(workspace, url);
        });
        frame.addEventListener('load', () => {
            // Keyboard events inside an iframe do not bubble to the workbench.
            try {
                const doc = frame.contentDocument;
                if (frame.dataset.frame === 'planning') {
                    if (active === 'planning') sendChatContext();
                    const scene = doc?.querySelector('select#scene');
                    if (scene && !scene.options.length) {
                        // Planning populates this control asynchronously after the document loads.
                        const optionsReady = new MutationObserver(() => {
                            if (!scene.options.length) return;
                            optionsReady.disconnect();
                            if (active === 'planning') sendChatContext();
                        });
                        optionsReady.observe(scene, {childList:true});
                    }
                    doc?.addEventListener('change', event => {
                        if (active === 'planning' && event.target.matches?.('select#scene')) sendChatContext();
                    });
                }
                doc?.addEventListener('keydown', event => {
                    if (event.key === 'Escape' && isFullscreen()) {
                        event.preventDefault(); leaveFullscreen();
                    }
                }, true);
            } catch { /* An external child document retains its own keyboard controls. */ }
        });
    }
    function switchPlatform(key, target, addHistory = true) {
        const url = embeddedUrl(key, target);
        if (!url) return;
        const changed = active !== key;
        active = key;
        if (changed && key !== 'station') pendingAlgorithm = null;
        let frame = frames.get(key);
        if (!frame) {
            frame = document.createElement('iframe');
            frame.title = platforms.get(key).label + '业务界面';
            frame.dataset.frame = key;
            frame.allowFullscreen = true;
            frames.set(key, frame);
            bindFrame(frame);
            frame.src = url.href;
            $('business-frames').append(frame);
        } else if (target && currentFrameUrl(frame) !== url.href) {
            if (key === 'station') stationReady = false;
            frame.src = url.href;
        }
        for (const [other, child] of frames) child.hidden = other !== key;
        for (const button of $('platform-list').querySelectorAll('[data-platform]')) {
            if (button.dataset.platform === key) button.setAttribute('aria-current', 'page');
            else button.removeAttribute('aria-current');
        }
        $('stage-title').textContent = platforms.get(key).label;
        if (addHistory && changed) {
            const hostUrl = new URL(location.href);
            for (const field of ['experiment','start','end','assistant']) hostUrl.searchParams.delete(field);
            if (key === 'station') hostUrl.searchParams.delete('platform');
            else hostUrl.searchParams.set('platform',key);
            history.pushState(null,'',hostUrl);
        }
        setNav(false);
        sendChatContext();
    }
    function selectAlgorithm(name) {
        pendingAlgorithm = name;
        const frame = frames.get('station');
        const current = frame && currentFrameUrl(frame);
        const onStationPage = current && new URL(current).pathname === '/station' && !new URL(current).searchParams.has('twin_experiment');
        switchPlatform('station', onStationPage ? null : '/station');
        sendAlgorithm();
    }
    function selectSkill(name) {
        const title = skillNames.get(name) || name;
        pendingSkill = {
            name,
            question:'请使用 ' + name + ' 核对当前场景的「' + title + '」，并引用本次工具证据。',
        };
        setNav(false);
        setChat(true);
        sendChatContext();
        sendSkill();
        notify('已选择「' + title + '」，请在 Chat 中确认问题。');
    }
    async function loadSkills() {
        const list = $('skill-list');
        list.setAttribute('aria-busy','true');
        try {
            const response = await fetch('/network/api/research-agent/tools');
            if (!response.ok) throw Error(String(response.status));
            const {tools} = await response.json();
            if (!Array.isArray(tools)) throw Error('invalid catalog');
            const buttons = [];
            for (const tool of tools) {
                if (tool.effects !== 'read_only' || typeof tool.name !== 'string') continue;
                const button = document.createElement('button');
                button.type = 'button';
                button.dataset.skill = tool.name;
                const title = skillNames.get(tool.name) || tool.name;
                button.title = tool.description || title;
                const icon = document.createElement('span');
                icon.className = 'library-icon skill-icon';
                icon.setAttribute('aria-hidden','true');
                button.append(icon, document.createTextNode(title));
                button.addEventListener('click', () => selectSkill(tool.name));
                buttons.push(button);
            }
            list.replaceChildren(...buttons);
            if (!buttons.length) list.textContent = '暂无可用技能';
        } catch {
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.textContent = '技能未加载 · 重试';
            retry.addEventListener('click', loadSkills, {once:true});
            list.replaceChildren(retry);
        } finally { list.setAttribute('aria-busy','false'); }
    }

    window.addEventListener('message', event => {
        if (event.origin !== origin || !event.data || typeof event.data !== 'object') return;
        const m = event.data;
        if (event.source === chat.contentWindow) {
            if (m.type === 'platform-assistant-ready') {
                chatReady = true;
                sendChatContext(); sendSkill(); sendSegment();
            } else if (m.type === 'platform-assistant-close') {
                setChat(false); $('chat-toggle').focus();
            } else if (m.type === 'workbench-clear-twin-segment') {
                pendingSegment = null;
                const url = new URL(location.href);
                url.searchParams.delete('assistant');
                history.replaceState(null,'',url);
            }
            return;
        }
        if (m.type === 'workbench-twin-segment' && ['planning','station'].includes(active) &&
                event.source === frames.get(active)?.contentWindow) {
            openSegment(m.experimentId, m.start, m.end, m.action);
            return;
        }
        if (event.source === frames.get('station')?.contentWindow &&
                m.type === 'workbench-business-ready' && m.workspace === 'station') {
            stationReady = true; sendAlgorithm();
        } else if (event.source === frames.get(active)?.contentWindow && m.type === 'workbench-navigate') {
            navigateInside(m.workspace, m.url);
        }
    });
    function isFullscreen() {
        return document.fullscreenElement === stage || stage.classList.contains('full-window');
    }
    function syncFullscreen() {
        const full = isFullscreen();
        $('stage-fullscreen').setAttribute('aria-pressed',String(full));
        $('fullscreen-label').textContent = full ? '还原' : '全屏';
    }
    async function leaveFullscreen() {
        if (document.fullscreenElement === stage) {
            try { await document.exitFullscreen(); }
            catch { /* The browser may have already handled Escape. */ }
        }
        stage.classList.remove('full-window'); syncFullscreen();
        $('stage-fullscreen').focus();
    }
    async function toggleFullscreen() {
        if (isFullscreen()) return leaveFullscreen();
        try {
            if (!stage.requestFullscreen) throw Error('fullscreen unavailable');
            await stage.requestFullscreen();
        } catch { stage.classList.add('full-window'); }
        syncFullscreen();
    }
    document.addEventListener('fullscreenchange', syncFullscreen);
    $('stage-fullscreen').addEventListener('click', toggleFullscreen);
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        if (isFullscreen()) leaveFullscreen();
        else if ($('workspace').classList.contains('nav-open')) setNav(false, true);
    });
    $('nav-toggle').addEventListener('click', () => setNav(true));
    $('nav-close').addEventListener('click', () => setNav(false, true));
    $('nav-scrim').addEventListener('click', () => setNav(false, true));
    navMedia.addEventListener('change', () => setNav(false));
    $('chat-toggle').addEventListener('click', () => setChat($('chat-dock').classList.contains('collapsed')));

    const grip = $('chat-resize');
    const main = $('main-workspace');
    function limits() {
        const max = Math.max(65, Math.floor(main.clientHeight * .65));
        return {min:Math.min(140, max), max};
    }
    function sizeDock(value) {
        const {min,max} = limits();
        const height = Math.max(min, Math.min(max, value));
        main.style.setProperty('--dock-height', Math.round(height) + 'px');
        syncGrip();
    }
    function syncGrip() {
        const {min,max} = limits();
        grip.setAttribute('aria-valuemin', String(min)); grip.setAttribute('aria-valuemax', String(max));
        const height = Math.round($('chat-dock').getBoundingClientRect().height);
        grip.setAttribute('aria-valuenow', String(Math.max(min,Math.min(height,max))));
        grip.setAttribute('aria-valuetext', height + ' 像素');
    }
    grip.addEventListener('pointerdown', e => {
        if (e.button !== 0) return;
        grip.setPointerCapture(e.pointerId); $('workspace').classList.add('resizing');
        e.preventDefault();
    });
    grip.addEventListener('pointermove', e => {
        if (grip.hasPointerCapture(e.pointerId)) sizeDock(main.getBoundingClientRect().bottom - e.clientY);
    });
    grip.addEventListener('lostpointercapture', () => $('workspace').classList.remove('resizing'));
    grip.addEventListener('keydown', e => {
        const height = $('chat-dock').getBoundingClientRect().height;
        const {min,max} = limits();
        const values = {ArrowUp:height+28, ArrowDown:height-28, Home:min, End:max};
        if (Object.hasOwn(values,e.key)) { e.preventDefault(); sizeDock(values[e.key]); }
    });
    new ResizeObserver(syncGrip).observe($('chat-dock'));
    for (const button of $('platform-list').querySelectorAll('[data-platform]')) {
        button.addEventListener('click', () => switchPlatform(button.dataset.platform));
    }
    for (const button of $('algorithm-list').querySelectorAll('[data-algorithm]')) {
        button.addEventListener('click', () => selectAlgorithm(button.dataset.algorithm));
    }
    function platformFromUrl() {
        const requested = new URLSearchParams(location.search).get('platform');
        return platforms.has(requested) ? requested : 'station';
    }
    function restoreLocation() {
        const params = new URLSearchParams(location.search), key = platformFromUrl();
        const id = params.get('experiment'), left = params.get('start'), right = params.get('end');
        if (['planning','station'].includes(key) && /^\d+$/.test(left) && /^\d+$/.test(right) &&
                validSegment(id, Number(left), Number(right))) {
            openSegment(id, Number(left), Number(right), key, false);
            if (params.get('assistant') === '1') openSegment(id, Number(left), Number(right), 'assistant', false);
        } else {
            const url = frames.get(key) && currentFrameUrl(frames.get(key));
            const inReplay = url && (new URL(url).searchParams.has('twin_experiment') || new URL(url).searchParams.has('experiment'));
            switchPlatform(key, inReplay ? platforms.get(key).path : null, false);
        }
    }
    window.addEventListener('popstate', restoreLocation);
    restoreLocation();
    chat.src = '/network/agents?embed=1&layout=dock&workspace=' + active;
    loadSkills();
})();
