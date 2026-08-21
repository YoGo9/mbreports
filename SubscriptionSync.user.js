// ==UserScript==
// @name         MusicBrainz Artist Subscriptions → MBReports
// @namespace    https://yogo9.github.io/mbreports/
// @version      1.0.0
// @description  Sync/export MusicBrainz artist subscriptions and make them available to YoGo9 MusicBrainz reports.
// @match        https://musicbrainz.org/user/*/subscriptions/artist*
// @match        https://beta.musicbrainz.org/user/*/subscriptions/artist*
// @match        https://yogo9.github.io/mbreports/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_setClipboard
// @run-at       document-start
// ==/UserScript==

(function () {
    'use strict';

    const GM_KEY = 'mbreports_artist_subscriptions_v1';
    const LOCAL_KEY = 'mbreports_artist_subscriptions_v1';

    // ------------------------------------------------------------
    // GitHub Pages bridge
    // ------------------------------------------------------------

    if (location.hostname === 'yogo9.github.io') {
        try {
            const saved = GM_getValue(GM_KEY, null);

            if (saved && Array.isArray(saved.mbids)) {
                localStorage.setItem(
                    LOCAL_KEY,
                    JSON.stringify(saved)
                );
            }
        } catch (e) {
            console.error('MBReports subscription bridge:', e);
        }

        return;
    }

    // ------------------------------------------------------------
    // MusicBrainz
    // ------------------------------------------------------------

    function ready(fn) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', fn, {
                once: true
            });
        } else {
            fn();
        }
    }

    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    function getUsername() {
        const m = location.pathname.match(
            /^\/user\/([^/]+)\/subscriptions\/artist/
        );

        return m
            ? decodeURIComponent(m[1])
            : 'MusicBrainz';
    }

    function currentPageNumber() {
        const u = new URL(location.href);
        return Number(u.searchParams.get('page') || 1);
    }

    function getMaxPage(doc) {
        let max = 1;

        for (const a of doc.querySelectorAll('a[href]')) {
            try {
                const u = new URL(
                    a.getAttribute('href'),
                    location.href
                );

                if (u.pathname !== location.pathname) {
                    continue;
                }

                const page = Number(
                    u.searchParams.get('page')
                );

                if (Number.isInteger(page) && page > max) {
                    max = page;
                }
            } catch (_) {
            }
        }

        return max;
    }

    function extractArtistMbids(doc) {
        const ids = new Set();

        const links = doc.querySelectorAll(
            'table a[href*="/artist/"]'
        );

        for (const a of links) {
            try {
                const u = new URL(
                    a.getAttribute('href'),
                    location.origin
                );

                const m = u.pathname.match(
                    /^\/artist\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i
                );

                if (m) {
                    ids.add(m[1].toLowerCase());
                }
            } catch (_) {
            }
        }

        return [...ids];
    }

    function expectedArtistCount() {
        const text = document.body.innerText;

        const match = text.match(
            /(?:^|\n)\s*([\d,]+)\s+artists\s*(?:\n|$)/i
        );

        if (!match) {
            return null;
        }

        return Number(
            match[1].replaceAll(',', '')
        );
    }

    async function fetchPage(page) {
        const u = new URL(location.href);
        u.searchParams.set('page', page);

        let response;

        for (let attempt = 1; attempt <= 2; attempt++) {
            response = await fetch(u.href, {
                credentials: 'include',
                headers: {
                    'Accept': 'text/html'
                }
            });

            if (response.ok) {
                break;
            }

            if (attempt < 2) {
                await sleep(2000);
            }
        }

        if (!response || !response.ok) {
            throw new Error(
                `Page ${page}: HTTP ${response?.status || 'error'}`
            );
        }

        const html = await response.text();

        return new DOMParser().parseFromString(
            html,
            'text/html'
        );
    }

    function getSaved() {
        const saved = GM_getValue(GM_KEY, null);

        if (
            !saved ||
            !Array.isArray(saved.mbids)
        ) {
            return null;
        }

        return saved;
    }

    function downloadText(filename, text) {
        const blob = new Blob(
            [text],
            { type: 'text/plain;charset=utf-8' }
        );

        const url = URL.createObjectURL(blob);

        const a = document.createElement('a');
        a.href = url;
        a.download = filename;

        document.body.appendChild(a);
        a.click();
        a.remove();

        setTimeout(
            () => URL.revokeObjectURL(url),
            1000
        );
    }

    function safeFilename(value) {
        return value.replace(
            /[^a-z0-9_-]+/gi,
            '_'
        );
    }

    function init() {
        const username = getUsername();

        const panel = document.createElement('div');

        panel.style.cssText = `
            margin: 12px 0;
            padding: 10px;
            border: 1px solid #bbb;
            border-radius: 6px;
            background: #f7f7f7;
            font-size: 14px;
        `;

        panel.innerHTML = `
            <div style="
                font-weight:bold;
                margin-bottom:8px;
            ">
                MBReports artist subscriptions
            </div>

            <div style="
                display:flex;
                gap:6px;
                flex-wrap:wrap;
            ">
                <button type="button" id="mbreports-sync">
                    Sync subscriptions
                </button>

                <button type="button" id="mbreports-export">
                    Export MBIDs
                </button>

                <button type="button" id="mbreports-copy">
                    Copy MBIDs
                </button>
            </div>

            <div id="mbreports-status"
                 style="margin-top:8px;color:#555">
            </div>
        `;

        const heading = [...document.querySelectorAll(
            'h1,h2,h3'
        )].find(el =>
            /artist subscriptions/i.test(el.textContent)
        );

        if (heading) {
            heading.insertAdjacentElement(
                'afterend',
                panel
            );
        } else {
            const content =
                document.querySelector('#content') ||
                document.body;

            content.prepend(panel);
        }

        const status =
            panel.querySelector('#mbreports-status');

        const syncButton =
            panel.querySelector('#mbreports-sync');

        const exportButton =
            panel.querySelector('#mbreports-export');

        const copyButton =
            panel.querySelector('#mbreports-copy');

        function showSavedStatus() {
            const saved = getSaved();

            if (!saved) {
                status.textContent =
                    'No artist subscriptions synced yet.';
                return;
            }

            const date = saved.syncedAt
                ? new Date(saved.syncedAt).toLocaleString()
                : 'Unknown';

            status.textContent =
                `${saved.mbids.length.toLocaleString()} artists saved • ${date}`;
        }

        showSavedStatus();

        syncButton.addEventListener(
            'click',
            async () => {
                syncButton.disabled = true;

                try {
                    const maxPage = getMaxPage(document);
                    const here = currentPageNumber();
                    const all = new Set();

                    const currentIds =
                        extractArtistMbids(document);

                    currentIds.forEach(id => all.add(id));

                    status.textContent =
                        `Found ${maxPage} pages. Starting sync…`;

                    for (
                        let page = 1;
                        page <= maxPage;
                        page++
                    ) {
                        if (page === here) {
                            status.textContent =
                                `Page ${page}/${maxPage} • ` +
                                `${all.size.toLocaleString()} artists`;

                            continue;
                        }

                        const doc = await fetchPage(page);

                        const ids =
                            extractArtistMbids(doc);

                        ids.forEach(id => all.add(id));

                        status.textContent =
                            `Page ${page}/${maxPage} • ` +
                            `${all.size.toLocaleString()} artists`;

                        // Be polite to MusicBrainz.
                        await sleep(1000);
                    }

                    const mbids = [...all].sort();

                    const data = {
                        version: 1,
                        username,
                        sourceHost: location.hostname,
                        syncedAt: new Date().toISOString(),
                        count: mbids.length,
                        mbids
                    };

                    GM_setValue(GM_KEY, data);

                    const expected =
                        expectedArtistCount();

                    if (
                        expected !== null &&
                        expected !== mbids.length
                    ) {
                        status.textContent =
                            `Saved ${mbids.length.toLocaleString()} artists, ` +
                            `but MusicBrainz shows ${expected.toLocaleString()}. ` +
                            `Please try syncing again.`;
                    } else {
                        status.textContent =
                            `✓ Saved ${mbids.length.toLocaleString()} artist subscriptions.`;
                    }

                } catch (e) {
                    console.error(e);

                    status.textContent =
                        `Error: ${e.message}`;
                } finally {
                    syncButton.disabled = false;
                }
            }
        );

        exportButton.addEventListener(
            'click',
            () => {
                const saved = getSaved();

                if (!saved) {
                    status.textContent =
                        'Sync subscriptions first.';
                    return;
                }

                const text =
                    saved.mbids.join('\n') + '\n';

                const date =
                    new Date().toISOString().slice(0, 10);

                downloadText(
                    `musicbrainz-artist-subscriptions-${safeFilename(saved.username || 'user')}-${date}.txt`,
                    text
                );

                status.textContent =
                    `Exported ${saved.mbids.length.toLocaleString()} MBIDs.`;
            }
        );

        copyButton.addEventListener(
            'click',
            () => {
                const saved = getSaved();

                if (!saved) {
                    status.textContent =
                        'Sync subscriptions first.';
                    return;
                }

                GM_setClipboard(
                    saved.mbids.join('\n'),
                    'text'
                );

                status.textContent =
                    `Copied ${saved.mbids.length.toLocaleString()} MBIDs.`;
            }
        );
    }

    ready(init);

})();
