/* Open the existing algorithm controls when this station is hosted by the workspace. */
(() => {
    'use strict';
    if (window.parent === window || window.frameElement?.dataset.frame !== 'station') return;
    const picker = document.getElementById('algorithm-planner');
    const view = document.querySelector('[data-view="algorithms"]');
    if (!view || !picker) return;

    window.addEventListener('message', event => {
        if (event.origin !== location.origin || event.source !== window.parent) return;
        const message = event.data;
        if (message?.type !== 'workbench-algorithm') return;
        if (!Array.from(picker.options).some(option => option.value === message.algorithm)) return;
        if (!view.classList.contains('active')) view.click();
        if (picker.value !== message.algorithm) {
            picker.value = message.algorithm;
            picker.dispatchEvent(new Event('change', {bubbles:true}));
        }
        picker.focus({preventScroll:true});
    });
    window.parent.postMessage({type:'workbench-business-ready', workspace:'station'}, location.origin);
})();
