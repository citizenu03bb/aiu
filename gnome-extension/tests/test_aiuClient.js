// test_aiuClient.js - Offline unit tests for aiuClient parsing (run in CI).
// The test that calls the real aiu CLI is test_aiuClient_live.js (local only);
// the subprocess tests here run sh and sleep in its place.
import GLib from 'gi://GLib';
import System from 'system';
import { AiuClient, installCommand, needsSetup, resetText, SCHEMA, schemaProblem } from '../aiuClient.js';

function assertEqual(got, want, what) {
    const g = JSON.stringify(got);
    const w = JSON.stringify(want);
    if (g !== w) {
        throw new Error(`${what}: expected ${w}, got ${g}`);
    }
}

/** A file of tests/fixtures/ at the repository root, which the Python tests write from aiu itself. */
function readFixture(name) {
    const [here] = GLib.filename_from_uri(import.meta.url);
    const path = GLib.build_filenamev([GLib.path_get_dirname(here), '..', '..', 'tests', 'fixtures', name]);
    return new TextDecoder().decode(GLib.file_get_contents(path)[1]);
}

/**
 * What aiu --json prints, from recorded probe answers and a frozen clock
 * (tests/test_json_output.py writes it): every field the menu reads is checked
 * here, so a field renamed in aiu or in the extension alone fails one side
 * (docs/json-schema.md).
 */
function runContract(client) {
    console.log('[Test] tests/fixtures/snapshot.json through _processData...');
    const raw = JSON.parse(readFixture('snapshot.json'));
    assertEqual([raw.schema, SCHEMA, schemaProblem(raw)], [1, 1, null], 'contract: schema');
    const data = client._processData(raw);
    // Ten minutes after the snapshot: a reset counted from reset_epoch, not from
    // the `reset` text, which still says what it said at capture time.
    const now = Date.parse(raw.captured_at) / 1000 + 600;
    assertEqual(data.capturedAt, '2026-10-26T07:33:20+00:00', 'contract: captured_at');
    assertEqual(data.quotas.map(q => [q.provider, q.metric, q.used_pct, q.note, resetText(q, now)]), [
        ['claude', '5-Hour Limit', 12, '', 'in 1h50m'],
        ['claude', 'Weekly (all models)', 40, '', 'in 2d23h'],
        ['claude', 'Weekly Sonnet', 5, '', 'in 2d23h'],
        ['codex', '5-Hour Limit', 20, 'plus', 'in 50m'],
        ['codex', 'Weekly Limit', 90, '', 'in 4d23h'],
        ['gemini', '5-Hour Limit', 25, '', 'in 1h50m'],
        ['gemini', 'Weekly Limit', 2, '', 'in 6d19h'],
        ['gemini', 'Image generation', 0, '', null],
    ], 'contract: quotas');
    // The bars show used_pct; `used` is only the fallback for a row without it.
    const pctOnly = client._processData({ ...raw, rows: raw.rows.map(r => ({ ...r, used: null })) });
    assertEqual(pctOnly.quotas.map(q => q.used_pct), data.quotas.map(q => q.used_pct), 'contract: used_pct');
    assertEqual(data.balances.map(b => [b.provider, b.metric, b.kind, b.used, b.limit, b.unit, b.note,
        resetText(b, now)]), [
        ['claude', 'Cloud Credit', 'credit', 31.5, 50, 'USD', '', 'in 9d23h'],
        ['codex', 'Credits', 'balance', 9.99, null, 'USD', '', null],
        ['openai', 'Spend (MTD)', 'spend', 1.73, null, 'USD', '', null],
        ['deepseek', 'Balance (USD)', 'balance', 4.2, null, 'USD', '', null],
        ['deepseek', 'Balance (CNY)', 'balance', 12, null, 'CNY', '', null],
    ], 'contract: balances');
    assertEqual(data.errors.map(e => [e.provider, e.note]), [
        ['openrouter', 'network: cannot connect'],
        ['mistral', 'session rejected (307) — copy a fresh Cookie header from admin.mistral.ai'],
    ], 'contract: errors');
    assertEqual(data.routing.map(l => [l.provider, l.metric, l.status, l.ttr_secs, l.leftover, l.runway_secs]), [
        ['claude', 'Weekly (all models)', 'burn', 259200, 0.3, null],
        ['gemini', 'Weekly Limit', '?', 590400, null, null],
        ['claude', 'Cloud Credit', 'burn', 864000, null, null],
        ['codex', 'Weekly Limit', 'conserve', 432000, -2.15, 19200],
    ], 'contract: advice lots');
    assertEqual([data.statusColor, data.worstQuotaPct, needsSetup(data)], ['critical', 90, false], 'contract: status');
    assertEqual(data.advice, '💡 Burn Claude first\nWeekly (all models): ~30% lost at reset in 3d0h\nConserve: Codex',
        'contract: advice');

    // An aiu older than the schema field prints none: that is schema 1.
    delete raw.schema;
    assertEqual(schemaProblem(raw), null, 'no schema reads as schema 1');
    for (const [schema, words] of [[2, 'update the extension'], [0, 'update aiu'], [null, 'update aiu']]) {
        const problem = schemaProblem({ ...raw, schema });
        assertEqual(!!problem?.includes(words), true, `schema ${schema}: ${problem}`);
    }
    console.log('[Test] The snapshot fixture is read as aiu wrote it ✅');
}

