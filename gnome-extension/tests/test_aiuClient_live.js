// test_aiuClient_live.js - Runs the real aiu CLI through AiuClient.
// Local only (make test-live): it needs aiu installed and calls your providers.
import GLib from 'gi://GLib';
import System from 'system';
import { AiuClient } from '../aiuClient.js';

async function run() {
    const client = new AiuClient();

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

    console.log('[Test] Live AiuClient test passed successfully! ✅');
}

run().catch((err) => {
    console.error('[Test Failed]', err);
    System.exit(1);
});
