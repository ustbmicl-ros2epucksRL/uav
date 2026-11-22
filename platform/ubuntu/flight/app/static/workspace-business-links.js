/* Keep ordinary links between the three local apps in the current workspace. */
'use strict';
function bindWorkspaceBusinessLinks(frame, navigate) {
    let attached, nextFrame;
    // Map tiles can hold up iframe.load long after its links are interactive.
    function bindDocument() {
        const doc = frame.contentDocument;
        if (!doc || doc.location.origin !== location.origin || doc.location.protocol === 'about:') return false;
        if (doc === attached) return true;
        attached = doc;
        doc.defaultView.addEventListener('pagehide', () => {
            nextFrame = requestAnimationFrame(waitForDocument);
        }, {once:true});
        doc.addEventListener('click', event => {
            if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            const link = event.target.closest?.('a[href]');
            if (!link || link.hasAttribute('download') || link.id === 'situation-open') return;
            const url = new URL(link.href, doc.baseURI);
            if (url.origin !== location.origin) return;
            let workspace;
            if (url.pathname === '/' || url.pathname === '/station') workspace = 'station';
            else if (/^\/network(?:\/|$)/.test(url.pathname)) workspace = 'planning';
            else if (/^\/service(?:\/|$)/.test(url.pathname)) workspace = 'service';
            if (!workspace || workspace === frame.dataset.frame) return;
            if (/\/(?:api|static)\//.test(url.pathname)) return;
            event.preventDefault();
            if (workspace === 'station') url.pathname = '/station';
            navigate(workspace, url.pathname + url.search + url.hash);
        });
        return true;
    }
    function waitForDocument() {
        if (!frame.isConnected) return;
        if (frame.contentDocument !== attached && bindDocument()) return;
        nextFrame = requestAnimationFrame(waitForDocument);
    }
    frame.addEventListener('load', () => {
        cancelAnimationFrame(nextFrame);
        bindDocument();
    });
    nextFrame = requestAnimationFrame(waitForDocument);
}
