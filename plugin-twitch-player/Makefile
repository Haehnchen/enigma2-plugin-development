PYTHON ?= python3
IPK_FILES = $(wildcard *.py skins/*.xml locale/*/LC_MESSAGES/*.mo) plugin.png mark.png

.PHONY: test dist

test:
	$(PYTHON) -B -m unittest discover -s tests -t .. -v

dist:
	$(PYTHON) ../tools/build-ipk.py package.control TwitchPlayer ../dist $(IPK_FILES)
