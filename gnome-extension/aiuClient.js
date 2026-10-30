// SPDX-License-Identifier: GPL-3.0-or-later
// aiuClient.js - Asynchronous client for aiu CLI

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

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

/** Compact French duration: 3h08, 4j22h, 12min. */
export function formatDuration(secs) {
    const s = Math.max(0, Math.floor(secs));
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d) return `${d}j${h}h`;
    if (h) return `${h}h${String(m).padStart(2, '0')}`;
    return `${m}min`;
}

/**
 * Time to a row's reset, recomputed from reset_epoch so it stays right while
 * the menu sits open; falls back to aiu's English text, translated.
 */
export function resetText(row, nowSecs = Date.now() / 1000) {
    if (typeof row.reset_epoch === 'number') {
        const ttr = row.reset_epoch - nowSecs;
        return ttr > 0 ? `dans ${formatDuration(ttr)}` : 'passé, relevé à rafraîchir';
    }
    if (!row.reset) return null;
    return row.reset
        .replace(/^expires in /, 'dans ')
        .replace(/^in /, 'dans ')
        .replace(/^now$/, 'passé, relevé à rafraîchir')
        .replace(/(\d+)d /g, '$1j ');
}

/** A fetch still running after this long is killed (the probes' own timeouts are 15-60 s). */
export const FETCH_TIMEOUT_SECONDS = 120;

export class AiuClient {
    constructor({ timeoutSeconds = FETCH_TIMEOUT_SECONDS } = {}) {
        this._timeoutSeconds = timeoutSeconds;
        this._run = null; // { proc, cancellable, watchdogId, timedOut } of the fetch in flight
    }

    _resolveCommand() {
        const home = GLib.get_home_dir();
        const localBin = GLib.build_filenamev([home, '.local', 'bin', 'aiu']);
        if (GLib.file_test(localBin, GLib.FileTest.IS_EXECUTABLE)) {
            return [localBin, '--json'];
        }

        const pathBin = GLib.find_program_in_path('aiu');
        if (pathBin) {
            return [pathBin, '--json'];
        }

        throw new Error("Impossible de localiser 'aiu' (ni ~/.local/bin/aiu, ni dans le PATH)");
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
     * @returns {Promise<{success: boolean, data?: object, error?: string, cancelled?: boolean}>}
     */
    fetchSnapshot() {
        return new Promise((resolve) => {
            this.cancel();

            let argv;
            try {
                argv = this._resolveCommand();
            } catch (err) {
                resolve({ success: false, error: err.message });
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
                resolve({ success: false, error: `Échec lancement aiu: ${err.message}` });
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
                        const errMsg = (stderr && stderr.trim()) || "Sortie vide de la commande aiu";
                        resolve({ success: false, error: errMsg });
                        return;
                    }

                    let parsed;
                    try {
                        parsed = JSON.parse(stdout);
                    } catch (parseErr) {
                        resolve({
                            success: false,
                            error: `JSON invalide retourné par aiu: ${parseErr.message}\n${stdout.slice(0, 150)}`
                        });
                        return;
                    }

                    const processed = this._processData(parsed);
                    resolve({ success: true, data: processed });
                } catch (err) {
                    if (run.timedOut) {
                        resolve({ success: false, error: `aiu n'a pas répondu en ${this._timeoutSeconds} s, arrêté` });
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
        const spare = keep.length ? `À ménager : ${keep.join(', ')}` : null;
        const first = lots[0];
        const lines = [];

        if (first.status === 'burn' || first.status === '?') {
            lines.push(`💡 Brûle d'abord ${displayName(first.provider)}`);
            const when = typeof first.ttr_secs === 'number' ? formatDuration(first.ttr_secs) : null;
            let detail = first.metric;
            if (first.status === '?') {
                detail += when ? ` : reset dans ${when} (allure inconnue)` : ' (allure inconnue)';
            } else if (typeof first.leftover === 'number') {
                detail += ` : ~${Math.round(first.leftover * 100)} % perdu au reset${when ? ` dans ${when}` : ''}`;
            } else if (when) {
                detail += ` : expire dans ${when}`;
            }
            lines.push(detail);
        } else if (first.status === 'on_track') {
            lines.push('💡 Pas de surplus qui expire : route par fit de tâche');
        } else {
            lines.push("💡 Aucun forfait n'a de surplus");
            lines.push("Réserve chacun à ce que lui seul sait faire, déborde sur l'API payante");
        }
        if (spare && first.status !== 'conserve') {
            lines.push(spare);
        }
        return lines.join('\n');
    }
}
