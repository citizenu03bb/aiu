#!/bin/sh
# Runs test_i18n.js in French. gettext ignores LANGUAGE under the C locale, so
# this needs a real one: any installed UTF-8 locale, else fr_FR compiled into a
# temporary directory (needs localedef and the glibc locale sources).
set -e
cd "$(dirname "$0")/.."
loc=$(locale -a 2>/dev/null | grep -iE '\.utf-?8$' | grep -ivE '^c\.' | head -n1)
if [ -z "$loc" ]; then
    LOCPATH=$(mktemp -d)
    trap 'rm -rf "$LOCPATH"' EXIT
    # -c: warnings about the locale sources are not failures
    localedef -c -i fr_FR -f UTF-8 "$LOCPATH/fr_FR.UTF-8" || [ -e "$LOCPATH/fr_FR.UTF-8/LC_MESSAGES" ]
    export LOCPATH
    loc=fr_FR.UTF-8
fi
LC_ALL=$loc LANGUAGE=fr gjs -m tests/test_i18n.js
