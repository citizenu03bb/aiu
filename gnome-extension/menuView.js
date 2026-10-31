// SPDX-License-Identifier: GPL-3.0-or-later
// menuView.js - Populates and manages the popup menu for aiu-indicator

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import Pango from 'gi://Pango';
import GLib from 'gi://GLib';
import {
    INSTALL_GUIDE_URL, displayName, formatDuration, fullQuotaText, installCommand, needsSetup, resetText,
} from './aiuClient.js';
import { dataMaxHeight } from './menuLayout.js';
import { _, N_, fmt } from './i18n.js';

function setLineWrap(label, wrap = true) {
    const ct = typeof label.get_clutter_text === 'function'
        ? label.get_clutter_text()
        : (label['clutter-text'] || label.clutter_text);
    if (ct) {
        // St labels ellipsize by default, which silently cuts long text.
        ct.line_wrap = wrap;
        ct.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        ct.ellipsize = Pango.EllipsizeMode.NONE;
    }
}

// [badge text (translated when shown), style class]
const STATUS_BADGES = {
    burn: [N_('burn first'), 'burn'],
    '?': [N_('unknown pace'), 'unknown'],
    on_track: [N_('on track'), 'on-track'],
    conserve: [N_('conserve'), 'conserve'],
};

// Bar colour for the window aiu routes on: what to DO with it, not how full it is.
const STATUS_FILL = { burn: 'green', '?': 'grey', on_track: 'blue', conserve: 'orange' };

/** A track whose fill always covers `frac` of the track's real width. */
function progressBar(frac, colorClass) {
    const track = new St.BoxLayout({
        style_class: 'aiu-track',
        y_align: Clutter.ActorAlign.CENTER,
    });
    const fill = new St.Widget({
        style_class: `aiu-fill ${colorClass}`,
    });
    track.add_child(fill);
    const clamped = Math.max(0, Math.min(1, frac));
    // Only once the track is on screen: reading the width of an actor that is
    // not in the stage logs "st_widget_get_theme_node called on … not in the stage".
    const sync = () => {
        if (!track.get_stage()) {
            return;
        }
        const w = Math.round(track.width * clamped);
        fill.width = clamped > 0 ? Math.max(3, w) : 0;
    };
    track.connect('notify::width', sync);
    return track;
}

export class MenuView {
    constructor(menu, callbacks = {}) {
        this._menu = menu;
        this._callbacks = callbacks; // onRefresh, etc.
        this._dataSection = null;
        this._scrollView = null;
        this._webSubmenu = null;
        this._headerSubtitle = null;
        this._refreshButton = null;
        this._refreshIcon = null;

        this._buildStaticMenu();

        // The menu dies with the indicator, and these handlers with it.
        this._menu.connect('open-state-changed', (menu, isOpen) => {
            if (isOpen) {
                this._fitToScreen();
            }
        });
        // Opening the web consoles makes the bottom items taller.
        this._webSubmenu.menu.connect('open-state-changed', () => {
            if (this._menu.isOpen) {
                this._fitToScreen();
            }
        });
    }

