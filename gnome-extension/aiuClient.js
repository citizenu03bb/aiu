// SPDX-License-Identifier: GPL-3.0-or-later
// aiuClient.js - Asynchronous client for aiu CLI

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import { _, fmt } from './i18n.js';

const DISPLAY_NAMES = {
    claude: 'Claude',
    codex: 'Codex',
    gemini: 'Gemini',
    deepseek: 'DeepSeek',
    openrouter: 'OpenRouter',
    openai: 'OpenAI',
    mistral: 'Mistral',
};

export function displayName(provider) {
    return DISPLAY_NAMES[provider] || provider;
}

/** Compact duration: 4d22h, 3h08m, 12m (French: 4j22h, 3h08, 12min). */
export function formatDuration(secs) {
    const s = Math.max(0, Math.floor(secs));
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d) return fmt(_('{d}d{h}h'), { d, h });
    if (h) return fmt(_('{h}h{m}m'), { h, m: String(m).padStart(2, '0') });
    return fmt(_('{m}m'), { m });
}

const UNIT_SECS = { d: 86400, h: 3600, m: 60 };

/**
 * Time to a row's reset, recomputed from reset_epoch so it stays right while
 * the menu sits open; else read from aiu's text ("in 2d 3h", "expires in 5m",
 * "now"). Text in any other shape is shown as aiu wrote it.
 */
export function resetText(row, nowSecs = Date.now() / 1000) {
    let ttr;
    if (typeof row.reset_epoch === 'number') {
        ttr = row.reset_epoch - nowSecs;
    } else if (!row.reset) {
        return null;
    } else if (row.reset === 'now') {
        ttr = 0;
    } else {
        const m = /^(?:expires )?in ((?:\d+[dhm] ?)+)$/.exec(row.reset);
        if (!m) return row.reset;
        ttr = 0;
        for (const [, n, unit] of m[1].matchAll(/(\d+)([dhm])/g)) {
            ttr += Number(n) * UNIT_SECS[unit];
        }
    }
    return ttr > 0
        ? fmt(_('in {duration}'), { duration: formatDuration(ttr) })
        : _('passed, refresh the snapshot');
}

/** A fetch still running after this long is killed (the probes' own timeouts are 15-60 s). */
export const FETCH_TIMEOUT_SECONDS = 120;

const AIU_SOURCE = 'git+https://github.com/citizenu03bb/aiu';
export const INSTALL_GUIDE_URL = 'https://github.com/citizenu03bb/aiu#install';

/**
 * The command that installs aiu with the tool this machine has: uv, else pipx.
 * Both put it in ~/.local/bin, where the client looks first. `tool` is null
 * when neither is installed (the command then needs pipx first).
 * The extension only shows and copies it: it never installs anything itself.
 */
export function installCommand(hasProgram = name => GLib.find_program_in_path(name) !== null) {
    if (hasProgram('uv')) {
        return { tool: 'uv', command: `uv tool install ${AIU_SOURCE}` };
    }
    return { tool: hasProgram('pipx') ? 'pipx' : null, command: `pipx install ${AIU_SOURCE}` };
}

/** aiu answered but read nothing: no provider is set up or signed in yet. */
export function needsSetup(data) {
    return !!data && data.quotas.length === 0 && data.balances.length === 0;
}

export class AiuClient {
    constructor({ timeoutSeconds = FETCH_TIMEOUT_SECONDS } = {}) {
        this._timeoutSeconds = timeoutSeconds;
        this._run = null; // { proc, cancellable, watchdogId, timedOut } of the fetch in flight
    }

    /** The aiu command line, or null when aiu is not installed. */
    _resolveCommand() {
        const home = GLib.get_home_dir();
        const localBin = GLib.build_filenamev([home, '.local', 'bin', 'aiu']);
        if (GLib.file_test(localBin, GLib.FileTest.IS_EXECUTABLE)) {
            return [localBin, '--json'];
        }

        const pathBin = GLib.find_program_in_path('aiu');
        return pathBin ? [pathBin, '--json'] : null;
    }

    /** Stop the fetch in flight, if any: its promise resolves with `cancelled`, its aiu is killed. */
    cancel() {
        if (this._run) {
            this._stop(this._run);
        }
    }

    /** Kill a fetch's aiu and stop waiting for it. Cancelling alone would leave aiu running. */
    _stop(run) {
        this._forget(run);
        run.cancellable.cancel();
        run.proc.force_exit();
    }

    _forget(run) {
        if (run.watchdogId) {
            GLib.source_remove(run.watchdogId);
            run.watchdogId = 0;
        }
        if (this._run === run) {
            this._run = null;
        }
    }

