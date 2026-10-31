// test_aiuConfig.js - What the preferences window does without a window (run in CI):
// reading `aiu config show --json`, the `aiu config set` command line, keeping
// keys out of the fields, the status line, the refresh interval and its schema,
// and AiuRunner, which runs sh and sleep here in place of aiu.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import System from 'system';
import {
    AiuRunner, clampInterval, fieldValue, intervalMinutes, looksLikeSecret, optionHelp, optionTitle, parseConfigShow,
    providerStatus, REFRESH_DEFAULT_SECONDS, REFRESH_KEY, REFRESH_MAX_SECONDS, REFRESH_MIN_SECONDS, runsCode,
    SCHEMA_ID, SchemaError, setArgv, showArgv, statusText,
} from '../aiuConfig.js';

/** A file of tests/fixtures/ at the repository root, which the Python tests write from aiu itself. */
function readFixture(name) {
    const [here] = GLib.filename_from_uri(import.meta.url);
    const path = GLib.build_filenamev([GLib.path_get_dirname(here), '..', '..', 'tests', 'fixtures', name]);
    return new TextDecoder().decode(GLib.file_get_contents(path)[1]);
}

function assertEqual(got, want, what) {
    const g = JSON.stringify(got);
    const w = JSON.stringify(want);
    if (g !== w) {
        throw new Error(`${what}: expected ${w}, got ${g}`);
    }
}

const opt = (type, value, source = 'default') => ({ type, value, source });
const keySources = (env = null) => ({
    key_env: opt('string', env, env ? 'file' : 'default'),
    key_cmd: opt('string', null),
    key_file: opt('string', null),
});

// Shaped as aiu's config_snapshot() (aiu/config.py) prints it.
function showJson({ allowed = true, problems = [] } = {}) {
    return JSON.stringify({
        path: '/home/me/.config/ai-usage/config.toml',
        key_cmd_allowed: allowed,
        key_cmd_problems: problems,
        providers: {
            claude: {
                section: true,
                options: { enabled: opt('bool', true, 'file'), read_token: opt('bool', true, 'file'), claude_cmd: opt('string', null) },
            },
            codex: { section: true, options: { enabled: opt('bool', false, 'file') } },
            openai: { section: true, options: { enabled: opt('bool', false), ...keySources('OPENAI_ADMIN_KEY') } },
            mistral: {
                section: false,
                options: {
                    enabled: opt('bool', false), ...keySources('MISTRAL_ADMIN_COOKIE'),
                    vibe_allowance_eur: opt('number', 8.5), api_allowance_eur: opt('number', 0, 'file'),
                },
            },
        },
    });
}

function testParse() {
    const config = parseConfigShow(showJson());
    assertEqual(config.path, '/home/me/.config/ai-usage/config.toml', 'path');
    assertEqual(config.keyCmdAllowed, true, 'key_cmd allowed');
    assertEqual(config.providers.map(p => [p.id, p.name, p.enabled, p.hasKeySources]), [
        ['claude', 'Claude', true, false],
        ['codex', 'Codex', false, false],
        ['openai', 'OpenAI', false, true],
        ['mistral', 'Mistral', false, true],
    ], 'providers in aiu order');
    assertEqual(config.providers[1].fields, [], 'codex has nothing to enter');
    assertEqual(config.providers[0].fields, [
        { option: 'claude_cmd', type: 'string', text: '', fromFile: false, editable: true },
    ], 'claude fields');
    assertEqual(config.providers[0].switches, [{ option: 'read_token', active: true, fromFile: true }],
        'claude switches, enabled left to the provider switch');
    assertEqual(config.providers.slice(1).map(p => p.switches.length), [0, 0, 0], 'no other switches');
    assertEqual([optionTitle('read_token'), optionHelp('read_token').startsWith('Off by default.'),
        optionHelp('claude_cmd')], ['Read the claude sign-in token', true, ''], 'switch title and help');
    assertEqual(config.providers[3].fields.map(f => [f.option, f.type, f.text, f.fromFile]), [
        ['key_env', 'string', 'MISTRAL_ADMIN_COOKIE', true],
        ['key_cmd', 'string', '', false],
        ['key_file', 'string', '', false],
        ['vibe_allowance_eur', 'number', '8.5', false],
        ['api_allowance_eur', 'number', '0', true],
    ], 'mistral fields, enabled left to the switch');

    // A config that fails aiu's trust check: the commands are read-only.
    const untrusted = parseConfigShow(showJson({ allowed: false, problems: ['/home/me/.config/ai-usage is writable by group or others'] }));
    assertEqual(untrusted.keyCmdProblems, ['/home/me/.config/ai-usage is writable by group or others'], 'problems');
    const editable = untrusted.providers.flatMap(p => p.fields.map(f => `${p.id}.${f.option}=${f.editable}`));
    assertEqual(editable, [
        'claude.claude_cmd=false', 'openai.key_env=true', 'openai.key_cmd=false', 'openai.key_file=true',
        'mistral.key_env=true', 'mistral.key_cmd=false', 'mistral.key_file=true',
        'mistral.vibe_allowance_eur=true', 'mistral.api_allowance_eur=true',
    ], 'only the *_cmd fields turn read-only');
    // Every *_cmd runs a program, as runs_code in aiu/config.py: a vendor client added later included.
    assertEqual(['key_cmd', 'claude_cmd', 'codex_cmd', 'agy_cmd', 'key_env', 'key_file', 'cmd'].map(runsCode),
        [true, true, true, true, false, false, false], 'runsCode follows the suffix');
    assertEqual([optionTitle('codex_cmd'), optionTitle('agy_cmd')], ['codex program', 'agy program'],
        'vendor program titles');

    // A bool is a switch under its own name; a type the window does not know is left out, not guessed.
    const odd = parseConfigShow(JSON.stringify({
        providers: { x: { options: { flag: opt('bool', true), n: opt('number', 2), list: opt('array', []) } } },
    }));
    assertEqual([odd.path, odd.providers[0].name, odd.providers[0].enabled, odd.providers[0].fields.map(f => f.option),
        odd.providers[0].switches.map(f => f.option), optionTitle('flag')],
    [null, 'x', null, ['n'], ['flag'], 'flag'], 'unknown provider and option types');

    for (const bad of ['', 'not json', '{}', '[]', '{"providers": null}']) {
        let threw = false;
        try {
            parseConfigShow(bad);
        } catch {
            threw = true;
        }
        assertEqual(threw, true, `parseConfigShow(${JSON.stringify(bad)}) throws`);
    }
    console.log('[Test] aiu config show --json is read into rows ✅');
}

