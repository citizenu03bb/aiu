// SPDX-License-Identifier: GPL-3.0-or-later
// prefs.js - Preferences window, opened from the Extensions app or with
// `gnome-extensions prefs aiu-indicator@citizenu03bb.github.io`. It runs in
// that app's process, not in GNOME Shell: Gtk 4 and Adwaita only.

import { ExtensionPreferences } from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';
import { SCHEMA_ID } from './aiuConfig.js';
import { PrefsController } from './prefsWindow.js';

export default class AiuPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        // The window's signals keep the controller alive; when the window
        // closes, it kills any aiu still running and removes its timeouts.
        new PrefsController(window, { settings: this.getSettings(SCHEMA_ID) });
    }
}
