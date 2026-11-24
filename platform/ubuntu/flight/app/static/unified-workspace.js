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
        ['research_framework', '研究体系与四维可行性'],
        ['proposal_kpi', '申报考核指标口径核对'],
        ['proposal_search', '申请书条款与页码检索'],
        ['diagnose_coverage', '三维覆盖与薄弱区诊断'],
        ['twin_experiment', '数字孪生五方案反事实对照'],
        ['assessment_results', '细网格（10m/2m）规划验算'],
        ['scene_snapshot', '场景站址与异构设备快照'],
        ['planning_records', '按需规划任务档案核查'],
        ['flight_algorithm_runs', '避障航路与 HEFT 算力调度'],
        ['flight_compute', '云边端计算批次与稳定性'],
        ['flight_records', 'SITL 仿真飞行档案核查'],
        ['service_tasks', '低空服务任务与审批闭环'],
    ]);
    const theorySkillIds = new Set(['research_framework', 'proposal_kpi', 'proposal_search']);
    const planningAlgoIds = new Set(['diagnose_coverage', 'twin_experiment', 'assessment_results', 'scene_snapshot', 'planning_records']);
    const fallbackTheoryMechanisms = [
        {id:'theory-task-1', task_id:1, workflow:'research', title:'子项目一 · 遮挡与网格收敛机理',
            description:'三维几何视线遮挡、共视相关性 ρ 与 10m/2m 混合网格收敛算例',
            question:'请针对子项目一（三维覆盖），选择并运行视线遮挡、传感器相关性或 10m/2m 混合网格算例，定量说明模型假设边界。'},
        {id:'theory-task-2', task_id:2, workflow:'research', title:'子项目二 · 干扰与协同精度机理',
            description:'双站观测夹角 GDOP、节点间共址干扰方差与共同系统偏差算例',
            question:'请针对子项目二（干扰与协同精度），运行观测几何夹角、节点间干扰或系统偏差算例，说明何时多站协同精度退化及理论界偏离实际误差。'},
        {id:'theory-task-3', task_id:3, workflow:'research', title:'子项目三 · 约束优化最优性机理',
            description:'贪心次优反例 vs 穷举最优、单站挂载容量约束与代理乐观偏差算例',
            question:'请针对子项目三（按需规划），运行预算下贪心与穷举对照、单站容量约束或代理偏差复核算例，论证多约束联合优化与参考模型复核的必要性。'},
        {id:'theory-task-4', task_id:4, workflow:'research', title:'子项目四 · 跨站关联与时延机理',
            description:'乱序晚到报文、遮挡期间运动关联模糊与异常观测稳健滤波算例',
            question:'请针对子项目四（运行平台与关联跟踪），运行样本延迟乱序、遮挡运动配对或异常值裁剪滤波算例，说明在线状态更新的适用边界。'},
        {id:'theory-task-5', task_id:5, workflow:'research', title:'子项目五 · 残差校准与混杂检验',
            description:'留出集域偏移校准、跨场次混杂难度与可复算证据链完整性算例',
            question:'请针对子项目五（示范验证），运行留出集偏差校准、混杂条件对照或证据链完整性审计算例，说明如何避免过拟合与伪改进结论。'},
    ];
    const platformSkillPrompts = new Map([
        ['research_framework', '请使用 research_framework 梳理本项目研究的问题、五项研究内容的输入输出关系、重要性、数字孪生必要性与四维可行性，并明确合成证据边界。'],
        ['proposal_kpi', '请使用 proposal_kpi 核对正式申报书考核指标阈值及早期稿对照口径，说明达标判定规则。'],
        ['proposal_search', '请使用 proposal_search 在正式申请书原文中检索“典型场景组网验证”和“多模态感知设备”相关条款并标注 PDF 页码。'],
        ['diagnose_coverage', '请使用 diagnose_coverage 对当前冻结场景执行三维覆盖诊断，指出覆盖率与薄弱空域并说明网格口径。'],
        ['twin_experiment', '请使用 twin_experiment 对比当前场景数字孪生中的现有部署、完整规划方案与三项反事实方案，说明各子项目必要性。'],
        ['assessment_results', '请使用 assessment_results 核查当前场景最近的 10m/2m 细网格规划验算结果、预算费用与定位精度瓶颈。'],
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
        if (visible) {
            if ($('group-platforms')) $('group-platforms').open = true;
            $('platform-list').querySelector('[aria-current]')?.focus();
        } else if (returnFocus && navMedia.matches) {
            $('nav-toggle').focus();
        }
    }
    function setChat(open) {
        $('chat-dock').classList.toggle('collapsed', !open);
        $('chat-toggle').setAttribute('aria-expanded', String(open));
        $('chat-toggle-label').textContent = open ? '收起' : '展开';
    }
    function syncRailGroups(key, pathname) {
        const groupPlatforms = $('group-platforms');
        const groupProposal = $('group-proposal');
        const badgePlatforms = $('badge-platforms');
        const badgeProposal = $('badge-proposal');
        const stationDrawer = $('station-algorithms');
        if (badgePlatforms) badgePlatforms.textContent = platforms.get(key)?.label || '';
        let activeSubviewLabel = '6 项视图';
        const planningViews = $('planning-views');
        if (planningViews) {
            for (const button of planningViews.querySelectorAll('[data-planning-path]')) {
                const match = key === 'planning' && pathname === button.dataset.planningPath;
                button.classList.toggle('active-subview', match);
                if (match) activeSubviewLabel = button.dataset.shortLabel || button.textContent.trim();
            }
        }
        if (badgeProposal) badgeProposal.textContent = key === 'planning' ? activeSubviewLabel : '感知规划';
        if (groupPlatforms) {
            groupPlatforms.classList.toggle('is-active-group', key !== 'planning');
            if (key !== 'planning') groupPlatforms.open = true;
        }
        if (groupProposal) {
            groupProposal.classList.toggle('is-active-group', key === 'planning');
            if (key === 'planning') groupProposal.open = true;
        }
        if (stationDrawer && key !== 'station') stationDrawer.open = false;
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
        const message = {type:'workbench-skill', workflow:pendingSkill.workflow, question:pendingSkill.question};
        if (pendingSkill.skill) message.skill = pendingSkill.skill;
        if (pendingSkill.taskId !== undefined) message.taskId = pendingSkill.taskId;
        chat.contentWindow.postMessage(message, origin);
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
                    if (active === 'planning') {
                        sendChatContext();
                        const loadedUrl = currentFrameUrl(frame);
                        if (loadedUrl) syncRailGroups('planning', new URL(loadedUrl).pathname);
                    }
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
        syncRailGroups(key, url.pathname);
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
        if ($('station-algorithms')) $('station-algorithms').open = true;
        sendAlgorithm();
    }
    function selectPlatformSkill(name) {
        const title = skillNames.get(name) || name;
        pendingSkill = {
            workflow: 'platform',
            skill: name,
            question: platformSkillPrompts.get(name) || ('请使用 ' + name + ' 核对当前场景的「' + title + '」，并引用本次工具证据。'),
        };
        setNav(false);
        setChat(true);
        sendChatContext();
        sendSkill();
        notify('已选择平台技能「' + title + '」，请在 Chat 中确认问题。');
    }
    function selectTheoryMechanismSkill(spec) {
        pendingSkill = {
            workflow: 'research',
            taskId: spec.task_id,
            question: spec.question,
        };
        setNav(false);
        setChat(true);
        sendChatContext();
        sendSkill();
        notify('已选择理论机理技能「' + spec.title + '」，请在 Chat 中确认问题。');
    }
    function makeSubgroupHeading(text) {
        const heading = document.createElement('p');
        heading.className = 'skill-subgroup-label';
        heading.textContent = text;
        return heading;
    }
    function makeSkillButton({key, title, description, iconKind, onClick, attrName = 'skill'}) {
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset[attrName] = key;
        button.title = description || title;
        const icon = document.createElement('span');
        icon.className = iconKind === 'theory' ? 'library-icon theory-icon' : 'library-icon skill-icon';
        icon.setAttribute('aria-hidden', 'true');
        if (iconKind === 'theory') icon.textContent = '∑';
        button.append(icon, document.createTextNode(title));
        button.addEventListener('click', onClick);
        return button;
    }
    async function loadSkills() {
        const theoryList = $('theory-skill-list');
        const algoList = $('skill-list');
        theoryList?.setAttribute('aria-busy', 'true');
        algoList.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch('/network/api/research-agent/tools');
            if (!response.ok) throw Error(String(response.status));
            const payload = await response.json();
            const tools = payload?.tools;
            if (!Array.isArray(tools)) throw Error('invalid catalog');
            const mechanisms = Array.isArray(payload.theory_mechanisms) && payload.theory_mechanisms.length
                ? payload.theory_mechanisms
                : fallbackTheoryMechanisms;

            const theoryDocButtons = [];
            const planningAlgoButtons = [];
            const collabAlgoButtons = [];
            for (const tool of tools) {
                if (tool.effects !== 'read_only' || typeof tool.name !== 'string') continue;
                const title = skillNames.get(tool.name) || tool.name;
                const category = tool.category || (theorySkillIds.has(tool.name) ? 'theory' : planningAlgoIds.has(tool.name) ? 'platform_planning' : 'platform_collab');
                const btn = makeSkillButton({
                    key: tool.name,
                    title,
                    description: tool.description || title,
                    iconKind: category === 'theory' ? 'theory' : 'algo',
                    onClick: () => selectPlatformSkill(tool.name),
                });
                if (category === 'theory') theoryDocButtons.push(btn);
                else if (category === 'platform_planning') planningAlgoButtons.push(btn);
                else collabAlgoButtons.push(btn);
            }

            if (theoryList) {
                const theoryNodes = [];
                if (theoryDocButtons.length) {
                    theoryNodes.push(makeSubgroupHeading('立项体系与申报依据'), ...theoryDocButtons);
                }
                const mechanismButtons = mechanisms
                    .filter(item => Number.isInteger(item.task_id) && item.task_id >= 1 && item.task_id <= 5)
                    .map(item => makeSkillButton({
                        key: item.id || ('theory-task-' + item.task_id),
                        title: item.title,
                        description: item.description,
                        iconKind: 'theory',
                        attrName: 'theorySkill',
                        onClick: () => selectTheoryMechanismSkill(item),
                    }));
                if (mechanismButtons.length) {
                    theoryNodes.push(makeSubgroupHeading('五子项目数学机理算例'), ...mechanismButtons);
                }
                theoryList.replaceChildren(...theoryNodes);
                if (!theoryNodes.length) theoryList.textContent = '暂无理论技能';
                const badgeTheory = $('badge-theory-skills');
                if (badgeTheory) badgeTheory.textContent = `立项与机理 · ${theoryDocButtons.length + mechanismButtons.length}`;
            }

            const algoNodes = [];
            if (planningAlgoButtons.length) {
                algoNodes.push(makeSubgroupHeading('感知网络规划核心算法'), ...planningAlgoButtons);
            }
            if (collabAlgoButtons.length) {
                algoNodes.push(makeSubgroupHeading('航路与云边端协同算法'), ...collabAlgoButtons);
            }
            algoList.replaceChildren(...algoNodes);
            if (!algoNodes.length) algoList.textContent = '暂无可用平台算法技能';
            const badgeAlgo = $('badge-algo-skills');
            if (badgeAlgo) badgeAlgo.textContent = `规划与协同 · ${planningAlgoButtons.length + collabAlgoButtons.length}`;
        } catch {
            for (const target of [theoryList, algoList].filter(Boolean)) {
                const retry = document.createElement('button');
                retry.type = 'button';
                retry.textContent = '技能未加载 · 重试';
                retry.addEventListener('click', loadSkills, {once:true});
                target.replaceChildren(retry);
            }
        } finally {
            theoryList?.setAttribute('aria-busy', 'false');
            algoList.setAttribute('aria-busy', 'false');
        }
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
    for (const button of $('planning-views')?.querySelectorAll('[data-planning-path]') || []) {
        button.addEventListener('click', () => switchPlatform('planning', button.dataset.planningPath));
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
