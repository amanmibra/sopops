# The walkthrough, one target per step. Run from this folder (`make <step>`) or the repo root (`make -C examples/walkthrough <step>`).
# MODELS=fake (default) or real. `make serve` in one terminal, the steps in another; or `make all` for everything.

MODELS ?= fake
PY := uv run --project ../.. python

.PHONY: all serve fresh onboard onboard-push onboard-publish generate backfill review replay verify clean

all:              ## everything, from a fresh database; sopops stays up afterwards
	$(PY) run_all.py --models $(MODELS)

serve:            ## 1. sopops on 127.0.0.1:8585 with the walkthrough's database
	$(PY) serve.py --models $(MODELS)

fresh:            ## 1. the same, from an empty database
	$(PY) serve.py --models $(MODELS) --fresh

onboard:          ## 2. no coding agent: push the prepared proposal, resolve, apply, publish release 1
	$(PY) onboard.py

onboard-push:     ## 2a. just push the proposal, to review it in the UI
	$(PY) onboard.py push

onboard-publish:  ## 2b. resolve the drift, apply and publish release 1
	$(PY) onboard.py publish

generate:         ## 4. 200 backfill calls, 100 new ones, and the ground truth
	$(PY) generate.py

backfill:         ## 5. send the backfill
	$(PY) backfill.py

review:           ## 6. verdicts on the patterns, suggestions applied, release 2
	$(PY) review.py

replay:           ## 7. the new calls through the LiveKit adapter, as if live
	$(PY) replay.py

verify:           ## 8. the checklist
	$(PY) verify.py --models $(MODELS)

clean:            ## remove out/ (the database, generated calls, tokens)
	rm -rf out
