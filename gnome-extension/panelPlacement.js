// SPDX-License-Identifier: GPL-3.0-or-later
// Placement relative to the clock, independent of Shell actors for offline tests.
export const PANEL_SIDE_KEY = 'panel-side';
export const PANEL_ORDER_KEY = 'panel-order';
export const PANEL_ORDER_MAX = 100;

/** Index among siblings with the indicator removed; rank 1 is closest to the clock. */
export function panelIndex(clockIndex, siblingCount, side, order) {
    const rank = Math.max(1, Math.min(PANEL_ORDER_MAX, Math.round(order)));
    if (clockIndex < 0)
        return side === 'left' ? 0 : siblingCount;
    return side === 'left'
        ? Math.max(0, clockIndex - rank + 1)
        : Math.min(siblingCount, clockIndex + rank);
}
