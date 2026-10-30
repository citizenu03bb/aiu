// SPDX-License-Identifier: GPL-3.0-or-later
// prefsWindow.js - The preferences window's pages. Gtk 4 and Adwaita only:
// it runs in the Extensions app, never in GNOME Shell. prefs.js hands it the
// window; it is a module of its own so it can also be built outside that app.
//
// Every provider setting is read with `aiu config show --json` and written
// with `aiu config set`, one at a time, then read back. No field ever holds a
// key: they say where aiu finds one. No clipboard.

import Adw from 'gi://Adw?version=1';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk?version=4.0';
import { FETCH_TIMEOUT_SECONDS, findAiu, INSTALL_GUIDE_URL, installCommand, schemaProblem } from './aiuClient.js';
import {
    AiuRunner, clampInterval, fieldValue, intervalMinutes, optionTitle, parseConfigShow,
    providerStatus, REFRESH_KEY, REFRESH_MAX_SECONDS, SchemaError, setArgv, showArgv, statusText,
} from './aiuConfig.js';
import { _, fmt } from './i18n.js';

/** A text field is saved this long after the last key stroke, or at once on Enter. */
const SAVE_DELAY_MS = 1000;
/** On close, the last changes get this long to reach config.toml before aiu is killed. */
const CLOSE_GRACE_MS = 5000;
/** Where aiu keeps its config, for Open config file when aiu cannot say. */
const DEFAULT_CONFIG = ['.config', 'ai-usage', 'config.toml'];

/** A row whose title and subtitle are plain text, never Pango markup: they show aiu's messages. */
function plainRow(Klass, props = {}) {
    return new Klass({ use_markup: false, ...props });
}

function button(label, onClicked) {
    const b = new Gtk.Button({ label, valign: Gtk.Align.CENTER });
    b.connect('clicked', onClicked);
    return b;
}

function keySourceHelp(provider) {
    return fmt(_('aiu takes the key from the first of these that gives one: the variable (from your session or ~/.env), the command, then the file. Better than ~/.env, the GNOME keyring: store the key once with secret-tool store --label="aiu {provider}" service aiu provider {provider}, then set the command to secret-tool lookup service aiu provider {provider}'),
        { provider });
}

function signInHelp(provider) {
    if (provider === 'claude') {
        return _('Nothing to enter here: aiu reads the sign-in of the claude command-line tool. Run claude once to sign in. Set the program only if claude is installed where aiu does not look.');
    }
    return fmt(_('Nothing to enter here: aiu reads the sign-in of the {provider} command-line tool. Run it once to sign in.'),
        { provider });
}

export class PrefsController {
    /**
     * @param {Adw.PreferencesWindow} window
     * @param {object} options settings: the extension's Gio.Settings (null to
     *   leave the interval out); findProgram: where aiu is, or null.
     */
    constructor(window, { settings = null, findProgram = findAiu, runner = new AiuRunner() } = {}) {
        this._window = window;
        this._settings = settings;
        this._findProgram = findProgram;
        this._runner = runner;
        this._aiu = null;
        this._config = null; // parseConfigShow()
        this._snapshot = null; // aiu --json, { rows: null } when it failed, null while running
        this._checking = false;
        this._providers = new Map(); // id -> { provider, expander, toggle, errorRow, fields }
        this._queue = new Map(); // 'provider.option' -> change waiting for `aiu config set`
        this._saving = null; // the change aiu is writing
        this._generation = 0; // counts finished sets: a show started before one is stale
        this._updating = false; // set while the window fills in values, so they are not saved back
        this._closing = false;
        this._destroyed = false;
        this._sources = new Set();

        window.add(this._buildProvidersPage());
        window.add(this._buildGeneralPage());
        this._closeId = window.connect('close-request', () => this._onCloseRequest());
        this._destroyId = window.connect('destroy', () => this.destroy());
        this.reload();
    }

