UUID = aiu-indicator@citizenu03bb.github.io
EXT_DIR = $(HOME)/.local/share/gnome-shell/extensions/$(UUID)
SRC_DIR = $(shell pwd)
JS_FILES = extension.js indicator.js menuView.js menuLayout.js aiuClient.js i18n.js \
	prefs.js prefsWindow.js aiuConfig.js
TEST_FILES = $(wildcard tests/*.js)
# gettext domain: "gettext-domain" in metadata.json and DOMAIN in i18n.js.
DOMAIN = aiu-indicator
PO_FILES = $(wildcard po/*.po)
MO_FILES = $(patsubst po/%.po,locale/%/LC_MESSAGES/$(DOMAIN).mo,$(PO_FILES))
# GSettings schema (refresh interval). GNOME 44+ compiles it when it installs
# the zip, and extensions.gnome.org wants only the XML for GNOME 45+; the
# compiled file is for a checkout linked in place (make install) and the tests.
SCHEMA_XML = $(wildcard schemas/*.gschema.xml)
SCHEMA_COMPILED = schemas/gschemas.compiled
# What extensions.gnome.org gets: metadata.json and extension.js at the zip root,
# the modules they import, their assets and the compiled translations.
# No tests, Makefile, README, .po sources or compiled schema.
PACK_FILES = metadata.json stylesheet.css $(JS_FILES) $(wildcard icons/*.svg) $(MO_FILES) $(SCHEMA_XML)
PACK_ZIP = $(UUID).shell-extension.zip

.PHONY: all test test-live mo schemas pot pack clean install uninstall reload status log check prefs

all: check test

check:
	@echo "Checking JS syntax..."
	@for f in $(JS_FILES) $(TEST_FILES); do node --check $$f || exit 1; done
	@echo "Checking metadata.json..."
	@python3 -m json.tool metadata.json > /dev/null
	@echo "Checking the settings schema..."
	@glib-compile-schemas --strict --dry-run schemas
	@echo "Checking translations..."
	@python3 po/compile.py --check $(PO_FILES) -- $(JS_FILES)
	@echo "Syntax OK ✅"

# Compiled translations, built with python3 (no GNU gettext needed).
mo: $(MO_FILES)

locale/%/LC_MESSAGES/$(DOMAIN).mo: po/%.po po/compile.py
	@python3 po/compile.py $< $@

schemas: $(SCHEMA_COMPILED)

$(SCHEMA_COMPILED): $(SCHEMA_XML)
	@glib-compile-schemas --strict schemas

# Template for a new language or after changing strings (needs GNU gettext):
#   make pot && msgmerge -U po/fr.po po/$(DOMAIN).pot
pot:
	@xgettext --from-code=UTF-8 --language=JavaScript --keyword=_ --keyword=N_ \
		--package-name=$(DOMAIN) -o po/$(DOMAIN).pot $(JS_FILES)
	@echo "Wrote po/$(DOMAIN).pot"

# Offline unit tests, run in CI: English, then the French catalog.
test: $(MO_FILES) $(SCHEMA_COMPILED)
	@echo "Running unit tests with GJS..."
	@LC_ALL=C.UTF-8 LANGUAGE= gjs -m tests/test_aiuClient.js
	@LC_ALL=C.UTF-8 LANGUAGE= gjs -m tests/test_aiuConfig.js
	@LC_ALL=C.UTF-8 LANGUAGE= gjs -m tests/test_menuLayout.js
	@sh tests/run_french.sh

# Calls the real aiu (and so your providers): local only, never in CI.
test-live:
	@echo "Running live test against the installed aiu..."
	@gjs -m tests/test_aiuClient_live.js

# Same layout as `gnome-extensions pack`, built with zip so it runs without
# GNOME Shell installed (CI). -X drops extra file attributes.
pack: check $(MO_FILES)
	@rm -f $(PACK_ZIP)
	@zip -q -X $(PACK_ZIP) $(PACK_FILES)
	@echo "Built $(PACK_ZIP)"

clean:
	@rm -f $(PACK_ZIP)
	@rm -rf locale
	@rm -f $(SCHEMA_COMPILED)

install: check $(MO_FILES) $(SCHEMA_COMPILED)
	@echo "Installing extension symlink to $(EXT_DIR)..."
	@mkdir -p $(HOME)/.local/share/gnome-shell/extensions
	@rm -rf $(EXT_DIR)
	@ln -s $(SRC_DIR) $(EXT_DIR)
	@echo "Enabling extension $(UUID)..."
	@gnome-extensions enable $(UUID) 2>/dev/null || true
	@echo "Done. Verify status with: make status"

uninstall:
	@echo "Disabling extension $(UUID)..."
	@gnome-extensions disable $(UUID) 2>/dev/null || true
	@echo "Removing symlink $(EXT_DIR)..."
	@rm -f $(EXT_DIR)
	@echo "Uninstalled."

reload:
	@echo "Reloading extension $(UUID)..."
	@gnome-extensions disable $(UUID) 2>/dev/null || true
	@sleep 0.5
	@gnome-extensions enable $(UUID) 2>/dev/null || true
	@echo "Reloaded."

status:
	@gnome-extensions info $(UUID)

# Opens the preferences window of the installed extension.
prefs:
	@gnome-extensions prefs $(UUID)

log:
	@journalctl /usr/bin/gnome-shell -f -n 50 -o cat | grep --line-buffered -E "aiu-indicator|Aiu"
