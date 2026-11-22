PYTHON ?= python3
COMPOSE ?= docker compose
IPK_FILES = $(wildcard *.py skins/*.xml locale/*/LC_MESSAGES/*.mo) plugin.png

.PHONY: test test-picker dist

test:
	$(PYTHON) -B -m unittest discover -s tests -t .. -v

test-picker:
	$(COMPOSE) --project-directory .. run --rm --no-deps --build -T \
		-v "$(CURDIR)/tests/pair-picker:/usr/lib/enigma2/python/Plugins/Extensions/PairPickerTest:ro" \
		enigma2 timeout --kill-after=5s 45s xvfb-run -a enigma2

dist:
	$(PYTHON) ../tools/build-ipk.py package.control CryptoMarket ../dist $(IPK_FILES)