    _buildProvidersPage() {
        const page = new Adw.PreferencesPage({ title: _('Providers'), icon_name: 'view-list-symbolic' });

        // What keeps the providers from showing or from being changed.
        this._notices = new Adw.PreferencesGroup({ visible: false });
        this._missingRow = plainRow(Adw.ActionRow, { title: _('aiu is not installed'), subtitle_selectable: true });
        this._missingRow.add_suffix(new Gtk.LinkButton({
            uri: INSTALL_GUIDE_URL, label: _('Install guide'), valign: Gtk.Align.CENTER,
        }));
        this._missingRow.add_suffix(button(_('Check again'), () => this.reload()));
        this._showErrorRow = plainRow(Adw.ActionRow, { title: _('aiu could not read its config'), subtitle_selectable: true });
        this._showErrorRow.add_suffix(button(_('Try again'), () => this.reload()));
        this._trustRow = plainRow(Adw.ActionRow, { title: _('Commands are read-only'), subtitle_selectable: true });
        this._statusErrorRow = plainRow(Adw.ActionRow, { title: _('Could not check the status'), subtitle_selectable: true });
        const rows = [this._missingRow, this._showErrorRow, this._trustRow, this._statusErrorRow];
        for (const row of rows) {
            row.visible = false;
            row.connect('notify::visible', () => {
                this._notices.visible = rows.some(r => r.visible);
            });
            this._notices.add(row);
        }
        page.add(this._notices);

        this._providersGroup = new Adw.PreferencesGroup({
            title: _('Providers'),
            description: _('Turn on the subscriptions you pay for, and say where aiu finds each key. aiu saves each change to its config file.'),
            visible: false,
        });
        this._checkButton = new Gtk.Button({
            icon_name: 'view-refresh-symbolic', tooltip_text: _('Check the status again'),
            valign: Gtk.Align.CENTER, css_classes: ['flat'],
        });
        this._checkButton.connect('clicked', () => this._checkStatus());
        this._providersGroup.set_header_suffix(this._checkButton);
        page.add(this._providersGroup);
        return page;
    }

    _buildGeneralPage() {
        const page = new Adw.PreferencesPage({ title: _('General'), icon_name: 'preferences-system-symbolic' });

        const panel = new Adw.PreferencesGroup({ title: _('Panel') });
        const interval = plainRow(Adw.SpinRow, {
            title: _('Refresh every (minutes)'),
            subtitle: _('How often aiu runs in the background. Opening the menu also refreshes data older than 2 minutes.'),
            adjustment: new Gtk.Adjustment({
                lower: 1, upper: REFRESH_MAX_SECONDS / 60, step_increment: 1, page_increment: 10,
            }),
        });
        if (this._settings) {
            interval.value = intervalMinutes(this._settings.get_uint(REFRESH_KEY));
            interval.connect('notify::value', () => {
                const seconds = clampInterval(interval.value * 60);
                if (seconds !== this._settings.get_uint(REFRESH_KEY)) {
                    this._settings.set_uint(REFRESH_KEY, seconds);
                }
            });
            this._settingsId = this._settings.connect(`changed::${REFRESH_KEY}`, () => {
                interval.value = intervalMinutes(this._settings.get_uint(REFRESH_KEY));
            });
        } else {
            interval.sensitive = false;
        }
        panel.add(interval);
        page.add(panel);

        const files = new Adw.PreferencesGroup({ title: _('aiu command') });
        this._programRow = plainRow(Adw.ActionRow, { title: _('Program'), subtitle_selectable: true });
        this._guideButton = new Gtk.LinkButton({
            uri: INSTALL_GUIDE_URL, label: _('Install guide'), valign: Gtk.Align.CENTER,
        });
        this._programRow.add_suffix(this._guideButton);
        this._fileRow = plainRow(Adw.ActionRow, { title: _('Config file'), subtitle_selectable: true });
        this._openButton = button(_('Open config file'), () => this._openConfig());
        this._fileRow.add_suffix(this._openButton);
        files.add(this._programRow);
        files.add(this._fileRow);
        page.add(files);
        return page;
    }