// What aiu config show --json prints (tests/test_config_cmd.py records it): a
// field renamed in aiu/config.py or here alone fails one side (docs/json-schema.md).
function testContract() {
    const config = parseConfigShow(readFixture('config-show.json'));
    assertEqual(config.path, '/home/me/.config/ai-usage/config.toml', 'contract: path');
    assertEqual([config.keyCmdAllowed, config.keyCmdProblems],
        [false, ['/home/me/.config/ai-usage/config.toml is writable by group or others']], 'contract: trust check');
    assertEqual(config.providers.map(p => [p.id, p.enabled, p.hasKeySources]), [
        ['claude', true, false],
        ['codex', false, false],
        ['openai', false, true],
        ['deepseek', false, true],
        ['openrouter', false, true],
        ['gemini', false, false],
        ['mistral', true, true],
    ], 'contract: providers');
    assertEqual(config.providers.flatMap(p => p.fields.filter(f => f.text || f.fromFile)
        .map(f => [p.id, f.option, f.type, f.text, f.fromFile, f.editable])), [
        ['openai', 'key_env', 'string', 'OPENAI_ADMIN_KEY', true, true],
        ['mistral', 'vibe_allowance_eur', 'number', '8.5', false, true],
        ['mistral', 'api_allowance_eur', 'number', '5', true, true],
    ], 'contract: values and where they come from');
    assertEqual(config.providers.flatMap(p => p.switches.map(f => [p.id, f.option, f.active, f.fromFile])),
        [['claude', 'read_token', false, false]], 'contract: switches, off by default');
    const readOnly = config.providers.flatMap(p => p.fields.filter(f => !f.editable).map(f => `${p.id}.${f.option}`));
    assertEqual(readOnly, ['claude.claude_cmd', 'codex.codex_cmd', 'openai.key_cmd', 'deepseek.key_cmd',
        'openrouter.key_cmd', 'gemini.agy_cmd', 'mistral.key_cmd'], 'contract: commands are read-only in an untrusted config');

    // The status lines read aiu --json too (tests/test_json_output.py records it).
    const snapshot = JSON.parse(readFixture('snapshot.json'));
    assertEqual(config.providers.map(p => providerStatus({ ...p, enabled: true }, snapshot)), [
        { state: 'ok' }, { state: 'ok' }, { state: 'ok' }, { state: 'ok' },
        { state: 'error', note: 'network: cannot connect' }, { state: 'ok' },
        { state: 'error', note: 'session rejected (307) — copy a fresh Cookie header from admin.mistral.ai' },
    ], 'contract: status of each provider');

    // A schema it does not know is a clear message, not a parse error; none at all is schema 1.
    const raw = JSON.parse(readFixture('config-show.json'));
    for (const [schema, words] of [[2, 'update the extension'], [0, 'update aiu'], ['1', 'update aiu']]) {
        let error = null;
        try {
            parseConfigShow(JSON.stringify({ ...raw, schema }));
        } catch (err) {
            error = err;
        }
        assertEqual([error instanceof SchemaError, !!error?.message.includes(words)], [true, true],
            `schema ${JSON.stringify(schema)}: ${error?.message}`);
    }
    delete raw.schema;
    assertEqual(parseConfigShow(JSON.stringify(raw)), config, 'an aiu without schema reads as schema 1');
    console.log('[Test] aiu config show --json fixture read through parseConfigShow ✅');
}

