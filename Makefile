.PHONY: test lint hooks check demo

test: ## test with fake Homebrew; never changes installed packages
	python3 -m unittest discover -s tests -v

lint: ## format, lint, and scan secrets
	pre-commit run --all-files

hooks: ## install pinned hooks once per clone
	pre-commit install --install-hooks

check: lint test

demo: ## re-record docs/demo.{cast,gif,mp4} with simulated packages (needs asciinema, agg, ffmpeg)
	python3 scripts/record_demo.py
	agg --font-size 14 --fps-cap 10 --idle-time-limit 2 --last-frame-duration 1 docs/demo.cast docs/demo.gif
	ffmpeg -loglevel error -y -i docs/demo.gif -movflags +faststart -pix_fmt yuv420p \
		-vf 'pad=ceil(iw/2)*2:ceil(ih/2)*2' docs/demo.mp4
