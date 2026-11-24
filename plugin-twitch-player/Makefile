PYTHON ?= python3
IPK_FILES = $(wildcard *.py skins/*.xml locale/*/LC_MESSAGES/*.mo) plugin.png mark.png

.PHONY: test test-bouquet dist

test:
	$(PYTHON) -B -m unittest discover -s tests -t .. -v

test-bouquet:
	docker compose --project-directory .. run --rm --no-deps -T -e ENIGMA2_TWITCH_CONFIG=/tmp/twitch-test enigma2 bash /usr/lib/enigma2/python/Plugins/Extensions/TwitchPlayer/tests/test-bouquet

dist:
	$(PYTHON) ../tools/build-ipk.py package.control TwitchPlayer ../dist $(IPK_FILES)
