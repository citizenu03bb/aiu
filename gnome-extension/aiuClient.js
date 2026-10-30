// SPDX-License-Identifier: GPL-3.0-or-later
// aiuClient.js - Asynchronous client for aiu CLI

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export class AiuClient {
    constructor() {
        this._cancellable = null;
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

        const aiuScript = GLib.build_filenamev([home, 'Projects', 'ai-usage', 'aiu.py']);
        if (GLib.file_test(aiuScript, GLib.FileTest.EXISTS)) {
            const python = GLib.find_program_in_path('python3') || 'python3';
            return [python, aiuScript, '--json'];
        }

        throw new Error("Impossible de localiser 'aiu' dans ~/.local/bin/aiu ou ~/Projects/ai-usage/aiu.py");
    }

    cancel() {
        if (this._cancellable) {
            this._cancellable.cancel();
            this._cancellable = null;
        }
    }

    /**
     * Fetch aiu snapshot asynchronously without blocking GNOME Shell main loop.
     * @returns {Promise<{success: boolean, data?: object, error?: string}>}
     */
    fetchSnapshot() {
        return new Promise((resolve) => {
            this.cancel();
            this._cancellable = new Gio.Cancellable();

            let argv;
            try {
                argv = this._resolveCommand();
            } catch (err) {
                resolve({ success: false, error: err.message });
                return;
            }

            let proc;
            try {
                proc = new Gio.Subprocess({
                    argv: argv,
                    flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
                });
                proc.init(this._cancellable);
            } catch (err) {
                resolve({ success: false, error: `Échec lancement aiu: ${err.message}` });
                return;
            }

            proc.communicate_utf8_async(null, this._cancellable, (subprocess, res) => {
                this._cancellable = null;
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
                    if (err.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED)) {
                        // Execution was intentionally cancelled
                        return;
                    }
                    resolve({ success: false, error: err.message });
                }
            });
        });
    }

    _processData(raw) {
        const rows = raw.rows || [];
        const capturedAt = raw.captured_at || new Date().toISOString();

        // Categorize rows
        const quotas = [];
        const balances = [];
        const errors = [];

        let worstQuotaPct = 0;
        let hasCriticalQuota = false;
        let hasWarningQuota = false;
        let hasError = false;

        for (const row of rows) {
            if (row.kind === 'error') {
                errors.push(row);
                hasError = true;
            } else if (row.kind === 'quota') {
                const usedPct = typeof row.used_pct === 'number'
                    ? row.used_pct
                    : (typeof row.used === 'number' ? Math.round(row.used * 1000) / 10 : 0);

                const item = {
                    ...row,
                    used_pct: usedPct,
                };
                quotas.push(item);

                if (usedPct > worstQuotaPct) {
                    worstQuotaPct = usedPct;
                }
                if (usedPct >= 95) {
                    hasCriticalQuota = true;
                } else if (usedPct >= 75) {
                    hasWarningQuota = true;
                }
            } else if (row.kind === 'balance' || row.kind === 'spend' || row.kind === 'credit') {
                balances.push(row);
            }
        }

        // Compute overall status color
        let statusColor = 'normal';
        if (hasError || hasCriticalQuota) {
            statusColor = 'critical';
        } else if (hasWarningQuota) {
            statusColor = 'warning';
        }

        // Routing advice comes from aiu itself (earliest-expiring surplus first)
        const advice = this._generateAdvice(raw.advice);

        return {
            capturedAt,
            quotas,
            balances,
            errors,
            statusColor,
            worstQuotaPct,
            advice,
        };
    }

    _formatDuration(secs) {
        const s = Math.max(0, Math.floor(secs));
        const d = Math.floor(s / 86400);
        const h = Math.floor((s % 86400) / 3600);
        const m = Math.floor((s % 3600) / 60);
        if (d) return `${d}j${h}h`;
        if (h) return `${h}h${String(m).padStart(2, '0')}`;
        return `${m}min`;
    }

    /**
     * Summarize aiu's "advice" list (already in routing order: burn / unknown
     * pace by deadline, then on_track, then conserve) into one line.
     */
    _generateAdvice(lots) {
        if (!Array.isArray(lots) || lots.length === 0) {
            return null;
        }

        const keep = [...new Set(lots.filter(l => l.status === 'conserve').map(l => l.provider))];
        const spare = keep.length ? ` · ménage ${keep.join(', ')}` : '';
        const first = lots[0];

        if (first.status === 'burn' || first.status === '?') {
            const when = typeof first.ttr_secs === 'number'
                ? ` expire dans ${this._formatDuration(first.ttr_secs)}`
                : '';
            const unsure = first.status === '?' ? ' (allure inconnue)' : '';
            return `💡 Brûle d'abord ${first.provider} : ${first.metric}${when}${unsure}${spare}`;
        }

        if (first.status === 'on_track') {
            return `💡 Pas de surplus qui expire : route par fit de tâche${spare}`;
        }

        return `💡 Aucun forfait n'a de surplus : réserve chacun à ce que lui seul sait faire, déborde sur l'API payante`;
    }
}
