# tools/hermes-skills/Makefile — deploy versioned Hermes skills to the runtime dir.
#
# Convention:
#   - tools/hermes-skills/<skill>/  — source of truth (versioned in git)
#   - ~/.hermes/skills/ratis/<skill>/ — runtime copy read by Hermes / the cron
#
# Usage:
#   make deploy             # rsync every skill subdir to ~/.hermes/skills/ratis/
#   make deploy SKILL=X     # only deploy skill X
#   make diff               # show what would change without writing
#   make test               # run pytest in every skill that has tests/
#   make clean-runtime      # remove the runtime copy (careful — state files live elsewhere)

HERE          := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
RUNTIME_ROOT  := $(HOME)/.hermes/skills/ratis
RSYNC_FLAGS   := -av --delete \
                 --exclude '__pycache__' \
                 --exclude '.pytest_cache' \
                 --exclude '*.pyc' \
                 --exclude '.venv'
# Every top-level subdir is a skill. ``shell find`` is more reliable than
# ``$(wildcard */)``, which on some GNU make builds matches files too.
SKILLS        := $(shell find $(HERE) -mindepth 1 -maxdepth 1 -type d -not -name '.*' -exec basename {} \;)

# If SKILL=X is passed, restrict.
ifdef SKILL
SKILLS := $(SKILL)
endif

.PHONY: deploy diff test clean-runtime list help

help:
	@echo "Targets:"
	@echo "  deploy         rsync skill(s) to $(RUNTIME_ROOT)"
	@echo "  diff           dry-run rsync (no writes)"
	@echo "  test           pytest in every skill with tests/"
	@echo "  clean-runtime  rm -rf $(RUNTIME_ROOT)/<skill> (careful)"
	@echo "  list           list known skills"
	@echo ""
	@echo "Use SKILL=<name> to restrict (e.g. make deploy SKILL=claude-code-postmortem)."

list:
	@echo "Skills under $(HERE):"
	@for s in $(SKILLS); do echo "  - $$s"; done

deploy:
	@mkdir -p $(RUNTIME_ROOT)
	@for s in $(SKILLS); do \
	  if [ -d "$(HERE)$$s" ]; then \
	    echo ">> deploy $$s -> $(RUNTIME_ROOT)/$$s/"; \
	    rsync $(RSYNC_FLAGS) "$(HERE)$$s/" "$(RUNTIME_ROOT)/$$s/"; \
	  else \
	    echo "!! skip $$s (no source dir)"; \
	  fi; \
	done

diff:
	@for s in $(SKILLS); do \
	  if [ -d "$(HERE)$$s" ]; then \
	    echo ">> diff $$s"; \
	    rsync --dry-run $(RSYNC_FLAGS) "$(HERE)$$s/" "$(RUNTIME_ROOT)/$$s/"; \
	  fi; \
	done

test:
	@for s in $(SKILLS); do \
	  if [ -d "$(HERE)$$s/tests" ]; then \
	    echo ">> pytest $$s"; \
	    (cd "$(HERE)$$s" && uv run --with pytest pytest tests/ -q); \
	  fi; \
	done

clean-runtime:
	@for s in $(SKILLS); do \
	  if [ -d "$(RUNTIME_ROOT)/$$s" ]; then \
	    echo ">> rm -rf $(RUNTIME_ROOT)/$$s"; \
	    rm -rf "$(RUNTIME_ROOT)/$$s"; \
	  fi; \
	done
