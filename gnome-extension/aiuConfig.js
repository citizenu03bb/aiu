// SPDX-License-Identifier: GPL-3.0-or-later
// aiuConfig.js - What the preferences window needs from aiu, without Gtk:
// `aiu config show --json` read into rows, the `aiu config set` command line,
// the status from a snapshot, the refresh interval, and AiuRunner, which runs
// aiu and never leaves it behind. extension.js imports it too, so nothing
// from Gtk, Adw, St or Main here.
//
// aiu is the only program that reads or writes config.toml. The window only
// says *where* aiu finds a key (a variable name, a command, a file), never the
// key itself, so no field here ever holds a secret.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import { displayName, schemaProblem } from './aiuClient.js';
import { _, N_, fmt } from './i18n.js';

/** GSettings schema of the extension-only settings (schemas/). */
export const SCHEMA_ID = 'org.gnome.shell.extensions.aiu-indicator';
export const REFRESH_KEY = 'refresh-interval';
/** Same default and range as the schema: tests/test_aiuConfig.js checks. */
export const REFRESH_DEFAULT_SECONDS = 300;
export const REFRESH_MIN_SECONDS = 60;
export const REFRESH_MAX_SECONDS = 86400;

/** `aiu config` reads a small file: it answers in well under a second. */
export const CONFIG_TIMEOUT_SECONDS = 15;

/** Options that make aiu run a program: read-only when the config fails aiu's trust check. */
const RUNS_CODE = ['key_cmd', 'claude_cmd'];

/** Field titles, by option; any other option aiu lists shows under its own name. */
const OPTION_TITLES = {
    key_env: N_('Environment variable (name only)'),
    key_cmd: N_('Command that prints the key'),
    key_file: N_('File that holds the key'),
    claude_cmd: N_('claude program'),
    vibe_allowance_eur: N_('Vibe allowance (EUR)'),
    api_allowance_eur: N_('API allowance (EUR)'),
};

/** The refresh interval in seconds, within the schema's range; the default when unreadable. */
export function clampInterval(seconds) {
    const s = Math.round(Number(seconds));
    if (!Number.isFinite(s)) {
        return REFRESH_DEFAULT_SECONDS;
    }
    return Math.min(REFRESH_MAX_SECONDS, Math.max(REFRESH_MIN_SECONDS, s));
}

/** The interval as the window shows it: whole minutes, at least 1. */
export function intervalMinutes(seconds) {
    return Math.max(1, Math.round(clampInterval(seconds) / 60));
}

export function optionTitle(option) {
    return option in OPTION_TITLES ? _(OPTION_TITLES[option]) : option;
}

function valueText(value) {
    return value === null || value === undefined ? '' : String(value);
}

/** aiu printed a `schema` this extension does not read: the message says which to update. */
export class SchemaError extends Error {}

/**
 * `aiu config show --json` as the window lays it out: one entry per provider,
 * in aiu's order, with its `enabled` value and the text fields it takes
 * (key sources, claude_cmd, Mistral's allowances). Throws on anything else,
 * a SchemaError for an unknown schema.
 */
export function parseConfigShow(text) {
    const raw = JSON.parse(text);
    const problem = schemaProblem(raw);
    if (problem) {
        throw new SchemaError(problem);
    }
    if (!raw || typeof raw !== 'object' || !raw.providers || typeof raw.providers !== 'object') {
        throw new Error(_('aiu config show listed no providers'));
    }
    const keyCmdAllowed = raw.key_cmd_allowed !== false;
    const providers = Object.entries(raw.providers).map(([id, p]) => {
        const options = (p && typeof p.options === 'object' && p.options) || {};
        const fields = Object.entries(options)
            .filter(([name, o]) => name !== 'enabled' && o && (o.type === 'string' || o.type === 'number'))
            .map(([name, o]) => ({
                option: name,
                type: o.type,
                text: valueText(o.value),
                fromFile: o.source === 'file',
                editable: keyCmdAllowed || !RUNS_CODE.includes(name),
            }));
        return {
            id,
            name: displayName(id),
            enabled: options.enabled ? options.enabled.value === true : null,
            hasKeySources: 'key_env' in options || 'key_cmd' in options || 'key_file' in options,
            fields,
        };
    });
    return {
        path: typeof raw.path === 'string' ? raw.path : null,
        keyCmdAllowed,
        keyCmdProblems: Array.isArray(raw.key_cmd_problems) ? raw.key_cmd_problems.map(String) : [],
        providers,
    };
}

/**
 * Text that looks like a key rather than where to find one: a known key
 * prefix, or one long token mixing lower case and digits (variable names are
 * upper case, commands and paths have spaces or slashes).
 */
