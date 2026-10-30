// SPDX-License-Identifier: GPL-3.0-or-later
// extension.js - AI Usage GNOME Shell Extension entry point

import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import GLib from 'gi://GLib';
import { AiuClient } from './aiuClient.js';
import { AiuIndicator } from './indicator.js';

const REFRESH_INTERVAL_SECONDS = 300; // 5 minutes

export default class AiuExtension extends Extension {
    enable() {
        console.log('[aiu-indicator] Enabling AI Usage indicator');

        this._client = new AiuClient();
        this._indicator = new AiuIndicator(this.path, {
            onRefresh: () => this._fetch(),
        });

        // Remove any stale indicator from a previous cycle
        const existing = Main.panel.statusArea['aiu-indicator'];
        if (existing) {
            existing.destroy();
        }

        // Place in center box, position 1 (immediately to the right of dateMenu at position 0)
        Main.panel.addToStatusArea(
            'aiu-indicator',
            this._indicator,
            1,
            'center'
        );

        // Schedule periodic background refresh
        this._timeoutId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            REFRESH_INTERVAL_SECONDS,
            () => {
                this._fetch();
                return GLib.SOURCE_CONTINUE;
            }
        );

        // Initial background fetch
        this._fetch();
    }

    disable() {
        console.log('[aiu-indicator] Disabling AI Usage indicator');

        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = null;
        }

        if (this._client) {
            this._client.cancel();
            this._client = null;
        }

        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }

    async _fetch() {
        if (!this._client || !this._indicator) {
            return;
        }

        try {
            this._indicator.setRefreshing(true);
            const res = await this._client.fetchSnapshot();

            if (!this._indicator) {
                // Extension was disabled while request was in-flight
                return;
            }

            if (res.success) {
                this._indicator.updateData(res.data);
            } else {
                console.error(`[aiu-indicator] Erreur snapshot: ${res.error}`);
                this._indicator.updateData(null, { error: res.error });
            }
        } catch (err) {
            console.error(`[aiu-indicator] Exception dans _fetch: ${err.message}`);
            if (this._indicator) {
                this._indicator.updateData(null, { error: err.message });
            }
        }
    }
}
