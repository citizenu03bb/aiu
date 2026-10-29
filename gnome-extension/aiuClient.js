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

        // Collect per-provider min free headroom for advice
        const providerHeadroom = {};

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

                const freePct = Math.max(0, 100 - usedPct);
                if (providerHeadroom[row.provider] === undefined || freePct < providerHeadroom[row.provider]) {
                    providerHeadroom[row.provider] = freePct;
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

        // Generate routing advice
        const advice = this._generateAdvice(providerHeadroom, quotas, balances, errors);

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

    _generateAdvice(providerHeadroom, quotas, balances, errors) {
        const entries = Object.entries(providerHeadroom);
        if (entries.length === 0) {
            return null;
        }

        // Sort descending by headroom free
        entries.sort((a, b) => b[1] - a[1]);

        const best = entries[0];
        const worst = entries[entries.length - 1];

        if (worst[1] <= 5 && best[1] > 20) {
            return `💡 ${worst[0]} saturé (${Math.round(100 - worst[1])}%) → basculez vers ${best[0]} (${Math.round(best[1])}% libre)`;
        }

        if (best[1] > 0) {
            return `💡 Forfait recommandé : ${best[0]} (${Math.round(best[1])}% de marge disponible)`;
        }

        return null;
    }
}
