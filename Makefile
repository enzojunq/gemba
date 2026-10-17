# Gemba make targets (spec section 19). Run with `make <target>`; uses uv.
SCENARIO ?= reslot_default
PROFILE  ?= customer_first
PLAYBOOK ?= v0
POLICY   ?= org
SEED     ?= 1

.PHONY: install test data baselines run fork eval retro console demo

install:
	uv sync --extra dev

test:
	uv run pytest -q

data:
	uv run python -m data.gen --seed $(SEED) --verify

baselines:
	uv run python -m eval.run_eval --contenders do_nothing,big_bang,static --scenario $(SCENARIO) --profile $(PROFILE)

run:
	uv run python -m engine.run --scenario $(SCENARIO) --profile $(PROFILE) --playbook $(PLAYBOOK) --policy $(POLICY) --seed $(SEED)

fork:
	uv run python -m engine.fork --run $(RUN) --day $(DAY) --levers '$(LEVERS)'

eval:
	uv run python -m eval.run_eval --scenario $(SCENARIO) --profile $(PROFILE)

retro:
	uv run python -m learn.retro --playbook $(PLAYBOOK)

console:
	uv run uvicorn console.server:app --reload --port 8000

demo:
	RUN_STORE_URL=sqlite:///recordings/hero.db uv run uvicorn console.server:app --port 8000