function testValues() {
    const text = { type: 'string' };
    const number = { type: 'number' };
    // Where aiu finds a key is fine.
    for (const ok of ['OPENAI_ADMIN_KEY', 'MISTRAL_ADMIN_COOKIE', '',
        'secret-tool lookup service aiu provider openai', 'pass show api/openai',
        '~/.config/openai/key', '/home/me/keys/deepseek.txt', '/usr/local/bin/claude']) {
        assertEqual(looksLikeSecret(ok), false, `"${ok}" is not a key`);
        assertEqual(fieldValue(text, ` ${ok} `), { value: ok }, `"${ok}" is saved trimmed`);
    }
    // A key itself is refused and never saved.
    for (const key of ['sk-admin-abc', 'sk-or-v1-0123', 'AIzaSyA0123456789', 'ghp_0123abcd', 'hf_abcdef',
        'xoxb-123', 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8', 'Zx9_Qw8-Er7tY6uI5oP4aS3dF2gH1jK0lMnB']) {
        assertEqual(looksLikeSecret(key), true, `"${key}" is a key`);
        const res = fieldValue(text, key);
        assertEqual([res.value, res.secret, typeof res.error], [undefined, true, 'string'], `"${key}" is refused`);
    }
    assertEqual(fieldValue(number, ' 12,5 '), { value: '12.5' }, 'decimal comma');
    assertEqual(fieldValue(number, '0'), { value: '0' }, 'zero');

    assertEqual(setArgv('/home/me/.local/bin/aiu', 'openai', 'enabled', true),
        ['/home/me/.local/bin/aiu', 'config', 'set', '--', 'openai.enabled', 'true'], 'set enabled');
    assertEqual(setArgv('aiu', 'openai', 'key_cmd', '-x; rm -rf ~'),
        ['aiu', 'config', 'set', '--', 'openai.key_cmd', '-x; rm -rf ~'], 'a value is one argument, after --');
    assertEqual(setArgv('aiu', 'openai', 'key_env', ''), ['aiu', 'config', 'set', '--', 'openai.key_env', ''], 'empty turns off');
    assertEqual(showArgv('aiu'), ['aiu', 'config', 'show', '--json'], 'show');
    console.log('[Test] Values for aiu config set ✅');
}

function testStatus() {
    const on = { id: 'claude', enabled: true };
    const snapshot = {
        rows: [
            { provider: 'claude', metric: 'Weekly', kind: 'quota', used: 0.4 },
            { provider: 'codex', metric: '—', kind: 'error', note: 'not signed in: run codex login' },
            { provider: 'deepseek', metric: 'Balance', kind: 'balance', used: 6 },
            { provider: 'deepseek', metric: '—', kind: 'error', note: 'partial' },
        ],
    };
    const cases = [
        [{ id: 'claude', enabled: false }, snapshot, { state: 'off' }, 'Off'],
        [on, null, { state: 'checking' }, 'Checking…'],
        [on, { rows: null }, { state: 'unknown' }, 'Status unknown'],
        [on, snapshot, { state: 'ok' }, 'Signed in'],
        [{ id: 'deepseek', enabled: true }, snapshot, { state: 'ok' }, 'Signed in'],
        [{ id: 'codex', enabled: true }, snapshot, { state: 'error', note: 'not signed in: run codex login' },
            'not signed in: run codex login'],
        [{ id: 'gemini', enabled: true }, snapshot, { state: 'unread' }, 'Not in the last check: check again'],
    ];
    for (const [provider, snap, want, text] of cases) {
        const got = providerStatus(provider, snap);
        assertEqual(got, want, `status of ${provider.id}`);
        assertEqual(statusText(got), text, `status text of ${provider.id}`);
    }
    console.log('[Test] Status from the last snapshot ✅');
}

function testInterval() {
    const cases = [[300, 300], [59, 60], [0, 60], [-5, 60], [90.4, 90], [1e9, REFRESH_MAX_SECONDS],
        [NaN, REFRESH_DEFAULT_SECONDS], [undefined, REFRESH_DEFAULT_SECONDS], ['600', 600]];
    for (const [given, want] of cases) {
        assertEqual(clampInterval(given), want, `clampInterval(${given})`);
    }
    assertEqual([intervalMinutes(300), intervalMinutes(60), intervalMinutes(89), intervalMinutes(90), intervalMinutes(1)],
        [5, 1, 1, 2, 1], 'minutes');

    // The schema the extension reads agrees with the constants above.
    const source = Gio.SettingsSchemaSource.new_from_directory('schemas', null, true);
    const key = source.lookup(SCHEMA_ID, false).get_key(REFRESH_KEY);
    assertEqual(key.get_default_value().unpack(), REFRESH_DEFAULT_SECONDS, 'schema default');
    const [kind, range] = key.get_range().deepUnpack();
    assertEqual([kind, range.deepUnpack()], ['range', [REFRESH_MIN_SECONDS, REFRESH_MAX_SECONDS]], 'schema range');
    console.log('[Test] Refresh interval and its schema ✅');
}

function waitExit(proc) {
    return new Promise((resolve) => {
        proc.wait_async(null, (p, res) => {
            p.wait_finish(res);
            resolve(p.get_if_signaled());
        });
    });
}

function assertGone(pid, what) {
    if (GLib.file_test(`/proc/${pid}`, GLib.FileTest.EXISTS)) {
        throw new Error(`${what}: aiu (pid ${pid}) is still running`);
    }
}

function onlyRun(runner) {
    const [run] = runner._runs;
    return run;
}

async function testRunner() {
    let runner = new AiuRunner();
    let res = await runner.run(['sh', '-c', 'echo "{}"; echo note >&2']);
    assertEqual([res.ok, res.stdout, res.stderr, res.error], [true, '{}\n', 'note\n', undefined], 'success');

    res = await runner.run(['sh', '-c', 'echo "aiu config: unknown provider" >&2; exit 1']);
    assertEqual([res.ok, res.error], [false, 'aiu config: unknown provider'], 'stderr of a refused set');
    res = await runner.run(['sh', '-c', 'exit 3']);
    assertEqual([res.ok, res.error], [false, 'aiu stopped with status 3'], 'no stderr');

    // argv only: nothing goes through a shell.
    res = await runner.run(['echo', '$(id) `id` ; id']);
    assertEqual(res.stdout, '$(id) `id` ; id\n', 'no shell');

    res = await runner.run(['/nonexistent/aiu', 'config', 'show']);
    assertEqual([res.ok, res.error.startsWith('Could not start aiu')], [false, true], 'missing program');

    console.log('[Test] The timeout kills a hung aiu...');
    let pending = runner.run(['sleep', '60'], { timeoutSeconds: 1 });
    let run = onlyRun(runner);
    let pid = run.proc.get_identifier();
    res = await pending;
    assertEqual([res.ok, res.timedOut, res.error.includes('1 s')], [false, true, true], 'timeout');
    assertEqual(await waitExit(run.proc), true, 'killed by a signal');
    assertGone(pid, 'timeout');
    assertEqual(runner.busy, false, 'nothing left running');

    console.log('[Test] cancelAll() (the window closing) kills every aiu...');
    runner = new AiuRunner();
    const all = [runner.run(['sleep', '60']), runner.run(['sleep', '60'])];
    const runs = [...runner._runs];
    assertEqual(runner.busy, true, 'busy');
    runner.cancelAll();
    const results = await Promise.all(all);
    assertEqual(results.map(r => r.cancelled), [true, true], 'cancelled');
    for (const r of runs) {
        pid = r.proc.get_identifier();
        await waitExit(r.proc);
        assertGone(pid, 'cancelAll');
        assertEqual(r.watchdogId, 0, 'watchdog removed');
    }
    assertEqual(runner.busy, false, 'nothing left running');
    console.log('[Test] AiuRunner ✅');
}

let failed = false;
try {
    testParse();
    testContract();
    testValues();
    testStatus();
    testInterval();
} catch (err) {
    console.error('[Test Failed]', err);
    System.exit(1);
}

const loop = GLib.MainLoop.new(null, false);
GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 20, () => {
    console.error('[Test Failed] runner tests timed out');
    failed = true;
    loop.quit();
    return GLib.SOURCE_REMOVE;
});
testRunner().catch((err) => {
    console.error('[Test Failed]', err);
    failed = true;
}).finally(() => loop.quit());
loop.run();
if (failed) {
    System.exit(1);
}