export function looksLikeSecret(text) {
    const v = text.trim();
    if (/^(sk-|sk_|AIza|ghp_|gho_|github_pat_|xox[abp]-|hf_)/.test(v)) {
        return true;
    }
    return v.length >= 32 && !/[\s/]/.test(v) && /[a-z]/.test(v) && /[0-9]/.test(v);
}

/**
 * The value to hand `aiu config set` for what was typed in a field, or why not.
 * aiu checks it again; this only keeps keys out of config.toml and accepts a
 * decimal comma.
 */
export function fieldValue(field, text) {
    const v = text.trim();
    if (looksLikeSecret(v)) {
        return { secret: true, error: _('This looks like a key. Enter where aiu finds it (a variable name, a command or a file), never the key itself.') };
    }
    if (field.type === 'number') {
        return { value: v.replace(',', '.') };
    }
    return { value: v };
}

/** argv of `aiu config set`; `--` so that a value starting with "-" stays a value. */
export function setArgv(aiu, provider, option, value) {
    return [aiu, 'config', 'set', '--', `${provider}.${option}`, String(value)];
}

export function showArgv(aiu) {
    return [aiu, 'config', 'show', '--json'];
}

/**
 * A provider's state in the last snapshot (`aiu --json`): `off`, `checking`
 * (no snapshot yet), `unknown` (the snapshot failed), `ok` (aiu read
 * something), `error` with the probe's note, or `unread` (turned on since).
 */
export function providerStatus(provider, snapshot) {
    if (provider.enabled === false) {
        return { state: 'off' };
    }
    if (!snapshot) {
        return { state: 'checking' };
    }
    if (!Array.isArray(snapshot.rows)) {
        return { state: 'unknown' };
    }
    const rows = snapshot.rows.filter(r => r && r.provider === provider.id);
    if (rows.some(r => r.kind !== 'error')) {
        return { state: 'ok' };
    }
    const error = rows.find(r => r.kind === 'error');
    if (error) {
        return { state: 'error', note: String(error.note || _('error')) };
    }
    return { state: 'unread' };
}

/** The line under a provider's name. Notes come from aiu, in English. */
export function statusText(status) {
    switch (status.state) {
    case 'off':
        return _('Off');
    case 'checking':
        return _('Checking…');
    case 'ok':
        return _('Signed in');
    case 'error':
        return status.note;
    case 'unread':
        return _('Not in the last check: check again');
    default:
        return _('Status unknown');
    }
}

/**
 * Runs aiu with an argv list (never a shell) and a timeout. cancelAll(),
 * which the window calls when it closes, kills every aiu still running.
 */
export class AiuRunner {
    constructor() {
        this._runs = new Set(); // { proc, cancellable, watchdogId, timedOut }
    }

    get busy() {
        return this._runs.size > 0;
    }

    /**
     * @returns {Promise<{ok: boolean, stdout?: string, stderr?: string,
     *   error?: string, timedOut?: boolean, cancelled?: boolean}>}
     */
    run(argv, { timeoutSeconds = CONFIG_TIMEOUT_SECONDS } = {}) {
        return new Promise((resolve) => {
            const cancellable = new Gio.Cancellable();
            let proc;
            try {
                proc = new Gio.Subprocess({
                    argv,
                    flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
                });
                proc.init(cancellable);
            } catch (err) {
                resolve({ ok: false, error: fmt(_('Could not start aiu: {error}'), { error: err.message }) });
                return;
            }
            const run = { proc, cancellable, watchdogId: 0, timedOut: false };
            this._runs.add(run);
            run.watchdogId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, timeoutSeconds, () => {
                run.watchdogId = 0;
                run.timedOut = true;
                this._stop(run);
                return GLib.SOURCE_REMOVE;
            });

            proc.communicate_utf8_async(null, cancellable, (p, res) => {
                this._forget(run);
                try {
                    const [, stdout, stderr] = p.communicate_utf8_finish(res);
                    const ok = p.get_successful();
                    const error = ok ? undefined
                        : (stderr && stderr.trim()) || fmt(_('aiu stopped with status {status}'), { status: p.get_exit_status() });
                    resolve({ ok, stdout: stdout || '', stderr: stderr || '', error });
                } catch (err) {
                    if (run.timedOut) {
                        resolve({
                            ok: false, timedOut: true,
                            error: fmt(_('aiu did not answer within {seconds} s, stopped'), { seconds: timeoutSeconds }),
                        });
                    } else if (err.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED)) {
                        resolve({ ok: false, cancelled: true });
                    } else {
                        resolve({ ok: false, error: err.message });
                    }
                }
            });
        });
    }

    /** Kill every aiu still running; their promises resolve with `cancelled`. */
    cancelAll() {
        for (const run of [...this._runs]) {
            this._stop(run);
        }
    }

    /** Cancelling alone would leave aiu running: kill it too. */
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
        this._runs.delete(run);
    }
}
