UUID = aiu-indicator@pat.local
EXT_DIR = $(HOME)/.local/share/gnome-shell/extensions/$(UUID)
SRC_DIR = $(shell pwd)

.PHONY: all test install uninstall reload restart-shell log check

all: check test

check:
	@echo "Checking JS syntax..."
	@node --check extension.js indicator.js menuView.js aiuClient.js tests/test_aiuClient.js
	@echo "Checking metadata.json..."
	@python3 -m json.tool metadata.json > /dev/null
	@echo "Syntax OK ✅"

test:
	@echo "Running unit tests with GJS..."
	@gjs -m tests/test_aiuClient.js

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
