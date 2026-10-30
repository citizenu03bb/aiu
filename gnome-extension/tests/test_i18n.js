// test_i18n.js - The French catalog renders. `make test` runs it through
// tests/run_french.sh, after building locale/.
import GLib from 'gi://GLib';
import Gettext from 'gettext';
import System from 'system';
import { DOMAIN } from '../i18n.js';
import { AiuClient, formatDuration, resetText } from '../aiuClient.js';
import { fieldValue, optionTitle, statusText } from '../aiuConfig.js';

const extDir = GLib.path_get_dirname(GLib.path_get_dirname(GLib.filename_from_uri(import.meta.url)[0]));

function assertEqual(got, want, what) {
    if (got !== want) {
        throw new Error(`${what}: expected "${want}", got "${got}"`);
    }
}

function run() {
    const [, metadata] = GLib.file_get_contents(GLib.build_filenamev([extDir, 'metadata.json']));
    assertEqual(JSON.parse(new TextDecoder().decode(metadata))['gettext-domain'], DOMAIN,
        'gettext-domain in metadata.json');

    // What GNOME Shell does for the extension's gettext-domain on load.
    Gettext.bindtextdomain(DOMAIN, GLib.build_filenamev([extDir, 'locale']));
    if (Gettext.dgettext(DOMAIN, 'Error') !== 'Erreur') {
        throw new Error(`French catalog not used (locale ${Gettext.setlocale(Gettext.LocaleCategory.MESSAGES, null)}, `
            + `LANGUAGE=${GLib.getenv('LANGUAGE')}): run it with tests/run_french.sh after make mo`);
    }

    assertEqual(formatDuration(90000), '1j1h', 'days');
    assertEqual(formatDuration(3 * 3600 + 8 * 60), '3h08', 'hours');
    assertEqual(formatDuration(600), '10min', 'minutes');
    assertEqual(resetText({ reset: 'expires in 2d 3h' }, 0), 'dans 2j3h', 'reset text from aiu');
    assertEqual(resetText({ reset_epoch: 0 }, 1), 'passé, relevé à rafraîchir', 'past reset');

    const client = new AiuClient();
    const cases = [
        [[{ provider: 'claude', metric: 'Weekly (all models)', status: 'burn', ttr_secs: 21485, leftover: 0.112 },
          { provider: 'codex', metric: 'Weekly Limit', status: 'conserve', ttr_secs: 437723, leftover: -1.764 },
          { provider: 'gemini', metric: 'Weekly Limit', status: 'conserve', ttr_secs: 424800, leftover: -1.226 }],
            "💡 Brûle d'abord Claude\nWeekly (all models) : ~11 % perdu au reset dans 5h58\nÀ ménager : Codex, Gemini"],
        [[{ provider: 'codex', metric: 'Weekly Limit', status: 'on_track', ttr_secs: 3600, leftover: 0.01 }],
            '💡 Pas de surplus qui expire : route par fit de tâche'],
        [[{ provider: 'gemini', metric: 'Gemini Flash', status: '?', ttr_secs: 600, leftover: null }],
            "💡 Brûle d'abord Gemini\nGemini Flash : reset dans 10min (allure inconnue)"],
        [[{ provider: 'claude', metric: 'Cloud Credit', status: 'burn', ttr_secs: 90000, leftover: null }],
            "💡 Brûle d'abord Claude\nCloud Credit : expire dans 1j1h"],
        [[{ provider: 'codex', metric: 'Weekly Limit', status: 'conserve', ttr_secs: 9, leftover: -0.5 }],
            "💡 Aucun forfait n'a de surplus\nRéserve chacun à ce que lui seul sait faire, déborde sur l'API payante"],
    ];
    for (const [lots, want] of cases) {
        assertEqual(client._generateAdvice(lots), want, 'advice');
    }

    // The preferences window, in the same catalog.
    assertEqual(statusText({ state: 'ok' }), 'Connecté', 'status line');
    assertEqual(optionTitle('key_cmd'), 'Commande qui affiche la clé', 'field title');
    assertEqual(optionTitle('unknown_option'), 'unknown_option', 'unknown option keeps its name');
    assertEqual(fieldValue({ type: 'string' }, 'sk-abc').error.startsWith('Ceci ressemble à une clé'), true,
        'a key is refused in French');
    console.log('[Test] French translations render ✅');
}

try {
    run();
} catch (err) {
    console.error('[Test Failed]', err);
    System.exit(1);
}
