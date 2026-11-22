COMPOSE ?= docker compose
UI_PORT ?= 6080
UI_URL = http://127.0.0.1:$(UI_PORT)/vnc.html?autoconnect=true&resize=scale
BROWSER ?= xdg-open
PLUGIN_DIRS = $(dir $(wildcard plugin-*/Makefile))

export UI_PORT

.PHONY: start stop ui restart dist screenshot

start:
	$(COMPOSE) up --build

stop:
	$(COMPOSE) stop enigma2

ui:
	@printf '%s\n' '$(UI_URL)'
	@$(BROWSER) '$(UI_URL)'

restart:
	$(COMPOSE) restart enigma2

dist:
	@set -e; for plugin in $(PLUGIN_DIRS); do \
		$(MAKE) --no-print-directory -C "$$plugin" "$@"; \
	done

screenshot:
	$(COMPOSE) exec -T enigma2 import -window root png:- > enigma2.png
