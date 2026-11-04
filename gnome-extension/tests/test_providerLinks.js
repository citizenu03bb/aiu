// SPDX-License-Identifier: GPL-3.0-or-later
import { providerLink } from '../providerLinks.js';

function equal(got, want, label) {
    if (JSON.stringify(got) !== JSON.stringify(want))
        throw new Error(`${label}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
}

// Keep subscription credits in the same account as the measured quotas.
equal(providerLink('claude'), { label: 'Claude usage', url: 'https://claude.ai/settings/usage' }, 'Claude subscription');
equal(providerLink('codex'), { label: 'Codex usage', url: 'https://chatgpt.com/settings/usage?tab=overview' }, 'Codex subscription');
equal(providerLink('openai'), { label: 'OpenAI API usage', url: 'https://platform.openai.com/usage' }, 'API spend, separate from Codex');
equal(providerLink('gemini'), { label: 'Antigravity', url: 'https://antigravity.google.com/' }, 'Antigravity quotas, not AI Studio');
equal(providerLink('mistral').url, 'https://admin.mistral.ai/organization/usage', 'Vibe and API usage');
equal(providerLink('deepseek').url, 'https://platform.deepseek.com/', 'DeepSeek platform');
equal(providerLink('openrouter').url, 'https://openrouter.ai/activity', 'OpenRouter activity');
for (const unknown of ['new-provider', 'https://example.com/', '__proto__', 'constructor', '', null])
    equal(providerLink(unknown), null, `unknown provider ${unknown} has no guessed link`);
console.log('[Test] Provider destinations distinguish subscriptions, credits and APIs ✅');
