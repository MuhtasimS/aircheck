# AIRCheck — convenience wrapper over the cross-platform task runner
# (scripts/project.py). The runner is the source of truth for every step; these
# targets exist so `make install test demo` works as the canonical entrypoint.
# On Windows without GNU make, call the runner directly, e.g.
#   python scripts/project.py install && python scripts/project.py test
PY ?= python

.PHONY: install test lint build verify demo all

install:
	$(PY) scripts/project.py install

test:
	$(PY) scripts/project.py test

lint:
	$(PY) scripts/project.py lint

build:
	$(PY) scripts/project.py build

verify:
	$(PY) scripts/project.py verify

demo:
	$(PY) scripts/project.py demo

all: install verify demo
