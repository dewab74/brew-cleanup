"""Background command execution without taking over the browser terminal."""

import codecs
import os
import queue
import select
import shlex
import signal
import subprocess
import sys
import threading
from pathlib import Path


class CommandRunner:
    def __init__(self, emit):
        self.emit = emit
        self.input = queue.Queue()
        self.cancelled = threading.Event()

    def failure(self, code, args):
        error = subprocess.CalledProcessError(code, ["brew", *args])
        error.interrupted = self.cancelled.is_set()  # Journaled as "interrupted".
        return error

    def __call__(self, *args):
        if self.cancelled.is_set():
            raise self.failure(130, args)
        self.emit("\n$ " + shlex.join(["brew", *args]) + "\n")
        env = dict(
            os.environ,
            HOMEBREW_NO_AUTO_UPDATE="1",
            HOMEBREW_NO_INSTALL_CLEANUP="1",
            HOMEBREW_NO_AUTOREMOVE="1",
            TERM="dumb",
            NO_COLOR="1",
        )
        helper = str(Path(__file__).with_name("terminal_process.py"))
        with subprocess.Popen(
            [sys.executable, helper, "brew", *args],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ) as process:
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            interrupted = False
            while True:
                if self.cancelled.is_set() and not interrupted:
                    process.send_signal(signal.SIGINT)
                    interrupted = True
                while not self.input.empty():
                    try:
                        process.stdin.write(self.input.get_nowait().encode())
                        process.stdin.flush()
                    except (BrokenPipeError, queue.Empty):
                        break
                ready, _, _ = select.select([process.stdout], [], [], 0.1)
                if ready:
                    data = os.read(process.stdout.fileno(), 65536)
                    if not data:
                        break
                    self.emit(decoder.decode(data))
            self.emit(decoder.decode(b"", final=True))
            code = process.wait()
            if code:
                raise self.failure(code, args)
