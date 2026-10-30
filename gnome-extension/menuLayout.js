// SPDX-License-Identifier: GPL-3.0-or-later
// menuLayout.js - How tall the menu's scrolling data section may be.
//
// Plain arithmetic with no GNOME Shell import, so the GJS unit tests can load
// it (menuView.js cannot leave the Shell).

/**
 * CSS pixels kept free outside the measured menu content: the menu's
 * border and arrow, its gap below the panel, and a margin above the screen's
 * bottom edge.
 */
export const MENU_CHROME = 40;

/** On a tiny screen the data section keeps at least this many CSS pixels. */
export const MIN_DATA_HEIGHT = 120;

/**
 * The max height, in CSS pixels, of the data section's scroll view, so that
 * the header, the data section and the bottom items all fit in the monitor's
 * work area (the screen less the top bar and any dock).
 *
 * workAreaHeight and fixedHeight (everything in the menu but the data
 * section) are stage pixels, as Main.layoutManager and get_preferred_height()
 * give them; scaleFactor turns them into CSS pixels, as PanelMenu.Button does
 * for the menu's own max-height.
 *
 * Returns null when the work area is unknown: no cap rather than a made-up one.
 */
export function dataMaxHeight(workAreaHeight, fixedHeight, {
    scaleFactor = 1,
    chrome = MENU_CHROME,
    minHeight = MIN_DATA_HEIGHT,
} = {}) {
    if (!(workAreaHeight > 0) || !(scaleFactor > 0)) {
        return null;
    }
    const fixed = fixedHeight > 0 ? fixedHeight : 0;
    const room = (workAreaHeight - fixed) / scaleFactor - chrome;
    return Math.max(minHeight, Math.floor(room));
}
