// test_menuLayout.js - The menu's data section fits the screen (run in CI).
// GNOME Shell cannot run here: menuView.js passes it the real measurements.
import System from 'system';
import { dataMaxHeight, MENU_CHROME, MIN_DATA_HEIGHT } from '../menuLayout.js';

function assertEqual(got, want, what) {
    if (got !== want) {
        throw new Error(`${what}: expected ${want}, got ${got}`);
    }
}

function run() {
    // 1080p with a 32 px top bar: 1048 px of work area. Header, two
    // separators and the two bottom items take about 180 px.
    assertEqual(dataMaxHeight(1048, 180), 1048 - 180 - MENU_CHROME, '1080p');
    assertEqual(dataMaxHeight(1048, 180) + 180 + MENU_CHROME <= 1048, true,
        'the whole menu fits in the work area');

    // Opening the web consoles submenu (5 links) leaves less room for the data.
    assertEqual(dataMaxHeight(1048, 180 + 5 * 36), 1048 - 360 - MENU_CHROME, 'submenu open');

    // Scale 2 (4K at 200 %): stage pixels are halved into CSS pixels.
    assertEqual(dataMaxHeight(2096, 360, { scaleFactor: 2 }), 1048 - 180 - MENU_CHROME, 'scale 2');

    // Fractional measurements round down, never past the screen.
    assertEqual(dataMaxHeight(1000.6, 100.3, { chrome: 0 }), 900, 'rounds down');
    assertEqual(dataMaxHeight(1000, 100, { scaleFactor: 1.5, chrome: 0 }), 600, 'scale 1.5');

    // A tiny screen still shows a few rows of data.
    assertEqual(dataMaxHeight(300, 250), MIN_DATA_HEIGHT, 'tiny screen');
    assertEqual(dataMaxHeight(300, 250, { minHeight: 0 }), 300 - 250 - MENU_CHROME, 'minHeight option');
    assertEqual(dataMaxHeight(300, 500, { minHeight: 0 }), 0, 'never negative');

    // Unknown or odd measurements: no cap, or ignore a bogus fixed height.
    for (const bad of [undefined, null, NaN, 0, -5]) {
        assertEqual(dataMaxHeight(bad, 180), null, `work area ${bad}`);
    }
    assertEqual(dataMaxHeight(1048, 180, { scaleFactor: 0 }), null, 'scale 0');
    assertEqual(dataMaxHeight(1048, NaN), 1048 - MENU_CHROME, 'fixed NaN');
    assertEqual(dataMaxHeight(1048, -20), 1048 - MENU_CHROME, 'fixed negative');

    console.log('[Test] Menu layout fits the screen ✅');
}

try {
    run();
} catch (err) {
    console.error('[Test Failed]', err);
    System.exit(1);
}
