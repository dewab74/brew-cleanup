.PHONY: test lint hooks check

test: ## test with fake Homebrew; never changes installed packages
	python3 -m unittest discover -s tests -v

lint: ## format, lint, and scan secrets
	pre-commit run --all-files

hooks: ## install pinned hooks once per clone
	pre-commit install --install-hooks

check: lint test
