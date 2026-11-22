COMPOSE ?= docker compose
PYTHON ?= python3
PROJECT_ROOT ?= $(abspath ..)
TEST_RUNNER = /usr/lib/enigma2/python/Plugins/Extensions/HelloWorld/tests/test-enigma2

.PHONY: test dist

test:
	$(COMPOSE) --project-directory "$(PROJECT_ROOT)" run --rm --no-deps --build -T enigma2 bash "$(TEST_RUNNER)"

dist:
	$(PYTHON) ../tools/build-ipk.py package.control HelloWorld ../dist __init__.py plugin.py plugin.png