function run() {
    console.log('[Test] Instantiating AiuClient...');
    const client = new AiuClient();
    runContract(client);

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
    const expected = "💡 Burn Claude first\nWeekly (all models): ~11% lost at reset in 5h58m\nConserve: Codex, Gemini";
    if (processed.advice !== expected) {
        throw new Error(`Expected advice "${expected}", got: ${processed.advice}`);
    }
    const cases = [
        [[{ provider: "codex", metric: "Weekly Limit", status: "on_track", ttr_secs: 3600, leftover: 0.01 }],
            "💡 No expiring surplus: route by task fit"],
        [[{ provider: "gemini", metric: "Gemini Flash", status: "?", ttr_secs: 600, leftover: null }],
            "💡 Burn Gemini first\nGemini Flash: reset in 10m (unknown pace)"],
        [[{ provider: "claude", metric: "Cloud Credit", status: "burn", ttr_secs: 90000, leftover: null },
          { provider: "gemini", metric: "Weekly Limit", status: "conserve", ttr_secs: 9, leftover: -0.5 }],
            "💡 Burn Claude first\nCloud Credit: expires in 1d1h\nConserve: Gemini"],
        [[{ provider: "codex", metric: "Weekly Limit", status: "conserve", ttr_secs: 9, leftover: -0.5 }],
            "💡 No subscription has surplus\nKeep each for what only it can do, overflow to the paid API"],
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

    const resets = [
        [{ reset_epoch: 1000 + 3 * 3600 + 8 * 60 }, 'in 3h08m'],
        [{ reset_epoch: 999 }, 'passed, refresh the snapshot'],
        [{ reset: 'expires in 2d 3h' }, 'in 2d3h'],
        [{ reset: 'now' }, 'passed, refresh the snapshot'],
        [{ reset: '2026-11-01T00:00:00Z' }, '2026-11-01T00:00:00Z'],
        [{}, null],
    ];
    for (const [row, want] of resets) {
        const got = resetText(row, 1000);
        if (got !== want) {
            throw new Error(`Expected reset text "${want}" for ${JSON.stringify(row)}, got: ${got}`);
        }
    }

    console.log('[Test] First run: install command and setup state...');
    const installs = [
        [['uv', 'pipx'], 'uv', 'uv tool install git+https://github.com/citizenu03bb/aiu'],
        [['pipx'], 'pipx', 'pipx install git+https://github.com/citizenu03bb/aiu'],
        [[], null, 'pipx install git+https://github.com/citizenu03bb/aiu'],
    ];
    for (const [tools, wantTool, wantCommand] of installs) {
        const got = installCommand(name => tools.includes(name));
        if (got.tool !== wantTool || got.command !== wantCommand) {
            throw new Error(`With ${tools}: expected ${wantTool} / ${wantCommand}, got ${JSON.stringify(got)}`);
        }
    }
    const signedOut = client._processData({ rows: [mock.rows[0]], advice: [] });
    if (!needsSetup(signedOut) || needsSetup(processed) || needsSetup(null)) {
        throw new Error('needsSetup: true only when aiu read no quota and no balance');
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

    console.log('[Test] An unknown schema is a clear message, not a parse error...');
    const unknown = await fakeClient(['sh', '-c', 'echo \'{"schema": 2, "rows": []}\'']).fetchSnapshot();
    if (unknown.success || !unknown.error.includes('update the extension')) {
        throw new Error(`Expected "update the extension", got ${JSON.stringify(unknown)}`);
    }

    console.log('[Test] A missing aiu is reported as such, not as an error...');
    const missing = await fakeClient(null).fetchSnapshot();
    if (missing.success || !missing.missing || missing.error) {
        throw new Error(`Expected { missing: true }, got ${JSON.stringify(missing)}`);
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
