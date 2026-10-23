IN    ?= photo.jpg
OUT   ?= _logs/$(shell basename "$(IN)" | sed 's/\.[^.]*$$//')-bg.jpg
COLOR ?= \#C8A2C8
ARGS  ?=
PORT  ?= 8765

CACHE := $(CURDIR)/.cache
export PYTHONPYCACHEPREFIX    := $(CACHE)/pycache
export REMBG_HOME             := $(CACHE)/rembg
export NUMBA_CACHE_DIR        := $(CACHE)/numba
export XDG_CACHE_HOME         := $(CACHE)

.PHONY: help install background ui clean clean-cache

help:
	@echo "make install                                   install locked dependencies"
	@echo "make background IN=photo.jpg COLOR='#0D4FE4'    render a photo (OUT defaults to _logs/<name>-bg.jpg)"
	@echo "  extra flags via ARGS, e.g. ARGS='--center 975 340 --shadow 0.15'"
	@echo "make ui                                        open the web frontend (PORT=8765)"

install:
	uv sync --frozen

background:
	PYTHONPATH=src uv run --frozen python -m color_background.cli "$(IN)" "$(OUT)" --color "$(COLOR)" $(ARGS)

ui:
	PYTHONPATH=src uv run --frozen python -m frontend.server $(PORT)

clean:
	rm -rf _logs

clean-cache:
	rm -rf .cache
