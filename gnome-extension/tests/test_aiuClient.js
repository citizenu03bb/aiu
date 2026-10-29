// test_aiuClient.js - Unit test for aiuClient parsing and execution
import GLib from 'gi://GLib';
import { AiuClient } from '../aiuClient.js';

async function run() {
    console.log('[Test] Instantiating AiuClient...');
    const client = new AiuClient();

    console.log('[Test] Testing _processData with synthetic mock data...');
    const mock = {
        captured_at: "2026-10-29T16:00:00Z",
        rows: [
            { provider: "claude_max", metric: "—", kind: "error", note: "token expired" },
            { provider: "codex", metric: "5-Hour Limit", kind: "quota", used: 1.0, used_pct: 100.0, reset: "in 40m" },
            { provider: "gemini", metric: "5-Hour Limit", kind: "quota", used: 0.1, used_pct: 10.0, reset: "in 4h" },
            { provider: "deepseek", metric: "Balance (USD)", kind: "balance", used: 6.31, unit: "USD" }
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
    if (!processed.advice || !processed.advice.includes('codex')) {
        throw new Error(`Expected advice to mention codex saturation, got: ${processed.advice}`);
    }

    console.log('[Test] Testing real async execution with aiu CLI...');
    const loop = GLib.MainLoop.new(null, false);
    let realRes = null;

    client.fetchSnapshot().then((res) => {
        realRes = res;
        loop.quit();
    });

    loop.run();

    if (!realRes.success) {
        throw new Error(`fetchSnapshot failed: ${realRes.error}`);
    }

    console.log('[Test] Real fetchSnapshot succeeded!');
    console.log(' - CapturedAt:', realRes.data.capturedAt);
    console.log(' - Status:', realRes.data.statusColor);
    console.log(' - Rows count (quotas + balances + errors):',
        realRes.data.quotas.length + realRes.data.balances.length + realRes.data.errors.length);
    console.log(' - Advice:', realRes.data.advice);

    console.log('[Test] All AiuClient tests passed successfully! ✅');
}

run().catch((err) => {
    console.error('[Test Failed]', err);
    imports.system.exit(1);
});
