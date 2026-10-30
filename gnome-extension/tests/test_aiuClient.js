// test_aiuClient.js - Offline unit tests for aiuClient parsing (run in CI).
// The test that calls the real aiu CLI is test_aiuClient_live.js (local only).
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

try {
    run();
} catch (err) {
    console.error('[Test Failed]', err);
    System.exit(1);
}
