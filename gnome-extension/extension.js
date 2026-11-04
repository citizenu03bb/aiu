// SPDX-License-Identifier: GPL-3.0-or-later
// extension.js - AI Usage GNOME Shell Extension entry point

import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import GLib from 'gi://GLib';
import { AiuClient } from './aiuClient.js';
import { clampInterval, REFRESH_KEY, SCHEMA_ID } from './aiuConfig.js';
import { AiuIndicator } from './indicator.js';
import { PANEL_ORDER_KEY, PANEL_SIDE_KEY, panelIndex } from './panelPlacement.js';

export default class AiuExtension extends Extension {
    enable() {
        console.log('[aiu-indicator] Enabling AI Usage indicator');

        this._client = new AiuClient();
        // The refresh interval, set in the preferences window: a change reschedules.
        this._settings = this.getSettings(SCHEMA_ID);
        this._settingsIds = [
            this._settings.connect(`changed::${REFRESH_KEY}`, () => this._schedule()),
            this._settings.connect(`changed::${PANEL_SIDE_KEY}`, () => this._placeIndicator()),
            this._settings.connect(`changed::${PANEL_ORDER_KEY}`, () => this._placeIndicator()),
        ];
        this._indicator = new AiuIndicator(this.path, {
            onRefresh: () => this._fetch(),
            onPreferences: () => this.openPreferences(),
        });

        // Remove any stale indicator from a previous cycle
        const existing = Main.panel.statusArea['aiu-indicator'];
        if (existing) {
            existing.destroy();
        }

        // Register once with Shell's menu manager; subsequent moves keep this actor.
        Main.panel.addToStatusArea(
            'aiu-indicator',
            this._indicator,
            1,
            'center'
        );
        this._placeIndicator();

        this._schedule();

        // Initial background fetch
        this._fetch();
    }

    disable() {
        console.log('[aiu-indicator] Disabling AI Usage indicator');

        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = null;
        }

        if (this._settings) {
            for (const id of this._settingsIds)
                this._settings.disconnect(id);
            this._settingsIds = [];
            this._settings = null;
        }

        if (this._client) {
            // Kills a running aiu, so none is left behind
            this._client.cancel();
            this._client = null;
        }

        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }

    /** Move relative to the clock, counting rank outwards on the chosen side. */
    _placeIndicator() {
        const container = this._indicator.container;
        const clock = Main.panel.statusArea.dateMenu?.container;
        const box = clock?.get_parent() || Main.panel._centerBox;
        const siblings = box.get_children().filter(child => child !== container);
        const index = panelIndex(siblings.indexOf(clock), siblings.length,
            this._settings.get_string(PANEL_SIDE_KEY), this._settings.get_uint(PANEL_ORDER_KEY));
        this._indicator.menu.close();
        const parent = container.get_parent();
        if (parent === box) {
            box.set_child_at_index(container, index);
        } else {
            parent?.remove_child(container);
            box.insert_child_at_index(container, index);
        }
    }

    /** (Re)start the periodic background refresh at the interval from GSettings. */
    _schedule() {
        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
        }
        this._timeoutId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            clampInterval(this._settings.get_uint(REFRESH_KEY)),
            () => {
                this._fetch();
                return GLib.SOURCE_CONTINUE;
            }
        );
    }

    async _fetch() {
        if (!this._client || !this._indicator) {
            return;
        }

        try {
            this._indicator.setRefreshing(true);
            const res = await this._client.fetchSnapshot();

            if (!this._indicator || res.cancelled) {
                // Extension was disabled, or a newer fetch replaced this one
                return;
            }

            if (res.success) {
                this._indicator.updateData(res.data);
            } else if (res.missing) {
                this._indicator.updateData(null, { missing: true });
            } else {
                console.error(`[aiu-indicator] Snapshot failed: ${res.error}`);
                this._indicator.updateData(null, { error: res.error });
            }
        } catch (err) {
            console.error(`[aiu-indicator] Exception in _fetch: ${err.message}`);
            if (this._indicator) {
                this._indicator.updateData(null, { error: err.message });
            }
        }
    }
}
