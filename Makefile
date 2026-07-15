UV      := uv
PROFILE ?=
PROFILE_DIR := data/profiles/$(PROFILE)
PROFILE_ARG := $(if $(PROFILE),--profile $(PROFILE),)

.PHONY: setup run dashboard clean reset-db help score-only

## setup  — create the uv-managed environment and install dependencies
setup:
	@command -v $(UV) >/dev/null || (echo "ERROR: uv is required. Install it from https://docs.astral.sh/uv/" && exit 1)
	$(UV) venv .venv
	$(UV) pip sync --python .venv/bin/python requirements.txt
	@echo "\n✓ Setup complete. Run: make run"

## run    — start Ollama (if not running) and execute the pipeline. caffeinate prevents sleep on macOS while the script is running.
run:
	@caffeinate -i bash start.sh $(PROFILE_ARG)

## dashboard — launch the local JobSiphon operations dashboard
dashboard:
	@caffeinate -i $(UV) run python dashboard_server.py

## clean  — remove generated output files (keep DB and venv)
clean:
	@test -n "$(PROFILE)" || (echo "ERROR: set PROFILE=<slug>" && exit 1)
	rm -f $(PROFILE_DIR)/apply_list.md $(PROFILE_DIR)/apply_list.csv
	@echo "✓ Output files removed."

## reset-db — clear the seen-jobs database so all URLs are re-evaluated
reset-db:
	@test -n "$(PROFILE)" || (echo "ERROR: set PROFILE=<slug>" && exit 1)
	rm -f $(PROFILE_DIR)/seen_jobs.db
	@echo "✓ Seen-jobs database cleared."

## help   — show this message
help:
	@echo ""
	@echo "  make setup     Create uv environment + install dependencies"
	@echo "  make run PROFILE=<slug>       Run the selected job profile"
	@echo "  make dashboard Open the local operations dashboard"
	@echo "  make clean PROFILE=<slug>     Remove that profile's current outputs"
	@echo "  make reset-db PROFILE=<slug>  Clear that profile's seen-job history"
	@echo ""

## score-only — skip scraping and run AI scoring on the cached data
score-only:
	@caffeinate -i bash start.sh --score-only $(PROFILE_ARG)
