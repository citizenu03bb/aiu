// SPDX-License-Identifier: GPL-3.0-or-later
// indicator.js - Panel button and menu wrapper for aiu-indicator

import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import { needsSetup } from './aiuClient.js';
import { MenuView } from './menuView.js';

export const AiuIndicator = GObject.registerClass(
class AiuIndicator extends PanelMenu.Button {
    _init(extensionPath, callbacks = {}) {
        super._init(0.0, 'AI Usage Indicator', false);

        this._extensionPath = extensionPath;
        this._callbacks = callbacks;

        // Container box for top bar
        const panelBox = new St.BoxLayout({
            style_class: 'aiu-panel-box',
            y_align: Clutter.ActorAlign.CENTER,
        });

        // 1. Icon
        let gicon;
        const iconPath = `${extensionPath}/icons/aiu-symbolic.svg`;
        const iconFile = Gio.File.new_for_path(iconPath);
        if (iconFile.query_exists(null)) {
            gicon = new Gio.FileIcon({ file: iconFile });
        } else {
            gicon = Gio.ThemedIcon.new('starred-symbolic');
        }

        this._icon = new St.Icon({
            gicon: gicon,
            style_class: 'system-status-icon',
        });
        panelBox.add_child(this._icon);

        // 2. Status indicator dot
        this._statusDot = new St.Widget({
            style_class: 'aiu-status-dot unknown',
            width: 6,
            height: 6,
        });
        panelBox.add_child(this._statusDot);

        this.add_child(panelBox);

        // Build Menu
        this._menuView = new MenuView(this.menu, {
            onRefresh: () => {
                if (this._callbacks.onRefresh) {
                    this._callbacks.onRefresh();
                }
            },
            onPreferences: () => this._callbacks.onPreferences?.(),
        });

        // Refresh when menu is opened if data is stale (> 2 minutes)
        this._lastFetchTime = 0;
        this.menu.connectObject('open-state-changed', (menu, isOpen) => {
            if (isOpen) {
                const now = Date.now();
                if (now - this._lastFetchTime > 120_000) {
                    if (this._callbacks.onRefresh) {
                        this._callbacks.onRefresh();
                    }
                }
            }
        }, this);
    }

    vfunc_allocate(box) {
        this.set_allocation(box);

        const child = this.get_first_child();
        if (!child)
            return;

        const availWidth = Math.max(0, box.x2 - box.x1);
        const availHeight = Math.max(0, box.y2 - box.y1);
        if (availWidth <= 0 || availHeight <= 0)
            return;

        const [, natWidth] = child.get_preferred_width(-1);
        const minPadding = this._minHPadding || 0;
        const natPadding = this._natHPadding || 0;

        const childBox = new Clutter.ActorBox();
        if (natWidth + 2 * natPadding <= availWidth) {
            childBox.x1 = natPadding;
            childBox.x2 = Math.max(childBox.x1, availWidth - natPadding);
        } else {
            const pad = Math.min(minPadding, Math.floor(availWidth / 2));
            childBox.x1 = pad;
            childBox.x2 = Math.max(childBox.x1, availWidth - pad);
        }

        childBox.y1 = 0;
        childBox.y2 = availHeight;

        child.allocate(childBox);
    }

    setRefreshing(isRefreshing) {
        this._menuView.setRefreshing(isRefreshing);
    }

    updateData(data, { error = null, missing = false } = {}) {
        this._lastFetchTime = Date.now();
        this._menuView.setRefreshing(false);

        // Update panel status dot
        this._statusDot.remove_style_class_name('normal');
        this._statusDot.remove_style_class_name('warning');
        this._statusDot.remove_style_class_name('critical');
        this._statusDot.remove_style_class_name('unknown');

        if (missing) {
            // Not installed yet is a setup step, not an error: no red dot.
            this._statusDot.add_style_class_name('unknown');
            this._menuView.showInstall();
            return;
        }

        if (error) {
            this._statusDot.add_style_class_name('critical');
            this._menuView.update(null, { error });
            return;
        }

        // Nothing signed in yet is still setup: grey, not the red of a failure.
        const statusColor = needsSetup(data) ? 'unknown' : data?.statusColor || 'normal';
        this._statusDot.add_style_class_name(statusColor);
        this._menuView.update(data);
    }
});
