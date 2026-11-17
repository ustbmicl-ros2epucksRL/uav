/* Optional assistant shell. Never replaces or controls the host workspace. */
(() => {
    'use strict';
    if (window.top !== window.self || document.getElementById('platform-assistant-host')) return;
    const script = document.currentScript;
    if (!script?.src) return;
    const agentURL = new URL('../agents', script.src);
    agentURL.searchParams.set('embed', '1');
    const host = document.createElement('div');
    host.id = 'platform-assistant-host';
    const shadow = host.attachShadow({mode:'open'});
    const style = document.createElement('style');
    style.textContent = `
        :host{all:initial;font-family:system-ui,-apple-system,"PingFang SC",sans-serif;color:#203344}
        *{box-sizing:border-box} [hidden]{display:none!important}
        button,a{font:inherit}button{cursor:pointer;border:1px solid #b9cbd5;border-radius:8px;padding:9px 13px;background:white;color:#203344}
        button:focus-visible,a:focus-visible{outline:3px solid #57bfc9;outline-offset:3px}
        .launcher{position:fixed;right:20px;bottom:22px;z-index:2100;background:#146d80;color:white;border:0;box-shadow:0 4px 18px #102f4333;padding:12px 18px}
        .panel{position:fixed;right:14px;top:14px;bottom:14px;width:min(450px,calc(100vw - 28px));z-index:2100;display:flex;flex-direction:column;overflow:hidden;border:1px solid #bfd0dc;border-radius:14px;background:#f3f6fa;box-shadow:0 12px 45px #102f4344}
        .heading{display:flex;align-items:center;gap:10px;padding:12px 14px;background:#142e43;color:white}
        .heading strong{font-size:17px;flex:1}.heading a{color:#cde8f0;font-size:12px}.heading button{padding:6px 10px}
        .note{margin:0;padding:9px 14px;font-size:12px;line-height:1.6;color:#4d6677;background:#eaf2f6}
        .load-status{margin:0;padding:8px 14px;font-size:12px;line-height:1.5}
        iframe{width:100%;flex:1;min-height:0;border:0;background:#f3f6fa}
        @media(max-width:600px){.panel{inset:8px;width:calc(100vw - 16px)}.launcher{right:12px;bottom:12px}}
    `;
    const launcher = document.createElement('button');
    launcher.type = 'button'; launcher.className = 'launcher'; launcher.textContent = '✦ 平台助手';
    launcher.setAttribute('aria-expanded', 'false'); launcher.setAttribute('aria-controls', 'assistant-panel');
    const panel = document.createElement('section');
    panel.id = 'assistant-panel'; panel.className = 'panel'; panel.hidden = true;
    panel.setAttribute('role', 'region'); panel.setAttribute('aria-label', '平台智能助手');
    const heading = document.createElement('div'); heading.className = 'heading';
    const title = document.createElement('strong'); title.textContent = '平台助手';
    const full = document.createElement('a'); full.href = new URL('../agents', script.src).href;
    full.target = '_blank'; full.rel = 'noopener'; full.textContent = '独立窗口 ↗';
    const close = document.createElement('button'); close.type = 'button'; close.textContent = '收起';
    close.setAttribute('aria-label', '收起平台助手');
    heading.append(title, full, close);
    const note = document.createElement('p'); note.className = 'note';
    note.textContent = '辅助分析窗口。请核对助手中的分析场景；原工作台继续可用。收起不会停止后台分析。';
    const loadStatus = document.createElement('p'); loadStatus.className = 'load-status';
    loadStatus.textContent = '正在加载助手；如未显示，可打开独立窗口重试。';
    const frame = document.createElement('iframe'); frame.title = '平台助手诊断与证据';
    frame.referrerPolicy = 'same-origin';
    panel.append(heading, note, loadStatus, frame); shadow.append(style, launcher, panel); document.body.append(host);
    let loaded = false;
    function collapse() {
        panel.hidden = true; launcher.hidden = false; launcher.setAttribute('aria-expanded', 'false'); launcher.focus();
    }
    launcher.onclick = () => {
        if (!loaded) {
            // Only copy a known scene ID once. Never read task forms, telemetry, or unsaved edits.
            const selected = document.getElementById('scene')?.value;
            if (['urban','corridor','electromagnetic'].includes(selected)) agentURL.searchParams.set('scene',selected);
            frame.src = agentURL.href; loaded = true;
        }
        panel.hidden = false; launcher.hidden = true; launcher.setAttribute('aria-expanded', 'true'); close.focus();
    };
    close.onclick = collapse;
    shadow.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !panel.hidden) { event.stopPropagation(); collapse(); }
    });
    // Embedded pages only request dismissal. They cannot trigger host operations.
    window.addEventListener('message', event => {
        if (event.origin !== agentURL.origin || event.source !== frame.contentWindow) return;
        if (event.data?.type === 'platform-assistant-close') collapse();
        if (event.data?.type === 'platform-assistant-ready') loadStatus.textContent = '助手已载入；连接状态与错误会在窗口内显示。';
    });
})();
