COMPOSE ?= docker compose
PYTHON ?= python3
IPK_FILES = $(wildcard *.py skins/*.xml locale/*/LC_MESSAGES/*.mo) plugin.png keymap.xml
RUNTIME_FILES = $(shell find .runtime -type f ! -name '.ready' ! -name '*.pyc' 2>/dev/null)
BACKEND_FILES = $(wildcard backend/*.py)

.PHONY: test test-gui test-fixture dist runtime catalog
test:
	$(PYTHON) -B -m unittest discover -s tests -v
test-gui: .build/gui-test.ts
	$(COMPOSE) --project-directory .. run --rm --no-deps -T enigma2 bash /usr/lib/enigma2/python/Plugins/Extensions/JoynPlayer/tests/test-enigma2

.build/gui-test.ts:
	mkdir -p .build
	ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=320x180:rate=25 -f lavfi -i sine=frequency=440 -t 10 -c:v libx264 -threads 2 -c:a aac -f mpegts $@

test-fixture:
	mkdir -p .build/test-dash
	ffmpeg -y -nostdin -v error -f lavfi -i testsrc2=size=320x180:rate=24 -f lavfi -i sine=sample_rate=48000 \
		-t 8 -map 0:v -map 0:v -map 1:a -c:v libx264 -threads 2 -preset veryfast -bf 3 -g 24 -sc_threshold 0 \
		-b:v:0 400k -s:v:1 160x90 -b:v:1 100k -c:a aac -f dash -seg_duration 1 \
		-format_options movflags=+negative_cts_offsets -adaptation_sets 'id=0,streams=0 id=1,streams=1 id=2,streams=2' \
		.build/test-dash/test.mpd

runtime: .runtime/.ready
.runtime/.ready: tools/prepare-runtime.py tools/runtime-lock.json tools/cross-build.sh $(wildcard tools/*.patch)
	$(PYTHON) tools/prepare-runtime.py
	touch $@
catalog:
	$(PYTHON) tools/prepare-runtime.py --catalog-only
dist: runtime
	@$(PYTHON) ../tools/build-ipk.py package.control JoynPlayer ../dist $(IPK_FILES) $(BACKEND_FILES) $(RUNTIME_FILES) --executable-files .runtime/dash2ts
