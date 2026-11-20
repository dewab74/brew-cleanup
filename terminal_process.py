"""Run a command in a private PTY, relaying input/output to the popup process.

A separate interpreter forks the PTY so no fork happens in the UI's worker thread.
"""

import os
import pty
import select
import signal
import sys


def main():
    # Hold interrupts until forwarding is installed so a Ctrl-C right at start
    # cannot kill this helper and leave the command running unsupervised.
    forwarded = {signal.SIGINT, signal.SIGTERM}
    signal.pthread_sigmask(signal.SIG_BLOCK, forwarded)
    pid, master = pty.fork()
    if pid == 0:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, forwarded)
        os.execvp(sys.argv[1], sys.argv[1:])

    def interrupt(signum, frame):
        try:
            os.killpg(pid, signum)
        except ProcessLookupError:
            pass

    for signum in forwarded:
        signal.signal(signum, interrupt)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, forwarded)
    inputs = [master, sys.stdin.fileno()]
    try:
        while True:
            ready, _, _ = select.select(inputs, [], [])
            if master in ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            if sys.stdin.fileno() in ready:
                data = os.read(sys.stdin.fileno(), 4096)
                if data:
                    os.write(master, data)
                else:
                    inputs.remove(sys.stdin.fileno())
    finally:
        os.close(master)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    sys.exit(main())
