// SPDX-License-Identifier: GPL-3.0-or-later
// Run with: claude plugin test claude-code-plugin
import type { On, SessionRateLimit } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'

const NOW = Date.parse('2026-10-30T22:11:46.367Z')
const LIMITS: SessionRateLimit[] = [
  { kind: 'five_hour', percentUsed: 19, resetsAt: '2026-10-31T02:30:00.000Z' },
  { kind: 'seven_day', percentUsed: 16.5 },
]
const CONTEXT = { window: 200000 }

type Write = { path: string; text: string }

function world(on: On, env: Record<string, string>,
               rateLimits: SessionRateLimit[]): Write[] {
  const writes: Write[] = []
  mock.clock(on, { now: NOW })
  mock.env(on, env)
  on('fs.write', (_, e) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  on('session.usage', () => ({ value: { startedAt: NOW, context: CONTEXT, rateLimits } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.measure', (_, e) => ({ changed: e.changed }))
  return writes
}

test('a new reading is written to the XDG state directory', async ($, on) => {
  const writes = world(on, { HOME: '/home/me', XDG_STATE_HOME: '/state' }, [])
  await $.session.measure({ context: CONTEXT, rateLimits: LIMITS, changed: ['rateLimits'] })
  expect(writes.length).toBe(1)
  expect(writes[0]?.path).toBe('/state/aiu/claude-usage.json')
  expect(JSON.parse(writes[0]?.text ?? '')).toEqual({
    schema: 1,
    source: 'claude-code',
    written_at: '2026-10-30T22:11:46.367Z',
    rate_limits: [
      { kind: 'five_hour', percent_used: 19, resets_at: '2026-10-31T02:30:00.000Z' },
      { kind: 'seven_day', percent_used: 16.5, resets_at: null },
    ],
  })
})

test('without XDG_STATE_HOME it goes under ~/.local/state', async ($, on) => {
  const writes = world(on, { HOME: '/home/me', XDG_STATE_HOME: 'relative' }, [])
  await $.session.measure({ context: CONTEXT, rateLimits: LIMITS, changed: ['rateLimits'] })
  expect(writes[0]?.path).toBe('/home/me/.local/state/aiu/claude-usage.json')
})

test('only a change of the rate limits writes, and never an empty reading', async ($, on) => {
  const writes = world(on, { HOME: '/home/me' }, [])
  await $.session.measure({ context: CONTEXT, rateLimits: LIMITS, changed: ['context'] })
  await $.session.measure({ context: CONTEXT, rateLimits: [], changed: ['rateLimits'] })
  expect(writes.length).toBe(0)
})

test('a session start writes the reading it already has', async ($, on) => {
  const writes = world(on, { HOME: '/home/me' }, LIMITS)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  expect(writes.length).toBe(1)
  expect(writes[0]?.path).toBe('/home/me/.local/state/aiu/claude-usage.json')
})

test('a failed write never stops the session', async ($, on) => {
  mock.clock(on, { now: NOW })
  mock.env(on, { HOME: '/home/me' })
  on('fs.write', () => { throw new Error('EACCES') })
  on('session.usage', () => ({ value: { startedAt: NOW, context: CONTEXT, rateLimits: LIMITS } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.measure', (_, e) => ({ changed: e.changed }))
  expect(await $.session.start({ cwd: '/w', surface: null, isInteractive: false })).toEqual({ cwd: '/w' })
  expect(await $.session.measure({ context: CONTEXT, rateLimits: LIMITS, changed: ['rateLimits'] }))
    .toEqual({ changed: ['rateLimits'] })
})