    _buildStaticMenu() {
        this._menu.removeAll();

        // 1. Header item
        const headerItem = new PopupMenu.PopupBaseMenuItem({
            reactive: false,
            can_focus: false,
            style_class: 'aiu-header-item',
        });

        const headerBox = new St.BoxLayout({
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'aiu-header-box',
        });

        const titleBox = new St.BoxLayout({
            vertical: true,
            x_expand: true,
        });

        const titleLabel = new St.Label({
            text: 'AI Usage (aiu)',
            style_class: 'aiu-header-title',
        });
        this._headerSubtitle = new St.Label({
            text: _('Starting…'),
            style_class: 'aiu-header-time',
        });

        titleBox.add_child(titleLabel);
        titleBox.add_child(this._headerSubtitle);
        headerBox.add_child(titleBox);

        // Refresh icon button
        this._refreshButton = new St.Button({
            style_class: 'button aiu-refresh-btn',
            can_focus: true,
            track_hover: true,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._refreshIcon = new St.Icon({
            icon_name: 'view-refresh-symbolic',
            icon_size: 16,
        });
        this._refreshButton.set_child(this._refreshIcon);
        this._refreshButton.connect('clicked', () => {
            if (this._callbacks.onRefresh) {
                this._callbacks.onRefresh();
            }
        });

        headerBox.add_child(this._refreshButton);
        headerItem.add_child(headerBox);
        this._menu.addMenuItem(headerItem);

        // Separator
        this._menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // 2. Dynamic content, scrolled when taller than the screen allows
        // (_fitToScreen caps it), so the header and bottom items stay visible.
        this._dataSection = new PopupMenu.PopupMenuSection();
        this._scrollView = new St.ScrollView({
            style_class: 'aiu-scroll',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            x_expand: true,
        });

        this._dataBox = new St.BoxLayout({
            vertical: true,
            x_expand: true,
            style_class: 'aiu-menu-box',
        });
        // GNOME 45 sets up the scrolling in add_actor(); 46+ only has set_child().
        if (typeof this._scrollView.add_actor === 'function') {
            this._scrollView.add_actor(this._dataBox);
        } else {
            this._scrollView.set_child(this._dataBox);
        }
        this._dataSection.actor.add_child(this._scrollView);
        this._menu.addMenuItem(this._dataSection);

        // 3. Static footer items
        this._menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // Terminal launcher
        const termItem = new PopupMenu.PopupMenuItem(_('💻 Open aiu in a terminal'));
        termItem.connect('activate', () => {
            this._launchTerminalAiu();
        });
        this._menu.addMenuItem(termItem);

        // Web consoles submenu
        const webSubmenu = new PopupMenu.PopupSubMenuMenuItem(_('🌐 Web consoles'));
        this._webSubmenu = webSubmenu;
        this._addWebLink(webSubmenu, 'Google AI Studio', 'https://aistudio.google.com/');
        this._addWebLink(webSubmenu, 'Anthropic Console', 'https://console.anthropic.com/');
        this._addWebLink(webSubmenu, 'ChatGPT / OpenAI', 'https://chatgpt.com/');
        this._addWebLink(webSubmenu, 'DeepSeek Platform', 'https://platform.deepseek.com/');
        this._addWebLink(webSubmenu, 'OpenRouter Activity', 'https://openrouter.ai/activity');
        this._menu.addMenuItem(webSubmenu);

        // The preferences window (prefs.js), also under the Extensions app's gear
        const prefsItem = new PopupMenu.PopupMenuItem(_('⚙️ Preferences…'));
        prefsItem.connect('activate', () => this._callbacks.onPreferences?.());
        this._menu.addMenuItem(prefsItem);
    }

    /** Caps the data section so the whole menu fits in the monitor's work area. */
    _fitToScreen() {
        try {
            const layout = Main.layoutManager;
            const source = this._menu.sourceActor;
            let monitor = source ? layout.findIndexForActor(source) : -1;
            if (monitor < 0) {
                monitor = layout.primaryIndex;
            }
            const workArea = layout.getWorkAreaForMonitor(monitor);
            const scaleFactor = St.ThemeContext.get_for_stage(global.stage).scale_factor;

            // Everything but the data section, at its natural height. A submenu
            // reports its new state before it shows or hides itself.
            const [, menuHeight] = this._menu.box.get_preferred_height(-1);
            const [, dataHeight] = this._dataSection.actor.get_preferred_height(-1);
            const submenu = this._webSubmenu.menu;
            const [, submenuHeight] = submenu.actor.get_preferred_height(-1);
            const fixedHeight = menuHeight - dataHeight
                - (submenu.actor.visible ? submenuHeight : 0)
                + (submenu.isOpen ? submenuHeight : 0)
                + this._menu.actor.margin_top + this._menu.actor.margin_bottom;

            const maxHeight = dataMaxHeight(workArea?.height, fixedHeight, { scaleFactor });
            this._scrollView.style = maxHeight === null ? null : `max-height: ${maxHeight}px;`;
        } catch (e) {
            // A menu that overflows still beats a menu that does not open.
            console.error(`[aiu-indicator] Could not fit the menu to the screen: ${e.message}`);
        }
    }

    _addWebLink(submenu, label, url) {
        const item = new PopupMenu.PopupMenuItem(label);
        item.connect('activate', () => this._openUri(url));
        submenu.menu.addMenuItem(item);
    }

    _openUri(uri) {
        try {
            Gio.AppInfo.launch_default_for_uri(uri, null);
        } catch (e) {
            console.error(`[aiu-indicator] Could not open ${uri}: ${e.message}`);
        }
    }

    /** A small button for the setup boxes; the menu stays open when it is clicked. */
    _actionButton(label, onClicked) {
        const button = new St.Button({
            label,
            style_class: 'button aiu-action-btn',
            can_focus: true,
            track_hover: true,
        });
        button.connect('clicked', () => onClicked(button));
        return button;
    }

    /** aiu is not installed: say what this extension needs and how to get it. */
    showInstall() {
        this._dataBox.destroy_all_children();
        this._headerSubtitle.text = _('Setup needed');

        const { tool, command } = installCommand();
        const box = new St.BoxLayout({ vertical: true, style_class: 'aiu-setup-box', x_expand: true });
        const lines = [
            [_('👋 One step left: install aiu'), 'aiu-advice-title'],
            [_('This panel shows what the aiu command-line tool reads from your AI subscriptions. Install it with:'), 'aiu-advice-text'],
            [command, 'aiu-setup-command'],
        ];
        if (!tool) {
            lines.push([_('This needs pipx (sudo apt install pipx) or uv first.'), 'aiu-advice-text']);
        }
        lines.push([_('Then check again. aiu writes a starter config on its first run.'), 'aiu-advice-text']);
        for (const [text, style] of lines) {
            const label = new St.Label({ text, style_class: style, x_expand: true });
            setLineWrap(label, true);
            box.add_child(label);
        }

        const buttons = new St.BoxLayout({ style_class: 'aiu-setup-buttons', x_expand: true });
        buttons.add_child(this._actionButton(_('Copy command'), button => {
            // The only clipboard access: write-only, on this click, declared in
            // metadata.json's description (extensions.gnome.org review rule).
            St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, command);
            button.label = _('Copied ✓');
        }));
        buttons.add_child(this._actionButton(_('Install guide'), () => this._openUri(INSTALL_GUIDE_URL)));
        buttons.add_child(this._actionButton(_('Check again'), () => this._callbacks.onRefresh?.()));
        box.add_child(buttons);
        this._dataBox.add_child(box);
    }

    /** aiu runs but reads nothing yet: point at its config. */
    _addSignInHint() {
        const configPath = GLib.build_filenamev([GLib.get_home_dir(), '.config', 'ai-usage', 'config.toml']);
        const box = new St.BoxLayout({ vertical: true, style_class: 'aiu-setup-box', x_expand: true });
        for (const [text, style] of [
            [_('👋 No subscription read yet'), 'aiu-advice-title'],
            [_('Turn on the providers you use in ~/.config/ai-usage/config.toml and sign in to their command-line tools, then refresh. The notes below say what each one is missing.'), 'aiu-advice-text'],
        ]) {
            const label = new St.Label({ text, style_class: style, x_expand: true });
            setLineWrap(label, true);
            box.add_child(label);
        }
        const buttons = new St.BoxLayout({ style_class: 'aiu-setup-buttons', x_expand: true });
        buttons.add_child(this._actionButton(_('Open config'),
            () => this._openUri(Gio.File.new_for_path(configPath).get_uri())));
        buttons.add_child(this._actionButton(_('Setup guide'), () => this._openUri(INSTALL_GUIDE_URL)));
        box.add_child(buttons);
        this._dataBox.add_child(box);
    }

    _launchTerminalAiu() {
        try {
            // Quoted twice: once for bash, once for the command line parser.
            const script = `aiu; echo; read -r -p ${GLib.shell_quote(_('Press Enter to close…'))}`;
            const cmd = `gnome-terminal -- bash -c ${GLib.shell_quote(script)}`;
            const context = global.create_app_launch_context(0, -1);
            Gio.AppInfo.create_from_commandline(cmd, 'aiu', Gio.AppInfoCreateFlags.NONE).launch([], context);
        } catch (e) {
            console.error(`[aiu-indicator] Could not open aiu in a terminal: ${e.message}`);
        }
    }

    setRefreshing(isRefreshing) {
        if (isRefreshing) {
            this._headerSubtitle.text = _('Refreshing…');
            this._refreshButton.reactive = false;
        } else {
            this._refreshButton.reactive = true;
        }
    }

    update(data, { error = null } = {}) {
        this._dataBox.destroy_all_children();

        if (error) {
            const errBox = new St.BoxLayout({
                vertical: true,
                style_class: 'aiu-alert-box',
                x_expand: true,
            });
            const errTitle = new St.Label({
                text: _('⚠️ Could not fetch the snapshot'),
                style_class: 'aiu-provider-name',
            });
            const errDesc = new St.Label({
                text: String(error),
                style_class: 'aiu-alert-text',
            });
            setLineWrap(errDesc, true);
            errBox.add_child(errTitle);
            errBox.add_child(errDesc);
            this._dataBox.add_child(errBox);
            this._headerSubtitle.text = _('Error');
            return;
        }

        if (!data) {
            const loadingLabel = new St.Label({
                text: _('No data available'),
                style_class: 'aiu-header-time',
            });
            this._dataBox.add_child(loadingLabel);
            return;
        }

        // Update header timestamp
        const dateObj = new Date(data.capturedAt);
        const timeStr = dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        this._headerSubtitle.text = fmt(_('Last snapshot: {time}'), { time: timeStr });

        // 0. First run: nothing read yet, so say how to set aiu up
        if (needsSetup(data)) {
            this._addSignInHint();
        }

        // 1. What turned the dot red or yellow: windows used up (errors follow)
        if (data.fullQuotas && data.fullQuotas.length > 0) {
            const critical = data.fullQuotas.some(q => q.sublimit !== true);
            const fullBox = new St.BoxLayout({
                vertical: true,
                style_class: critical ? 'aiu-alert-box' : 'aiu-advice-box',
                x_expand: true,
            });
            fullBox.add_child(new St.Label({
                text: _('Used up, or nearly:'),
                style_class: 'aiu-provider-name',
            }));
            for (const q of data.fullQuotas) {
                const label = new St.Label({
                    text: fullQuotaText(q),
                    style_class: q.sublimit === true ? 'aiu-advice-text' : 'aiu-alert-text',
                });
                setLineWrap(label, true);
                fullBox.add_child(label);
            }
            this._dataBox.add_child(fullBox);
        }

        // 2. Errors & Token warnings (if any)
        if (data.errors && data.errors.length > 0) {
            const alertBox = new St.BoxLayout({
                vertical: true,
                style_class: 'aiu-alert-box',
                x_expand: true,
            });
            for (const err of data.errors) {
                const provLabel = new St.Label({
                    text: fmt(_('⚠️ {provider}:'), { provider: displayName(err.provider) }),
                    style_class: 'aiu-provider-name',
                });
                const noteLabel = new St.Label({
                    text: err.note || _('Authentication error or quota exceeded'),
                    style_class: 'aiu-alert-text',
                });
                setLineWrap(noteLabel, true);
                alertBox.add_child(provLabel);
                alertBox.add_child(noteLabel);
            }
            this._dataBox.add_child(alertBox);
        }

        // 3. Advice box: headline + detail lines (wrapped, never cut)
        if (data.advice) {
            const adviceBox = new St.BoxLayout({
                vertical: true,
                style_class: 'aiu-advice-box',
                x_expand: true,
            });
            data.advice.split('\n').forEach((line, i) => {
                const label = new St.Label({
                    text: line,
                    style_class: i === 0 ? 'aiu-advice-title' : 'aiu-advice-text',
                    x_expand: true,
                });
                setLineWrap(label, true);
                adviceBox.add_child(label);
            });
            this._dataBox.add_child(adviceBox);
        }

        const routing = data.routing || [];
        // The subscription's own window lot (credits are listed with balances).
        const lotFor = provider => routing.find(l => l.provider === provider && l.leftover !== null)
            || routing.find(l => l.provider === provider && l.status !== 'burn');
        const rankOf = provider => {
            const i = routing.findIndex(l => l.provider === provider);
            return i < 0 ? routing.length : i;
        };

        // 4. Subscriptions, in routing order (the one to use first on top)
        if (data.quotas && data.quotas.length > 0) {
            this._dataBox.add_child(new St.Label({
                text: _('Subscriptions'),
                style_class: 'aiu-section-label',
            }));

            const grouped = {};
            for (const q of data.quotas) {
                (grouped[q.provider] ||= []).push(q);
            }
            const providers = Object.keys(grouped).sort((x, y) => rankOf(x) - rankOf(y));

            for (const provider of providers) {
                const items = grouped[provider];
                const lot = lotFor(provider);
                const card = new St.BoxLayout({
                    vertical: true,
                    style_class: 'aiu-card',
                    x_expand: true,
                });

                const provHeader = new St.BoxLayout({
                    style_class: 'aiu-provider-header',
                    x_expand: true,
                    y_align: Clutter.ActorAlign.CENTER,
                });
                provHeader.add_child(new St.Label({
                    text: displayName(provider),
                    style_class: 'aiu-provider-name',
                    x_expand: true,
                    y_align: Clutter.ActorAlign.CENTER,
                }));
                const firstNote = items.find(i => i.note)?.note;
                if (firstNote) {
                    provHeader.add_child(new St.Label({
                        text: firstNote,
                        style_class: 'aiu-pill',
                        y_align: Clutter.ActorAlign.CENTER,
                    }));
                }
                if (lot && STATUS_BADGES[lot.status]) {
                    const [text, cls] = STATUS_BADGES[lot.status];
                    provHeader.add_child(new St.Label({
                        text: _(text),
                        style_class: `aiu-badge ${cls}`,
                        y_align: Clutter.ActorAlign.CENTER,
                    }));
                }
                card.add_child(provHeader);

                for (const item of items) {
                    const isRouted = lot && item.metric === lot.metric;
                    // One line per metric: name, reset, bar, percent.
                    const row = new St.BoxLayout({ x_expand: true, style_class: 'aiu-quota-row' });
                    const name = new St.Label({
                        text: item.metric,
                        style_class: 'aiu-metric-name',
                        x_expand: true,
                        y_align: Clutter.ActorAlign.CENTER,
                    });
                    setLineWrap(name, true);
                    row.add_child(name);

                    const reset = resetText(item);
                    if (reset) {
                        row.add_child(new St.Label({
                            text: fmt(_('Reset {when}'), { when: reset }),
                            style_class: 'aiu-metric-reset',
                            y_align: Clutter.ActorAlign.CENTER,
                        }));
                    }

                    let colorClass;
                    if (item.used_pct === null) {
                        colorClass = 'grey';
                    } else if (isRouted) {
                        colorClass = STATUS_FILL[lot.status] || 'grey';
                    } else {
                        // Short windows are rate limits: only fullness matters.
                        colorClass = item.used_pct >= 85 ? 'red' : item.used_pct >= 60 ? 'yellow' : 'green';
                    }
                    row.add_child(progressBar((item.used_pct ?? 0) / 100, colorClass));

                    row.add_child(new St.Label({
                        text: item.used_pct === null
                            ? '—'
                            : fmt(_('{percent}%'), { percent: Math.round(item.used_pct) }),
                        style_class: 'aiu-metric-detail',
                        y_align: Clutter.ActorAlign.CENTER,
                    }));
                    card.add_child(row);

                    // The pace hint, on the window aiu routes on only.
                    let hint = null;
                    if (isRouted && typeof lot.leftover === 'number') {
                        if (lot.status === 'conserve' && typeof lot.runway_secs === 'number') {
                            hint = fmt(_('runs dry in ~{duration} at your pace'),
                                { duration: formatDuration(lot.runway_secs) });
                        } else if (lot.status === 'burn') {
                            hint = fmt(_('~{percent}% lost at reset at your pace'),
                                { percent: Math.round(lot.leftover * 100) });
                        }
                    }
                    if (hint) {
                        const sub = new St.Label({
                            text: hint,
                            style_class: `aiu-metric-reset ${STATUS_BADGES[lot.status]?.[1] || ''}`,
                        });
                        setLineWrap(sub, true);
                        card.add_child(sub);
                    }
                }

                this._dataBox.add_child(card);
            }
        }

        // 5. Credits, prepaid balances, lifetime spend
        if (data.balances && data.balances.length > 0) {
            this._dataBox.add_child(new St.Label({
                text: _('Credits & paid APIs'),
                style_class: 'aiu-section-label',
            }));

            const balCard = new St.BoxLayout({
                vertical: true,
                style_class: 'aiu-card',
                x_expand: true,
            });
            // Free credits first, then what can still pay for overflow, then sunk spend.
            const order = { credit: 0, balance: 1, spend: 2 };
            const sorted = [...data.balances].sort((x, y) => (order[x.kind] ?? 3) - (order[y.kind] ?? 3));

            for (const b of sorted) {
                const block = new St.BoxLayout({ vertical: true, x_expand: true });
                const balRow = new St.BoxLayout({ x_expand: true, style_class: 'aiu-balance-row' });
                balRow.add_child(new St.Label({
                    text: `${displayName(b.provider)} — ${b.metric}`,
                    style_class: 'aiu-metric-name',
                    x_expand: true,
                }));

                let valText = typeof b.used === 'number' ? b.used.toFixed(2) : `${b.used ?? '—'}`;
                if (b.kind === 'credit' && typeof b.limit === 'number') {
                    valText += ` / ${b.limit.toFixed(0)}`;
                }
                valText = `${valText} ${b.unit || ''}`.trim();
                balRow.add_child(new St.Label({
                    text: valText,
                    style_class: `aiu-currency-val ${b.kind}`,
                }));
                block.add_child(balRow);

                let sub = null;
                if (b.kind === 'credit') {
                    const left = resetText(b);
                    sub = left
                        ? fmt(_('free, lost when it expires ({when})'), { when: left })
                        : _('free, lost when it expires');
                } else if (b.kind === 'spend' && /total/i.test(b.metric)) {
                    sub = _('already spent (history)');
                }
                if (b.note) {
                    sub = sub ? `${sub} · ${b.note}` : b.note;
                }
                if (sub) {
                    block.add_child(new St.Label({ text: sub, style_class: 'aiu-metric-reset' }));
                }
                balCard.add_child(block);
            }

            this._dataBox.add_child(balCard);
        }
    }
}