    /** Find aiu, read its config and check the status: on open, and from Check again. */
    reload() {
        if (this._destroyed) {
            return;
        }
        this._aiu = this._findProgram();
        this._missingRow.visible = !this._aiu;
        this._missingRow.subtitle = fmt(_('The preferences go through aiu. Install it with: {command}'),
            { command: installCommand().command });
        this._programRow.subtitle = this._aiu || _('Not installed');
        this._guideButton.visible = !this._aiu;
        this._showErrorRow.visible = false;
        this._updateFileRow();
        if (!this._aiu) {
            this._providersGroup.visible = false;
            this._trustRow.visible = false;
            this._statusErrorRow.visible = false;
            return;
        }
        this._readConfig();
        if (!this._checking) {
            this._checkStatus();
        }
    }

    async _readConfig() {
        const generation = this._generation;
        const res = await this._runner.run(showArgv(this._aiu));
        if (res.cancelled || this._destroyed) {
            return;
        }
        if (generation !== this._generation || this._saving || this._queue.size) {
            return; // a change was saved meanwhile: the read after it is the one to show
        }
        let config = null;
        let error = res.error;
        if (/unrecognized arguments|invalid choice/.test(error || '')) {
            error = _('This aiu has no config command yet: update it (uv tool upgrade aiu, or pipx upgrade aiu), then check again.');
        } else if (res.ok) {
            try {
                config = parseConfigShow(res.stdout);
            } catch (err) {
                error = err instanceof SchemaError ? err.message
                    : fmt(_('aiu config show printed something else: {error}'), { error: err.message });
            }
        }
        this._showErrorRow.visible = !config;
        if (!config) {
            this._showErrorRow.subtitle = error;
            this._providersGroup.visible = false;
            this._trustRow.visible = false;
            return;
        }
        this._config = config;
        this._applyConfig();
    }

    _applyConfig() {
        const config = this._config;
        const ids = config.providers.map(p => p.id).join();
        if (ids !== [...this._providers.keys()].join()) {
            for (const ui of this._providers.values()) {
                this._cancelField(ui);
                this._providersGroup.remove(ui.expander);
            }
            this._providers.clear();
            for (const p of config.providers) {
                this._providers.set(p.id, this._buildProvider(p));
            }
        }

        this._updating = true;
        try {
            for (const p of config.providers) {
                const ui = this._providers.get(p.id);
                ui.provider = p;
                ui.toggle.sensitive = p.enabled !== null;
                if (!this._isPending(p.id, 'enabled')) {
                    ui.toggle.active = p.enabled === true;
                }
                for (const field of p.fields) {
                    const f = ui.fields.get(field.option);
                    if (!f) {
                        continue;
                    }
                    f.field = field;
                    f.row.editable = field.editable;
                    if (!f.dirty && !this._isPending(p.id, field.option)) {
                        f.row.text = field.text;
                    }
                }
            }
        } finally {
            this._updating = false;
        }

        this._providersGroup.visible = true;
        this._trustRow.visible = !config.keyCmdAllowed;
        this._trustRow.subtitle = fmt(_('aiu ignores key_cmd and claude_cmd: {reasons}. Fix that, then check again.'),
            { reasons: config.keyCmdProblems.join('; ') });
        this._updateFileRow();
        this._updateStatus();
    }