    /**
     * Fetch aiu snapshot asynchronously without blocking GNOME Shell main loop.
     * A newer fetch, cancel() or the watchdog kills the aiu process.
     * `missing` means aiu is not installed: the menu then shows how to install it.
     * @returns {Promise<{success: boolean, data?: object, error?: string, cancelled?: boolean, missing?: boolean}>}
     */
    fetchSnapshot() {
        return new Promise((resolve) => {
            this.cancel();

            const argv = this._resolveCommand();
            if (!argv) {
                resolve({ success: false, missing: true });
                return;
            }

            const cancellable = new Gio.Cancellable();
            let proc;
            try {
                proc = new Gio.Subprocess({
                    argv: argv,
                    flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
                });
                proc.init(cancellable);
            } catch (err) {
                resolve({ success: false, error: fmt(_('Could not start aiu: {error}'), { error: err.message }) });
                return;
            }

            const run = { proc, cancellable, watchdogId: 0, timedOut: false };
            this._run = run;
            run.watchdogId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, this._timeoutSeconds, () => {
                run.watchdogId = 0;
                run.timedOut = true;
                this._stop(run);
                return GLib.SOURCE_REMOVE;
            });

            proc.communicate_utf8_async(null, cancellable, (subprocess, res) => {
                this._forget(run);
                try {
                    const [, stdout, stderr] = subprocess.communicate_utf8_finish(res);

                    if (!stdout || stdout.trim().length === 0) {
                        const errMsg = (stderr && stderr.trim()) || _('aiu printed nothing');
                        resolve({ success: false, error: errMsg });
                        return;
                    }

                    let parsed;
                    try {
                        parsed = JSON.parse(stdout);
                    } catch (parseErr) {
                        resolve({
                            success: false,
                            error: `${fmt(_('aiu returned invalid JSON: {error}'), { error: parseErr.message })}\n${stdout.slice(0, 150)}`
                        });
                        return;
                    }

                    const processed = this._processData(parsed);
                    resolve({ success: true, data: processed });
                } catch (err) {
                    if (run.timedOut) {
                        resolve({
                            success: false,
                            error: fmt(_('aiu did not answer within {seconds} s, stopped'), { seconds: this._timeoutSeconds }),
                        });
                    } else if (err.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED)) {
                        // Superseded by a newer fetch, or the extension was disabled
                        resolve({ success: false, cancelled: true });
                    } else {
                        resolve({ success: false, error: err.message });
                    }
                }
            });
        });
    }

    _processData(raw) {
        const rows = raw.rows || [];
        const capturedAt = raw.captured_at || new Date().toISOString();
        const routing = Array.isArray(raw.advice) ? raw.advice : [];

        const quotas = [];
        const balances = [];
        const errors = [];
        let worstQuotaPct = 0;

        for (const row of rows) {
            if (row.kind === 'error') {
                errors.push(row);
            } else if (row.kind === 'quota') {
                // An unreadable quota stays unknown (null), never "0% used".
                const usedPct = typeof row.used_pct === 'number'
                    ? row.used_pct
                    : (typeof row.used === 'number' ? Math.round(row.used * 1000) / 10 : null);
                quotas.push({ ...row, used_pct: usedPct });
                if (usedPct !== null && usedPct > worstQuotaPct) {
                    worstQuotaPct = usedPct;
                }
            } else if (row.kind === 'balance' || row.kind === 'spend' || row.kind === 'credit') {
                balances.push(row);
            }
        }

        return {
            capturedAt,
            quotas,
            balances,
            errors,
            routing,
            statusColor: this._statusColor(quotas, errors, routing),
            worstQuotaPct,
            advice: this._generateAdvice(routing),
        };
    }

    /**
     * Panel dot = "is there something to act on?"
     * critical: a probe failed or a window is (nearly) exhausted right now;
     * warning: free subscription quota will be lost at a reset within 24h.
     */
    _statusColor(quotas, errors, routing) {
        if (errors.length > 0 || quotas.some(q => q.used_pct !== null && q.used_pct >= 95)) {
            return 'critical';
        }
        const expiringSoon = routing.some(l => l.status === 'burn' && l.leftover !== null
            && typeof l.ttr_secs === 'number' && l.ttr_secs <= 24 * 3600);
        return expiringSoon ? 'warning' : 'normal';
    }

    /**
     * Summarize aiu's "advice" list (already in routing order: burn / unknown
     * pace by deadline, then on_track, then conserve). First line is the
     * headline, the following lines are details.
     */
    _generateAdvice(lots) {
        if (!Array.isArray(lots) || lots.length === 0) {
            return null;
        }

        const keep = [...new Set(lots.filter(l => l.status === 'conserve').map(l => displayName(l.provider)))];
        const spare = keep.length ? fmt(_('Conserve: {providers}'), { providers: keep.join(', ') }) : null;
        const first = lots[0];
        const lines = [];

        if (first.status === 'burn' || first.status === '?') {
            lines.push(fmt(_('💡 Burn {provider} first'), { provider: displayName(first.provider) }));
            const duration = typeof first.ttr_secs === 'number' ? formatDuration(first.ttr_secs) : null;
            const fields = { metric: first.metric, duration };
            let detail = first.metric;
            if (first.status === '?') {
                detail = duration
                    ? fmt(_('{metric}: reset in {duration} (unknown pace)'), fields)
                    : fmt(_('{metric} (unknown pace)'), fields);
            } else if (typeof first.leftover === 'number') {
                fields.percent = Math.round(first.leftover * 100);
                detail = duration
                    ? fmt(_('{metric}: ~{percent}% lost at reset in {duration}'), fields)
                    : fmt(_('{metric}: ~{percent}% lost at reset'), fields);
            } else if (duration) {
                detail = fmt(_('{metric}: expires in {duration}'), fields);
            }
            lines.push(detail);
        } else if (first.status === 'on_track') {
            lines.push(_('💡 No expiring surplus: route by task fit'));
        } else {
            lines.push(_('💡 No subscription has surplus'));
            lines.push(_('Keep each for what only it can do, overflow to the paid API'));
        }
        if (spare && first.status !== 'conserve') {
            lines.push(spare);
        }
        return lines.join('\n');
    }
}
