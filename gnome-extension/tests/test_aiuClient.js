// test_aiuClient.js - Offline unit tests for aiuClient parsing (run in CI).
// The test that calls the real aiu CLI is test_aiuClient_live.js (local only);
// the subprocess tests here run sh and sleep in its place.
import GLib from 'gi://GLib';
import System from 'system';
import { AiuClient } from '../aiuClient.js';

function run() {
    console.log('[Test] Instantiating AiuClient...');
    const client = new AiuClient();

    console.log('[Test] Testing _processData with synthetic mock data...');
    const mock = {
        captured_at: "2026-10-29T16:00:00Z",
        rows: [
            { provider: "claude", metric: "—", kind: "error", note: "token expired" },
            { provider: "codex", metric: "5-Hour Limit", kind: "quota", used: 1.0, used_pct: 100.0, reset: "in 40m" },
            { provider: "gemini", metric: "5-Hour Limit", kind: "quota", used: 0.1, used_pct: 10.0, reset: "in 4h" },
            { provider: "deepseek", metric: "Balance (USD)", kind: "balance", used: 6.31, unit: "USD" }
        ],
        advice: [
            { provider: "claude", metric: "Weekly (all models)", status: "burn", ttr_secs: 21485, leftover: 0.112, runway_secs: null, detail: "" },
            { provider: "claude", metric: "Cloud Credit", status: "burn", ttr_secs: 514625, leftover: null, runway_secs: null, detail: "" },
            { provider: "codex", metric: "Weekly Limit", status: "conserve", ttr_secs: 437723, leftover: -1.764, runway_secs: 39180, detail: "" },
            { provider: "gemini", metric: "Weekly Limit", status: "conserve", ttr_secs: 424800, leftover: -1.226, runway_secs: 77100, detail: "" }
        ]
    };

    const processed = client._processData(mock);
    console.log('[Test] Processed result:');
    console.log(' - StatusColor:', processed.statusColor);
    console.log(' - Quotas count:', processed.quotas.length);
    console.log(' - Balances count:', processed.balances.length);
    console.log(' - Errors count:', processed.errors.length);
    console.log(' - Advice:', processed.advice);

    if (processed.statusColor !== 'critical') {
        throw new Error(`Expected statusColor 'critical' due to error & 100% quota, got: ${processed.statusColor}`);
    }
    if (processed.quotas.length !== 2) {
        throw new Error(`Expected 2 quotas, got: ${processed.quotas.length}`);
    }
    const expected = "💡 Brûle d'abord Claude\nWeekly (all models) : ~11 % perdu au reset dans 5h58\nÀ ménager : Codex, Gemini";
    if (processed.advice !== expected) {
        throw new Error(`Expected advice "${expected}", got: ${processed.advice}`);
    }
    const cases = [
        [[{ provider: "codex", metric: "Weekly Limit", status: "on_track", ttr_secs: 3600, leftover: 0.01 }],
            "💡 Pas de surplus qui expire : route par fit de tâche"],
        [[{ provider: "gemini", metric: "Gemini Flash", status: "?", ttr_secs: 600, leftover: null }],
            "💡 Brûle d'abord Gemini\nGemini Flash : reset dans 10min (allure inconnue)"],
        [[{ provider: "claude", metric: "Cloud Credit", status: "burn", ttr_secs: 90000, leftover: null },
          { provider: "gemini", metric: "Weekly Limit", status: "conserve", ttr_secs: 9, leftover: -0.5 }],
            "💡 Brûle d'abord Claude\nCloud Credit : expire dans 1j1h\nÀ ménager : Gemini"],
        [[{ provider: "codex", metric: "Weekly Limit", status: "conserve", ttr_secs: 9, leftover: -0.5 }],
            "💡 Aucun forfait n'a de surplus\nRéserve chacun à ce que lui seul sait faire, déborde sur l'API payante"],
        [undefined, null],
    ];
    const unknown = client._processData({ rows: [{ provider: "x", metric: "m", kind: "quota" }], advice: [] });
    if (unknown.quotas[0].used_pct !== null || unknown.statusColor !== 'normal') {
        throw new Error(`Unreadable quota must stay unknown, got ${unknown.quotas[0].used_pct} / ${unknown.statusColor}`);
    }
    const soon = client._processData({ rows: [], advice: [mock.advice[0]] });
    if (soon.statusColor !== 'warning') {
        throw new Error(`Quota lost within 24h should warn, got ${soon.statusColor}`);
    }
    for (const [lots, want] of cases) {
        const got = client._generateAdvice(lots);
        if (got !== want) {
            throw new Error(`Expected advice "${want}", got: ${got}`);
        }
    }

    console.log('[Test] All offline AiuClient tests passed successfully! ✅');
}

