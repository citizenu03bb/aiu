// SPDX-License-Identifier: GPL-3.0-or-later
// Explicit destinations, never constructed from snapshot text or provider errors.
import { _, N_ } from './i18n.js';

const LINKS = {
    claude: [N_('Claude usage'), 'https://claude.ai/settings/usage'],
    codex: [N_('Codex usage'), 'https://chatgpt.com/settings/usage?tab=overview'],
    gemini: [N_('Antigravity'), 'https://antigravity.google.com/'],
    mistral: [N_('Mistral usage'), 'https://admin.mistral.ai/organization/usage'],
    openai: [N_('OpenAI API usage'), 'https://platform.openai.com/usage'],
    deepseek: [N_('DeepSeek Platform'), 'https://platform.deepseek.com/'],
    openrouter: [N_('OpenRouter activity'), 'https://openrouter.ai/activity'],
};

/** Credits from Claude/Codex still belong to their subscription, not a separate API account. */
export function providerLink(provider) {
    if (!Object.hasOwn(LINKS, provider))
        return null;
    const [label, url] = LINKS[provider];
    return { label: _(label), url };
}
