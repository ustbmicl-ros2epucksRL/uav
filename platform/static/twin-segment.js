'use strict';
window.TwinSegment = {
    async analyze(record, start, end) {
        const prefix = location.pathname.startsWith('/network/') ? '/network' : '';
        const path = prefix + '/api/twin-experiments/' + encodeURIComponent(record.id) + '/segment';
        const response = await fetch(path + '?' + new URLSearchParams({start, end}));
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '无法读取航线选段');
        return data;
    }
};
