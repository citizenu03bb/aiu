#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile and check the extension's translations without GNU gettext installed.

  compile.py po/fr.po locale/fr/LC_MESSAGES/aiu-indicator.mo
  compile.py --check po/*.po -- extension.js menuView.js ...

--check fails when a .po misses a string the JS translates, keeps one it no
longer uses, leaves one untranslated or fuzzy, or changes its {placeholders}.
Only plain msgid/msgstr entries are supported (no plurals, no contexts)."""
import ast
import re
import struct
import sys
from pathlib import Path

# _('…') or N_('…') with a literal argument, in either quote style.
JS_MSGID_RE = re.compile(r"""\bN?_\(\s*('(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")\s*\)""")
FIELD_RE = re.compile(r"\{(\w+)\}")


def read_po(path: Path) -> dict[str, tuple[str, bool]]:
    """{msgid: (msgstr, fuzzy)}, the header under ""."""
    entries: dict[str, tuple[str, bool]] = {}
    msgid = msgstr = None
    field = None
    fuzzy = False

    def flush():
        nonlocal msgid, msgstr, fuzzy
        if msgid is not None:
            if msgid in entries:
                sys.exit(f"{path}: duplicate msgid {msgid!r}")
            entries[msgid] = (msgstr or "", fuzzy)
        msgid = msgstr = None
        fuzzy = False

    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            if msgstr is not None:
                flush()
            if line.startswith("#,") and "fuzzy" in line:
                fuzzy = True
            continue
        key, _, rest = line.partition(" ")
        if key == "msgid":
            if msgstr is not None:
                flush()
            msgid, field = ast.literal_eval(rest), "msgid"
        elif key == "msgstr":
            msgstr, field = ast.literal_eval(rest), "msgstr"
        elif line.startswith('"') and field:
            if field == "msgid":
                msgid += ast.literal_eval(line)
            else:
                msgstr += ast.literal_eval(line)
        else:
            sys.exit(f"{path}:{n}: unsupported line: {line}")
    flush()
    return entries


def write_mo(entries: dict[str, tuple[str, bool]], out: Path) -> None:
    """GNU .mo, as msgfmt writes it: fuzzy and untranslated entries left out."""
    msgs = {k.encode(): v.encode() for k, (v, fuzzy) in entries.items()
            if v and (not fuzzy or k == "")}
    keys = sorted(msgs)
    ids = strs = b""
    offsets = []
    for k in keys:
        offsets.append((len(ids), len(k), len(strs), len(msgs[k])))
        ids += k + b"\0"
        strs += msgs[k] + b"\0"
    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)
    table = []
    for o1, l1, _, _ in offsets:
        table += [l1, o1 + keystart]
    for _, _, o2, l2 in offsets:
        table += [l2, o2 + valuestart]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(struct.pack("<7I", 0x950412DE, 0, len(keys), 7 * 4, 7 * 4 + len(keys) * 8, 0, 0)
                    + struct.pack(f"<{len(table)}I", *table) + ids + strs)


def js_msgids(paths: list[Path]) -> set[str]:
    found = set()
    for p in paths:
        for m in JS_MSGID_RE.finditer(p.read_text(encoding="utf-8")):
            lit = m.group(1)
            # JS and Python agree on the escapes these strings use (\' \" \\ \n).
            found.add(ast.literal_eval(lit))
    return found


def check(pos: list[Path], sources: list[Path]) -> int:
    used = js_msgids(sources)
    errors = []
    for po in pos:
        entries = read_po(po)
        entries.pop("", None)
        for m in sorted(used - entries.keys()):
            errors.append(f"{po}: missing {m!r}")
        for m in sorted(entries.keys() - used):
            errors.append(f"{po}: unused {m!r}")
        for m, (s, fuzzy) in entries.items():
            if not s or fuzzy:
                errors.append(f"{po}: untranslated or fuzzy {m!r}")
            elif set(FIELD_RE.findall(s)) != set(FIELD_RE.findall(m)):
                errors.append(f"{po}: placeholders differ in {m!r}")
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--check"]:
        rest = argv[1:]
        split = rest.index("--") if "--" in rest else len(rest)
        return check([Path(p) for p in rest[:split]], [Path(p) for p in rest[split + 1:]])
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    write_mo(read_po(Path(argv[0])), Path(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
