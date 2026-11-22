COMPOSE ?= docker compose
PYTHON ?= python3
RUN = $(COMPOSE) --project-directory "$(abspath ..)" run --rm --no-deps --build -T
TEST = enigma2 bash /usr/lib/enigma2/python/Plugins/Extensions/StandbyXL/tests/test-enigma2
SKIN = display/Standby XL

.PHONY: test preview dist

test:
	$(RUN) $(TEST)

preview:
	@set -eu; out=$$(mktemp -d); trap 'rm -rf "$$out"' EXIT; \
		$(RUN) -v "$$out:/preview" $(TEST) /preview; \
		mkdir -p docs; \
		cp "$$out/comparison.webp" docs/; \
		cp "$$out/xl.png" "$(SKIN)/prev.png"

dist:
	$(PYTHON) ../tools/build-ipk.py package.control StandbyXL ../dist __init__.py plugin.py \
		--display-files "$(SKIN)/skin_display.xml" "$(SKIN)/prev.png"