    _buildProvider(p) {
        const expander = plainRow(Adw.ExpanderRow, { title: p.name });
        const toggle = new Gtk.Switch({ valign: Gtk.Align.CENTER, tooltip_text: _('Read this provider') });
        expander.add_suffix(toggle);
        const ui = { provider: p, expander, toggle, fields: new Map(), errorOption: null };
        toggle.connect('notify::active', () => {
            if (!this._updating) {
                this._enqueue(ui, 'enabled', toggle.active, null);
            }
        });

        for (const field of p.fields) {
            const row = plainRow(Adw.EntryRow, {
                title: optionTitle(field.option),
                input_hints: Gtk.InputHints.NO_SPELLCHECK,
                input_purpose: field.type === 'number' ? Gtk.InputPurpose.NUMBER : Gtk.InputPurpose.FREE_FORM,
            });
            const f = { row, field, dirty: false, timer: 0 };
            row.connect('changed', () => {
                if (this._updating) {
                    return;
                }
                f.dirty = true;
                row.remove_css_class('error');
                this._cancelTimer(f);
                f.timer = this._later(SAVE_DELAY_MS, () => {
                    f.timer = 0;
                    this._saveField(ui, f);
                });
            });
            row.connect('entry-activated', () => this._saveField(ui, f));
            ui.fields.set(field.option, f);
            expander.add_row(row);
        }

        const help = plainRow(Adw.ActionRow, {
            title: p.hasKeySources ? _('Where aiu finds the key') : _('Sign in'),
            subtitle: p.hasKeySources ? keySourceHelp(p.id) : signInHelp(p.id),
            subtitle_selectable: true,
            activatable: false,
        });
        expander.add_row(help);
        ui.errorRow = plainRow(Adw.ActionRow, {
            title: _('Not saved'), subtitle_selectable: true, visible: false, css_classes: ['error'],
        });
        expander.add_row(ui.errorRow);
        this._providersGroup.add(expander);
        return ui;
    }

    _saveField(ui, f) {
        this._cancelTimer(f);
        if (!f.dirty) {
            return;
        }
        const res = fieldValue(f.field, f.row.text);
        if (res.error) {
            if (res.secret) {
                // Never keep a key on screen, even unsaved: back to what aiu has.
                this._setText(f, f.field.text);
                f.dirty = false;
            }
            f.row.add_css_class('error');
            this._showError(ui, res.error, f.field.option);
            return;
        }
        this._enqueue(ui, f.field.option, res.value, f);
    }

    /** Queue one `aiu config set`: one at a time, the latest value of each option. */
    _enqueue(ui, option, value, f) {
        const key = `${ui.provider.id}.${option}`;
        this._queue.delete(key);
        this._queue.set(key, { ui, option, value, f });
        this._pump();
    }

    async _pump() {
        if (this._saving || this._destroyed) {
            return;
        }
        const [entry] = this._queue;
        if (!entry) {
            if (this._closing) {
                this._finishClose();
            } else {
                this._readConfig();
            }
            return;
        }
        const [key, job] = entry;
        this._queue.delete(key);
        this._saving = key;
        const res = await this._runner.run(setArgv(this._aiu, job.ui.provider.id, job.option, job.value));
        this._saving = null;
        if (res.cancelled || this._destroyed) {
            return;
        }
        this._generation++;
        if (res.ok) {
            if (job.option === job.ui.errorOption) {
                job.ui.errorRow.visible = false;
            }
            if (job.f && fieldValue(job.f.field, job.f.row.text).value === job.value) {
                job.f.dirty = false;
            }
        } else {
            // A refused field keeps what was typed, to fix; the read below resets a switch.
            job.f?.row.add_css_class('error');
            this._showError(job.ui, res.error, job.option);
        }
        this._pump();
    }

    _isPending(provider, option) {
        const key = `${provider}.${option}`;
        return this._saving === key || this._queue.has(key);
    }

    /** Show why a change to one option was not saved, until that option is saved. */
    _showError(ui, message, option) {
        ui.errorOption = option;
        ui.errorRow.subtitle = message;
        ui.errorRow.visible = true;
        ui.expander.expanded = true;
    }

    _setText(f, text) {
        this._updating = true;
        try {
            f.row.text = text;
        } finally {
            this._updating = false;
        }
    }

