// SPDX-License-Identifier: GPL-3.0-or-later
import type { EngineInterface, Register, SessionRateLimit } from 'claude-code'

// Claude Code's own rate-limit readings (the figures its status line shows),
// written where aiu reads them, so aiu never handles a Claude.ai token.
// Same state directory as aiu's history: $XDG_STATE_HOME/aiu, else ~/.local/state/aiu.
// The format is aiu's docs/json-schema.md, "claude-usage.json".

async function usageFile($: EngineInterface): Promise<string | undefined> {
  const state = await $.env.get('XDG_STATE_HOME')
  if (state && state.startsWith('/')) return `${state}/aiu/claude-usage.json`
  const home = await $.env.get('HOME')
  return home ? `${home}/.local/state/aiu/claude-usage.json` : undefined
}

async function save($: EngineInterface, rateLimits: readonly SessionRateLimit[]): Promise<void> {
  // Off a subscription, or before the first response, there is nothing to say.
  if (rateLimits.length === 0) return
  const path = await usageFile($)
  if (!path) return
  const body = {
    schema: 1,
    source: 'claude-code',
    written_at: new Date(await $.clock.now()).toISOString(),
    rate_limits: rateLimits.map(r => ({
      kind: r.kind,
      percent_used: r.percentUsed,
      resets_at: r.resetsAt ?? null,
    })),
  }
  await $.fs.write(path, JSON.stringify(body, null, 2) + '\n')
}

export const register: Register = on => {
  // Pushed after each turn, and whenever a window moves a whole point.
  on('session.measure', async ($, e, next) => {
    if (e.changed.includes('rateLimits')) await save($, e.rateLimits)
    return next(e)
  }).catch(($, e, next) => next(e))

  // A reading as soon as the session has one, so a fresh start writes too.
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    const { rateLimits } = await $.session.usage()
    await save($, rateLimits)
    return result
  }).catch(($, e, next) => next(e))
}
