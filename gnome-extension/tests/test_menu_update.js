// test_menu_update.js - Test menuView.update with live aiu data
import { AiuClient } from '../aiuClient.js';

console.log('[Test] Fetching real aiu data...');
const client = new AiuClient();
const res = await client.fetchSnapshot();
if (!res.success) {
    throw new Error(`fetchSnapshot failed: ${res.error}`);
}

console.log('[Test] Data fetched successfully:', Object.keys(res.data));
console.log(' - Quotas:', res.data.quotas.map(q => `${q.provider} (${q.metric}): ${q.used_pct}%`));
console.log(' - Balances:', res.data.balances.map(b => `${b.provider}: ${b.used} ${b.unit}`));
console.log(' - Errors:', res.data.errors);
console.log(' - StatusColor:', res.data.statusColor);
console.log(' - Advice:', res.data.advice);
