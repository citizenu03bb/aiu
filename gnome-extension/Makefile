UUID = aiu-indicator@citizenu03bb.github.io
EXT_DIR = $(HOME)/.local/share/gnome-shell/extensions/$(UUID)
SRC_DIR = $(shell pwd)
JS_FILES = extension.js indicator.js menuView.js aiuClient.js i18n.js
TEST_FILES = $(wildcard tests/*.js)
# gettext domain: "gettext-domain" in metadata.json and DOMAIN in i18n.js.
DOMAIN = aiu-indicator
PO_FILES = $(wildcard po/*.po)
MO_FILES = $(patsubst po/%.po,locale/%/LC_MESSAGES/$(DOMAIN).mo,$(PO_FILES))
# What extensions.gnome.org gets: metadata.json and extension.js at the zip root,
# the modules they import, their assets and the compiled translations.
# No tests, Makefile, README or .po sources.
PACK_FILES = metadata.json stylesheet.css $(JS_FILES) $(wildcard icons/*.svg) $(MO_FILES)
PACK_ZIP = $(UUID).shell-extension.zip

.PHONY: all test test-live mo pot pack clean install uninstall reload status log check

all: check test

check:
	@echo "Checking JS syntax..."
	@for f in $(JS_FILES) $(TEST_FILES); do node --check $$f || exit 1; done
	@echo "Checking metadata.json..."
	@python3 -m json.tool metadata.json > /dev/null
	@echo "Checking translations..."
	@python3 po/compile.py --check $(PO_FILES) -- $(JS_FILES)
	@echo "Syntax OK ✅"

# Compiled translations, built with python3 (no GNU gettext needed).
mo: $(MO_FILES)

locale/%/LC_MESSAGES/$(DOMAIN).mo: po/%.po po/compile.py
	@python3 po/compile.py $< $@

# Template for a new language or after changing strings (needs GNU gettext):
#   make pot && msgmerge -U po/fr.po po/$(DOMAIN).pot
pot:
	@xgettext --from-code=UTF-8 --language=JavaScript --keyword=_ --keyword=N_ \
		--package-name=$(DOMAIN) -o po/$(DOMAIN).pot $(JS_FILES)
	@echo "Wrote po/$(DOMAIN).pot"

# Offline unit tests, run in CI: English, then the French catalog.
test: $(MO_FILES)
	@echo "Running unit tests with GJS..."
	@LC_ALL=C.UTF-8 LANGUAGE= gjs -m tests/test_aiuClient.js
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

install: check $(MO_FILES)
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

log:
	@journalctl /usr/bin/gnome-shell -f -n 50 -o cat | grep --line-buffered -E "aiu-indicator|Aiu"
