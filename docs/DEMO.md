# Recording the usage demo

The demo uses the production curses browser with an in-memory simulated Homebrew backend.
It never invokes `brew`, writes real removal history, or saves user preferences.

Requirements: Python 3, asciinema, [agg](https://github.com/asciinema/agg), and ffmpeg.
The checked-in recording uses asciinema 3 and agg 1.9.0.

```sh
make demo
```

which runs `scripts/record_demo.py`, then `agg` (GIF) and `ffmpeg` (MP4). Re-record for every
release: the cast title includes `__version__`, and the release workflow rejects a tag whose
demo was recorded for a different version. Edit the scripted keystrokes in `EVENTS` in
`scripts/record_demo.py` when the UI changes.

The recorder drives the real UI in a 110×34 pseudo-terminal. Only terminal output is recorded;
keyboard-input capture is disabled. The raw cast, animated GIF, and MP4 are kept together.
To explore the safe demo manually, run `python3 scripts/demo.py`.
