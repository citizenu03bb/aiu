// SPDX-License-Identifier: GPL-3.0-or-later
// i18n.js - Translations for aiu-indicator: English msgids, po/<lang>.po.
//
// Uses GJS's own gettext module rather than the Shell's, so the unit tests can
// load aiuClient.js outside GNOME Shell. The Shell binds DOMAIN to locale/ when
// it loads the extension (gettext-domain in metadata.json), before enable(), so
// translate when building text, never at module load.

import Gettext from 'gettext';

/** Must match "gettext-domain" in metadata.json. */
export const DOMAIN = 'aiu-indicator';

const domain = Gettext.domain(DOMAIN);

/** The translation of msgid in the user's language. */
export function _(msgid) {
    return domain.gettext(msgid);
}

/** Marks a msgid for extraction (make pot) without translating it yet. */
export function N_(msgid) {
    return msgid;
}

/**
 * Fill {name} placeholders: fmt(_('{percent}% used'), { percent: 40 }).
 * The result is plain text for St.Label({ text }), never markup.
 */
export function fmt(text, fields) {
    return text.replace(/\{(\w+)\}/g, (m, key) => (key in fields ? String(fields[key]) : m));
}
