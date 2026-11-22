const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function checkWorkspace(browser, workspace, experimentId) {
    const page = await browser.newPage();
    const writes = [], errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', req => {
        if (new URL(req.url()).origin === new URL(workspace).origin && ['POST', 'PUT', 'DELETE', 'PATCH'].includes(req.method())) {
            writes.push(req.method() + ' ' + req.url());
        }
    });
    const url = new URL(workspace);
    url.search = new URLSearchParams({platform:'planning', experiment:experimentId, start:68, end:80});
    await page.goto(url.href);
    const planning = page.frameLocator('[data-frame="planning"]');
    const station = page.frameLocator('[data-frame="station"]');
    const chat = page.frameLocator('#chat-frame');
    await planning.locator('#station-segment').click();
    await station.locator('#replay-panel').waitFor();
    assert.equal(await station.locator('#replay-index').getAttribute('min'), '68');
    assert.equal(await station.locator('#replay-index').getAttribute('max'), '80');
    await station.locator('#replay-index').fill('75');
    await station.locator('#replay-assistant').click();
    await chat.locator('#segment-evidence a').waitFor();
    assert.ok((await chat.locator('#segment-evidence a').getAttribute('href')).endsWith(experimentId + '/segment?start=68&end=80'));
    await chat.locator('[data-dock-view="steps"]').click();
    await chat.locator('#segment-steps').waitFor();
    await chat.locator('[data-dock-view="history"]').click();
    await chat.locator('#segment-history').getByText(/实验已归档/).waitFor();
    await station.locator('#back-planning').click();
    await planning.locator('#segment-title').filter({hasText:/68.*80/}).waitFor();
    assert.deepEqual(writes, []);
    assert.deepEqual(errors, []);
    await page.close();
}

async function main() {
    const base = (process.env.TWIN_PLANNING_URL || 'http://127.0.0.1:8894/network').replace(/\/$/, '');
    const browser = await chromium.launch({executablePath: process.env.CHROMIUM_EXECUTABLE});
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    try {
        const history = await (await page.request.get(base + '/api/twin-experiments')).json();
        assert.ok(history.experiments.length, 'This read-only browser check needs an existing urban experiment.');
        const id = history.experiments.at(-1).id;
        const archive = await (await page.request.get(base + '/api/twin-experiments/' + id)).json();
        const times = archive.inputs.trajectory.times;
        const rows = archive.result.deployments;
        const neutral = times.findIndex((_, i) => rows.every(row => row.timeline[i].state === 'ok'));
        assert.ok(neutral >= 0, 'Use the default urban fixture for the neutral interval.');
        // A shared link must still resolve an older archive outside the recent history window.
        await page.route('**/api/twin-experiments', route => route.fulfill({json: {experiments: []}}));
        const link = base + '/twin?experiment=' + id;
        await page.goto(link + '&start=' + neutral + '&end=' + neutral);
        await page.locator('#segment-result').waitFor({state: 'visible'});
        assert.match(await page.locator('#segment-title').innerText(), /1 个航迹采样点/);
        assert.match(await page.locator('#segment-conclusion').innerText(), /持平/);
        assert.equal(await page.locator('#history').inputValue(), id);
        assert.equal(new URL(await page.locator('#station-segment').getAttribute('href')).searchParams.get('twin_experiment'), id);
        const baselineGap = rows.find(item => item.key === 'baseline').segments[0];
        const map = page.locator('#map');
        await map.scrollIntoViewIfNeeded();
        const coordinate = archive.inputs.trajectory.points[Math.floor((baselineGap.start + baselineGap.end) / 2)];
        const click = await map.evaluate((canvas, [x, y]) => {
            const {X, Y} = mapProjection(canvas);
            return {x: X(x) * canvas.clientWidth / canvas.width, y: Y(y) * canvas.clientHeight / canvas.height};
        }, coordinate);
        await map.click({position: click});
        await page.waitForFunction(([start, end]) => {
            const title = document.querySelector('#segment-title').textContent;
            return title.startsWith(start + '–' + end + ' s');
        }, [baselineGap.start, baselineGap.end]);
        assert.match(await page.locator('#segment-diagnosis').innerText(), new RegExp(baselineGap.cause));
        const submit = async (start, end) => {
            await page.locator('#segment-start').fill(String(times[start]));
            await page.locator('#segment-end').fill(String(times[end]));
            await page.locator('#segment-form button').click();
        };
        let release, announce;
        const gate = new Promise(resolve => { release = resolve; });
        const started = new Promise(resolve => { announce = resolve; });
        await page.route('**/segment?start=3&end=4', async route => {
            const response = await route.fetch();
            announce();
            await gate;
            await route.fulfill({response});
        });
        await submit(3, 4);
        await started;
        await submit(8, 11);
        await page.locator('#segment-result').waitFor({state: 'visible'});
        const staleFinished = page.waitForResponse(r => r.url().endsWith('segment?start=3&end=4'));
        release();
        await staleFinished;
        await page.evaluate(() => new Promise(requestAnimationFrame));
        assert.equal(await page.locator('#segment-title').innerText(), times[8] + '–' + times[11] + ' s · 4 个航迹采样点');
        assert.equal(await page.locator('#time').getAttribute('min'), '8');
        assert.equal(await page.locator('#time').getAttribute('max'), '11');
        const assistantUrl = new URL(await page.locator('#assist-segment').getAttribute('href'));
        assert.equal(assistantUrl.searchParams.get('experiment'), id);
        assert.equal(assistantUrl.searchParams.get('start'), '8');
        assert.equal(assistantUrl.searchParams.get('end'), '11');

        // Capture the standalone assistant request without creating an actual model run.
        let payload;
        await page.route('**/api/research-agent/runs', route => {
            payload = route.request().postDataJSON();
            return route.fulfill({status: 409, json: {detail: 'test capture'}});
        });
        await page.locator('#question').fill('所选航段的规划对照有什么结论？');
        await page.locator('#send').click();
        await page.getByText('助手未启动：test capture').waitFor();
        assert.deepEqual(payload.twin_segment, {experiment_id: id, start: 8, end: 11});
        assert.equal(payload.skill, 'twin_experiment');
        await page.setViewportSize({width: 390, height: 844});
        const layout = await page.evaluate(() => ({
            width: innerWidth, page: document.documentElement.scrollWidth,
            sections: Array.from(document.querySelectorAll('main, aside, header, nav, main > *, aside > *'))
                .filter(el => el.getBoundingClientRect().right > innerWidth + 1)
                .map(el => el.tagName + '.' + el.className + '#' + el.id)
        }));
        assert.ok(layout.page <= layout.width + 1, JSON.stringify(layout));
        await page.goto(link + '&start=22&end=1');
        await page.getByText('链接中的航段超出档案范围，请重新选择。').waitFor();
        assert.ok(await page.locator('#segment-result').isHidden());
        assert.deepEqual(errors, []);
        if (process.env.TWIN_WORKSPACE_URL) await checkWorkspace(browser, process.env.TWIN_WORKSPACE_URL, id);
        console.log('Passed archived link, neutral segment, map segment selection, rapid reselection, pinned assistant, mobile layout and invalid interval.');
    } finally {
        await browser.close();
    }
}

main().catch(error => {console.error(error); process.exitCode = 1;});
