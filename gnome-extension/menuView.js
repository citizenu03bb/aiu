// SPDX-License-Identifier: GPL-3.0-or-later
// menuView.js - Populates and manages the popup menu for aiu-indicator

import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export class MenuView {
    constructor(menu, callbacks = {}) {
        this._menu = menu;
        this._callbacks = callbacks; // onRefresh, etc.
        this._dataSection = null;
        this._headerSubtitle = null;
        this._refreshButton = null;
        this._refreshIcon = null;

        this._buildStaticMenu();
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
            text: 'Initialisation...',
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

        // 2. Dynamic content container item
        this._dataSection = new PopupMenu.PopupBaseMenuItem({
            reactive: false,
            can_focus: false,
        });

        this._dataBox = new St.BoxLayout({
            vertical: true,
            x_expand: true,
            style_class: 'aiu-menu-box',
        });
        this._dataSection.add_child(this._dataBox);
        this._menu.addMenuItem(this._dataSection);

        // 3. Static footer items
        this._menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // Terminal launcher
        const termItem = new PopupMenu.PopupMenuItem('💻 Ouvrir aiu dans le terminal');
        termItem.connect('activate', () => {
            this._launchTerminalAiu();
        });
        this._menu.addMenuItem(termItem);

        // Web consoles submenu
        const webSubmenu = new PopupMenu.PopupSubMenuMenuItem('🌐 Consoles Web');
        this._addWebLink(webSubmenu, 'Google AI Studio', 'https://aistudio.google.com/');
        this._addWebLink(webSubmenu, 'Anthropic Console', 'https://console.anthropic.com/');
        this._addWebLink(webSubmenu, 'ChatGPT / OpenAI', 'https://chatgpt.com/');
        this._addWebLink(webSubmenu, 'DeepSeek Platform', 'https://platform.deepseek.com/');
        this._addWebLink(webSubmenu, 'OpenRouter Activity', 'https://openrouter.ai/activity');
        this._menu.addMenuItem(webSubmenu);
    }

    _addWebLink(submenu, label, url) {
        const item = new PopupMenu.PopupMenuItem(label);
        item.connect('activate', () => {
            try {
                Gio.AppInfo.launch_default_for_uri(url, null);
            } catch (e) {
                console.error(`[aiu-indicator] Erreur ouverture URL ${url}: ${e.message}`);
            }
        });
        submenu.menu.addMenuItem(item);
    }

    _launchTerminalAiu() {
        try {
            const cmd = 'gnome-terminal -- bash -c "aiu; echo \'\'; read -p \'Appuyez sur Entrée pour fermer...\'"';
            const context = global.create_app_launch_context(0, -1);
            Gio.AppInfo.create_from_commandline(cmd, 'aiu', Gio.AppInfoCreateFlags.NONE).launch([], context);
        } catch (e) {
            console.error(`[aiu-indicator] Erreur lancement terminal aiu: ${e.message}`);
        }
    }

    setRefreshing(isRefreshing) {
        if (isRefreshing) {
            this._headerSubtitle.text = 'Actualisation en cours...';
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
                text: '⚠️ Erreur lors de la récupération',
                style_class: 'aiu-provider-name',
            });
            const errDesc = new St.Label({
                text: String(error),
                style_class: 'aiu-alert-text',
            });
            errDesc.clutter_text.line_wrap = true;
            errBox.add_child(errTitle);
            errBox.add_child(errDesc);
            this._dataBox.add_child(errBox);
            this._headerSubtitle.text = 'Erreur';
            return;
        }

        if (!data) {
            const loadingLabel = new St.Label({
                text: 'Aucune donnée disponible',
                style_class: 'aiu-header-time',
            });
            this._dataBox.add_child(loadingLabel);
            return;
        }

        // Update header timestamp
        const dateObj = new Date(data.capturedAt);
        const timeStr = dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        this._headerSubtitle.text = `Dernier relevé : ${timeStr}`;

        // 1. Errors & Token warnings (if any)
        if (data.errors && data.errors.length > 0) {
            const alertBox = new St.BoxLayout({
                vertical: true,
                style_class: 'aiu-alert-box',
                x_expand: true,
            });
            for (const err of data.errors) {
                const provLabel = new St.Label({
                    text: `⚠️ ${err.provider} :`,
                    style_class: 'aiu-provider-name',
                });
                const noteLabel = new St.Label({
                    text: err.note || 'Erreur d’authentification ou quota dépassé',
                    style_class: 'aiu-alert-text',
                });
                noteLabel.clutter_text.line_wrap = true;
                alertBox.add_child(provLabel);
                alertBox.add_child(noteLabel);
            }
            this._dataBox.add_child(alertBox);
        }

        // 2. Advice Box (if available)
        if (data.advice) {
            const adviceBox = new St.BoxLayout({
                vertical: true,
                style_class: 'aiu-advice-box',
                x_expand: true,
            });
            const adviceLabel = new St.Label({
                text: data.advice,
                style_class: 'aiu-advice-text',
            });
            adviceLabel.clutter_text.line_wrap = true;
            adviceBox.add_child(adviceLabel);
            this._dataBox.add_child(adviceBox);
        }

        // 3. Quotas Section
        if (data.quotas && data.quotas.length > 0) {
            const quotaSectionLabel = new St.Label({
                text: 'Forfaits & Quotas',
                style_class: 'aiu-section-label',
            });
            this._dataBox.add_child(quotaSectionLabel);

            // Group quotas by provider
            const grouped = {};
            for (const q of data.quotas) {
                if (!grouped[q.provider]) {
                    grouped[q.provider] = [];
                }
                grouped[q.provider].push(q);
            }

            for (const [provider, items] of Object.entries(grouped)) {
                const card = new St.BoxLayout({
                    vertical: true,
                    style_class: 'aiu-card',
                    x_expand: true,
                });

                // Provider header with note pill if available
                const provHeader = new St.BoxLayout({
                    x_expand: true,
                    y_align: Clutter.ActorAlign.CENTER,
                    spacing: 6,
                });
                const nameLabel = new St.Label({
                    text: provider,
                    style_class: 'aiu-provider-name',
                    x_expand: true,
                });
                provHeader.add_child(nameLabel);

                const firstNote = items.find(i => i.note)?.note;
                if (firstNote) {
                    const pill = new St.Label({
                        text: firstNote,
                        style_class: 'aiu-pill',
                    });
                    provHeader.add_child(pill);
                }
                card.add_child(provHeader);

                // Quota items
                for (const item of items) {
                    const rowBox = new St.BoxLayout({
                        vertical: true,
                        margin_top: 4,
                    });

                    const infoLine = new St.BoxLayout({
                        x_expand: true,
                    });

                    const metricLabel = new St.Label({
                        text: item.metric,
                        style_class: 'aiu-metric-name',
                        x_expand: true,
                    });
                    const pctText = `${item.used_pct.toFixed(1)}%`;
                    const pctLabel = new St.Label({
                        text: pctText,
                        style_class: 'aiu-metric-detail',
                    });

                    infoLine.add_child(metricLabel);
                    infoLine.add_child(pctLabel);
                    rowBox.add_child(infoLine);

                    // Visual progress bar
                    const track = new St.BoxLayout({
                        style_class: 'aiu-track',
                        x_expand: true,
                    });

                    let colorClass = 'green';
                    if (item.used_pct >= 85) {
                        colorClass = 'red';
                    } else if (item.used_pct >= 60) {
                        colorClass = 'yellow';
                    }

                    const fill = new St.Widget({
                        style_class: `aiu-fill ${colorClass}`,
                    });

                    // Target width proportional to 260px container
                    const fillPx = Math.max(3, Math.min(270, Math.round((item.used_pct / 100) * 270)));
                    fill.set_style(`width: ${fillPx}px;`);

                    track.add_child(fill);
                    rowBox.add_child(track);

                    // Reset label if exists
                    if (item.reset) {
                        const resetLabel = new St.Label({
                            text: `Reset : ${item.reset}`,
                            style_class: 'aiu-metric-reset',
                        });
                        rowBox.add_child(resetLabel);
                    }

                    card.add_child(rowBox);
                }

                this._dataBox.add_child(card);
            }
        }

        // 4. Balances & Pay-as-you-go Section
        if (data.balances && data.balances.length > 0) {
            const balSectionLabel = new St.Label({
                text: 'Soldes & Consommation API',
                style_class: 'aiu-section-label',
            });
            this._dataBox.add_child(balSectionLabel);

            const balCard = new St.BoxLayout({
                vertical: true,
                style_class: 'aiu-card',
                x_expand: true,
            });

            for (const b of data.balances) {
                const balRow = new St.BoxLayout({
                    x_expand: true,
                    style_class: 'aiu-balance-row',
                    margin_top: 2,
                    margin_bottom: 2,
                });

                const labelText = `${b.provider} — ${b.metric}`;
                const nameLabel = new St.Label({
                    text: labelText,
                    style_class: 'aiu-metric-name',
                    x_expand: true,
                });

                const valText = typeof b.used === 'number'
                    ? `${b.used.toFixed(2)} ${b.unit || ''}`.trim()
                    : `${b.used || '—'}`;

                const valLabel = new St.Label({
                    text: valText,
                    style_class: 'aiu-currency-val',
                });

                balRow.add_child(nameLabel);
                balRow.add_child(valLabel);
                balCard.add_child(balRow);
            }

            this._dataBox.add_child(balCard);
        }
    }
}