    /** Run `aiu --json` once, as the panel does, for the line under each provider. */
    async _checkStatus() {
        if (!this._aiu || this._checking) {
            return;
        }
        this._checking = true;
        this._checkButton.sensitive = false;
        this._snapshot = null;
        this._statusErrorRow.visible = false;
        this._updateStatus();
        const res = await this._runner.run([this._aiu, '--json'], { timeoutSeconds: FETCH_TIMEOUT_SECONDS });
        if (res.cancelled || this._destroyed) {
            return;
        }
        this._checking = false;
        this._checkButton.sensitive = true;
        let snapshot = null;
        let error = res.error;
        if (res.ok) {
            try {
                snapshot = JSON.parse(res.stdout);
            } catch (err) {
                error = fmt(_('aiu returned invalid JSON: {error}'), { error: err.message });
            }
            error = schemaProblem(snapshot) || error;
            if (error) {
                snapshot = null;
            }
        }
        if (!snapshot || !Array.isArray(snapshot.rows)) {
            this._statusErrorRow.subtitle = error || _('aiu printed nothing');
            this._statusErrorRow.visible = true;
            snapshot = { rows: null };
        }
        this._snapshot = snapshot;
        this._updateStatus();
    }

    _updateStatus() {
        for (const ui of this._providers.values()) {
            ui.expander.subtitle = statusText(providerStatus(ui.provider, this._snapshot));
        }
    }

    _configPath() {
        return this._config?.path || GLib.build_filenamev([GLib.get_home_dir(), ...DEFAULT_CONFIG]);
    }

    _updateFileRow() {
        const path = this._configPath();
        this._fileRow.subtitle = path;
        this._openButton.sensitive = GLib.file_test(path, GLib.FileTest.EXISTS);
    }

    _openConfig() {
        const path = this._configPath();
        const launcher = new Gtk.FileLauncher({ file: Gio.File.new_for_path(path) });
        launcher.launch(this._window, null, (l, res) => {
            try {
                l.launch_finish(res);
            } catch (err) {
                if (!this._destroyed && !err.matches(Gtk.DialogError, Gtk.DialogError.DISMISSED)) {
                    this._fileRow.subtitle = fmt(_('Could not open {path}: {error}'), { path, error: err.message });
                }
            }
        });
    }

    /** Let the last changes reach config.toml, then close; never wait long. */
    _onCloseRequest() {
        if (this._destroyed) {
            return false;
        }
        for (const ui of this._providers.values()) {
            for (const f of ui.fields.values()) {
                if (f.timer) {
                    this._saveField(ui, f);
                }
            }
        }
        if (!this._saving && this._queue.size === 0) {
            this.destroy();
            return false;
        }
        if (!this._closing) {
            this._closing = true;
            this._later(CLOSE_GRACE_MS, () => this._finishClose());
        }
        return true; // _pump closes the window once the queue is empty
    }

    _finishClose() {
        this.destroy();
        this._window.close();
    }

    _later(ms, fn) {
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._sources.delete(id);
            fn();
            return GLib.SOURCE_REMOVE;
        });
        this._sources.add(id);
        return id;
    }

    _cancelTimer(f) {
        if (f.timer) {
            GLib.source_remove(f.timer);
            this._sources.delete(f.timer);
            f.timer = 0;
        }
    }

    _cancelField(ui) {
        for (const f of ui.fields.values()) {
            this._cancelTimer(f);
        }
    }

    /** Remove every timeout and kill every aiu still running: nothing outlives the window. */
    destroy() {
        if (this._destroyed) {
            return;
        }
        this._destroyed = true;
        for (const id of this._sources) {
            GLib.source_remove(id);
        }
        this._sources.clear();
        this._runner.cancelAll();
        if (this._settingsId) {
            this._settings.disconnect(this._settingsId);
            this._settingsId = 0;
        }
        this._window.disconnect(this._closeId);
        this._window.disconnect(this._destroyId);
    }
}
