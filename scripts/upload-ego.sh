#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Submit an already-tested ZIP. EGO approval is a separate step.
set +x
set -euo pipefail

if [[ $# != 1 || ! -f "$1" || "$1" != *.shell-extension.zip ]]; then
    echo 'Usage: upload-ego.sh FILE.shell-extension.zip' >&2
    exit 2
fi
if [[ -z "${EGO_USERNAME:-}" || -z "${EGO_PASSWORD:-}" ]]; then
    echo 'Set EGO_USERNAME and EGO_PASSWORD before submitting to EGO.' >&2
    exit 2
fi

# No password in argv or in the uploader's environment, including with bash -x.
printf '%s' "$EGO_PASSWORD" | /usr/bin/env -u EGO_PASSWORD -u EGO_USERNAME \
    gnome-extensions upload --accept-tos --user "$EGO_USERNAME" --password-file=- "$1"