/** A client that runs argv instead of aiu. */
function fakeClient(argv, options) {
    const client = new AiuClient(options);
    client._resolveCommand = () => argv;
    return client;
}

/** Resolves once proc has exited, with whether a signal killed it. */
function waitExit(proc) {
    return new Promise((resolve) => {
        proc.wait_async(null, (p, res) => {
            p.wait_finish(res);
            resolve(p.get_if_signaled());
        });
    });
}

function assertGone(pid, what) {
    if (GLib.file_test(`/proc/${pid}`, GLib.FileTest.EXISTS)) {
        throw new Error(`${what}: aiu (pid ${pid}) is still running`);
    }
}

async function runSubprocess() {
    console.log('[Test] A finished fetch is parsed...');
    const ok = await fakeClient(['sh', '-c', 'echo \'{"rows": [], "advice": []}\'']).fetchSnapshot();
    if (!ok.success || ok.data.statusColor !== 'normal') {
        throw new Error(`Expected a parsed snapshot, got ${JSON.stringify(ok)}`);
    }

    console.log('[Test] cancel() (what disable() calls) kills aiu...');
    let client = fakeClient(['sleep', '60']);
    let pending = client.fetchSnapshot();
    let proc = client._run.proc;
    let pid = proc.get_identifier();
    client.cancel();
    let res = await pending;
    if (!res.cancelled || !(await waitExit(proc))) {
        throw new Error(`Expected a cancelled fetch and a killed aiu, got ${JSON.stringify(res)}`);
    }
    assertGone(pid, 'cancel');
    if (client._run !== null) {
        throw new Error('cancel() left a fetch in flight');
    }

    console.log('[Test] A newer fetch kills the one it replaces...');
    pending = client.fetchSnapshot();
    proc = client._run.proc;
    pid = proc.get_identifier();
    client._resolveCommand = () => ['sh', '-c', 'echo \'{"rows": []}\''];
    const [older, newer] = await Promise.all([pending, client.fetchSnapshot()]);
    if (!older.cancelled || !newer.success || !(await waitExit(proc))) {
        throw new Error(`Expected the older fetch cancelled, got ${JSON.stringify([older, newer])}`);
    }
    assertGone(pid, 'newer fetch');

    console.log('[Test] The watchdog kills a fetch that runs too long...');
    client = fakeClient(['sleep', '60'], { timeoutSeconds: 1 });
    pending = client.fetchSnapshot();
    proc = client._run.proc;
    pid = proc.get_identifier();
    res = await pending;
    if (res.success || res.cancelled || !res.error.includes('1 s') || !(await waitExit(proc))) {
        throw new Error(`Expected a timeout error and a killed aiu, got ${JSON.stringify(res)}`);
    }
    assertGone(pid, 'watchdog');

    console.log('[Test] A fetch that finishes in time disarms the watchdog...');
    client = fakeClient(['sh', '-c', 'echo \'{"rows": []}\''], { timeoutSeconds: 1 });
    pending = client.fetchSnapshot();
    const run = client._run;
    res = await pending;
    await new Promise((resolve) => {
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
            resolve();
            return GLib.SOURCE_REMOVE;
        });
    });
    if (!res.success || run.watchdogId !== 0 || run.timedOut) {
        throw new Error(`Expected success and no watchdog, got ${JSON.stringify(res)}`);
    }

    console.log('[Test] All subprocess tests passed ✅');
}

let failed = false;
try {
    run();
} catch (err) {
    console.error('[Test Failed]', err);
    System.exit(1);
}

const loop = GLib.MainLoop.new(null, false);
// Fails rather than hangs if a promise never resolves.
GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 20, () => {
    console.error('[Test Failed] subprocess tests timed out');
    failed = true;
    loop.quit();
    return GLib.SOURCE_REMOVE;
});
runSubprocess().catch((err) => {
    console.error('[Test Failed]', err);
    failed = true;
}).finally(() => loop.quit());
loop.run();
if (failed) {
    System.exit(1);
}
