# Gemba: a decision twin for operations

Simulates a warehouse **and the people who run it**. A deterministic floor twin replays orders, labor and equipment hour by hour; an org twin of LLM agents holds a daily standup, argues with simulator evidence, and decides through typed levers. Decisions cascade, any day can be forked, and a retro coach improves the org's playbook on unseen scenarios.

Built at the Crusoe Recursive Agents Hackathon, Oct 17 2026.

## Read first
- `PLATFORM_SPEC.md`: what we're building and why (Part A), architecture (Part B), build plan (Part C), decision log (Part D).
- `CONTRACT.md`: the A/B boundary. Person A owns `data/ floor/ scenarios/ console/`; Person B owns `llm/ org/ engine/ eval/ learn/`. `contracts.py` is the only shared code.
- GitHub issues: #1 parent spec, #2 Person A, #3 Person B.

## Setup
```
cp .env.example .env      # fill CRUSOE_API_KEY, CRUSOE_BASE_URL, MODEL_ULTRA/SUPER/NANO
make install              # uv sync --extra dev
make test
```

## Run
```
make data SEED=1                         # generate inputs.db and verify the 8 traps
make run POLICY=static                   # 30-day run, no LLM
make run POLICY=org PROFILE=customer_first
make fork RUN=<id> DAY=69 LEVERS='[...]'
make eval
make retro
make console                             # http://localhost:8000
make demo                                # replay recordings/hero.db offline
```
