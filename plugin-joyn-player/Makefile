COMPOSE ?= docker compose
PYTHON ?= python3
IPK_FILES = $(wildcard *.py skins/*.xml locale/*/LC_MESSAGES/*.mo) plugin.png keymap.xml
RUNTIME_FILES = $(shell find .runtime -type f ! -name '.ready' ! -name '*.pyc' 2>/dev/null)
BACKEND_FILES = $(wildcard backend/*.py)

.PHONY: test test-gui dist runtime catalog
test:
	$(PYTHON) -B -m unittest discover -s tests -v
test-gui: .build/gui-test.ts
	$(COMPOSE) --project-directory .. run --rm --no-deps -T enigma2 bash /usr/lib/enigma2/python/Plugins/Extensions/JoynPlayer/tests/test-enigma2

.build/gui-test.ts:
	mkdir -p .build
	ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=320x180:rate=25 -f lavfi -i sine=frequency=440 -t 10 -c:v libx264 -threads 2 -c:a aac -f mpegts $@

runtime: .runtime/.ready
.runtime/.ready: tools/prepare-runtime.py tools/runtime-lock.json tools/cross-build.sh tools/0001-add-start-position.patch
	$(PYTHON) tools/prepare-runtime.py
	touch $@
catalog:
	$(PYTHON) tools/prepare-runtime.py --catalog-only
dist: runtime
	@$(PYTHON) ../tools/build-ipk.py package.control JoynPlayer ../dist $(IPK_FILES) $(BACKEND_FILES) $(RUNTIME_FILES) --executable-files .runtime/dash2ts
