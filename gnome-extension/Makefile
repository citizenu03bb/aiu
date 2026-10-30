UUID = aiu-indicator@citizenu03bb.github.io
EXT_DIR = $(HOME)/.local/share/gnome-shell/extensions/$(UUID)
SRC_DIR = $(shell pwd)
JS_FILES = extension.js indicator.js menuView.js aiuClient.js
TEST_FILES = $(wildcard tests/*.js)
# What extensions.gnome.org gets: metadata.json and extension.js at the zip root,
# the modules they import and their assets. No tests, Makefile or README.
PACK_FILES = metadata.json stylesheet.css $(JS_FILES) $(wildcard icons/*.svg)
PACK_ZIP = $(UUID).shell-extension.zip

.PHONY: all test test-live pack clean install uninstall reload status log check

all: check test

check:
	@echo "Checking JS syntax..."
	@for f in $(JS_FILES) $(TEST_FILES); do node --check $$f || exit 1; done
	@echo "Checking metadata.json..."
	@python3 -m json.tool metadata.json > /dev/null
	@echo "Syntax OK ✅"

# Offline unit tests, run in CI.
test:
	@echo "Running unit tests with GJS..."
	@gjs -m tests/test_aiuClient.js

# Calls the real aiu (and so your providers): local only, never in CI.
test-live:
	@echo "Running live test against the installed aiu..."
	@gjs -m tests/test_aiuClient_live.js

# Same layout as `gnome-extensions pack`, built with zip so it runs without
# GNOME Shell installed (CI). -X drops extra file attributes.
pack: check
	@rm -f $(PACK_ZIP)
	@zip -q -X $(PACK_ZIP) $(PACK_FILES)
	@echo "Built $(PACK_ZIP)"

clean:
	@rm -f $(PACK_ZIP)

install: check
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
