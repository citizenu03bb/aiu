// SPDX-License-Identifier: GPL-3.0-or-later
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import { SCHEMA_ID } from '../aiuConfig.js';
import { PANEL_ORDER_KEY, PANEL_ORDER_MAX, PANEL_SIDE_KEY, panelIndex } from '../panelPlacement.js';

function equal(got, want, what) {
    if (JSON.stringify(got) !== JSON.stringify(want))
        throw new Error(`${what}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
}

// Realistic panel compositions, including another extension on either side.
// Simulate removing/reinserting the same actor through repeated live changes.
const clock = 'clock';
const indicator = 'aiu';
let children = ['left2', 'left1', clock, indicator, 'right1', 'right2'];
function place(side, order, want) {
    const siblings = children.filter(c => c !== indicator);
    const index = panelIndex(siblings.indexOf(clock), siblings.length, side, order);
    siblings.splice(index, 0, indicator);
    equal(siblings, want, `${side}, rank ${order}`);
    children = siblings;
}
place('right', 1, ['left2', 'left1', clock, indicator, 'right1', 'right2']);
place('left', 1, ['left2', 'left1', indicator, clock, 'right1', 'right2']);
place('left', 2, ['left2', indicator, 'left1', clock, 'right1', 'right2']);
place('right', 2, ['left2', 'left1', clock, 'right1', indicator, 'right2']);
place('right', PANEL_ORDER_MAX, ['left2', 'left1', clock, 'right1', 'right2', indicator]);
place('left', PANEL_ORDER_MAX, [indicator, 'left2', 'left1', clock, 'right1', 'right2']);
// Reapply and switch back: no offset from the indicator's previous position.
place('left', PANEL_ORDER_MAX, [indicator, 'left2', 'left1', clock, 'right1', 'right2']);
place('right', 1, ['left2', 'left1', clock, indicator, 'right1', 'right2']);
for (const side of ['left', 'right']) {
    equal(panelIndex(0, 1, side, 100), side === 'left' ? 0 : 1, 'clock only');
    equal(panelIndex(-1, 3, side, 1), side === 'left' ? 0 : 3, 'clock absent');
}
console.log('[Test] Placement around the clock, ranks and repeated moves ✅');

const source = Gio.SettingsSchemaSource.new_from_directory('schemas', null, true);
const schema = source.lookup(SCHEMA_ID, false);
const sideKey = schema.get_key(PANEL_SIDE_KEY);
const orderKey = schema.get_key(PANEL_ORDER_KEY);
equal(sideKey.get_default_value().unpack(), 'right', 'default side preserves current placement');
equal(orderKey.get_default_value().unpack(), 1, 'default rank');
equal(['left', 'right', 'center'].map(s => sideKey.range_check(new GLib.Variant('s', s))),
    [true, true, false], 'side choices');
equal([0, 1, PANEL_ORDER_MAX, PANEL_ORDER_MAX + 1].map(n => orderKey.range_check(new GLib.Variant('u', n))),
    [false, true, true, false], 'rank bounds');
// Isolated backend: never reads or writes the user's real panel preferences.
const settings = new Gio.Settings({ settings_schema: schema, backend: Gio.memory_settings_backend_new() });
const changes = [];
settings.connect('changed', (_s, key) => changes.push(key));
settings.set_string(PANEL_SIDE_KEY, 'left');
settings.set_uint(PANEL_ORDER_KEY, 3);
equal([settings.get_string(PANEL_SIDE_KEY), settings.get_uint(PANEL_ORDER_KEY)], ['left', 3], 'stored choices');
equal(changes, [PANEL_SIDE_KEY, PANEL_ORDER_KEY], 'live settings notifications');
console.log('[Test] Panel settings defaults, limits and notifications ✅');
