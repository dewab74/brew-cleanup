"""Build a deterministic source release and render its Homebrew formula (stdlib only).

    python scripts/build_release.py                      # dist/: archive, checksums, formula
    python scripts/build_release.py --formula 0.1.0 dist/checksums.txt   # formula to stdout

The release workflow publishes the archive and checksums, then renders the formula from
the published checksums with this script as it exists on main, and pushes it to the tap.
"""

import argparse
import gzip
import hashlib
import io
import re
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "dewab74/brew-cleanup"
MODULES = [
    "brew_cleanup.py",
    "package_browser.py",
    "ui_dialogs.py",
    "themes.py",
    "popup_process.py",
    "terminal_process.py",
]
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def version():
    return re.search(
        r'__version__ = "(' + VERSION.pattern + ')"', (ROOT / "brew_cleanup.py").read_text()
    ).group(1)


def archive_name(release):
    return f"brew-cleanup-{release}.tar.gz"


def formula(release, checksum):
    if not VERSION.fullmatch(release):
        raise SystemExit(f"error: {release!r} is not a version number")
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise SystemExit(f"error: {checksum!r} is not a sha256 checksum")
    return f"""class BrewCleanup < Formula
  include Language::Python::Shebang

  desc "Interactive Homebrew package removal with history and undo"
  homepage "https://github.com/{REPO}"
  url "https://github.com/{REPO}/releases/download/v{release}/{archive_name(release)}"
  sha256 "{checksum}"
  license "MIT"

  livecheck do
    url :stable
    strategy :github_latest
  end

  depends_on "python@3.13"

  def install
    libexec.install Dir["*.py"]
    rewrite_shebang detected_python_shebang, libexec/"brew_cleanup.py"
    bin.install_symlink libexec/"brew_cleanup.py" => "brew-cleanup"
  end

  test do
    assert_match version.to_s, shell_output("#{{bin}}/brew-cleanup --version")
    assert_match "--theme", shell_output("#{{bin}}/brew-cleanup --help")
    ENV["XDG_STATE_HOME"] = testpath.to_s
    assert_match "No history yet", shell_output("#{{bin}}/brew-cleanup history")
  end
end
"""


def checksum_from(path, release):
    name = archive_name(release)
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == name:
            return parts[0]
    raise SystemExit(f"error: no checksum for {name} in {path}")


def build(destination):
    release = version()
    destination.mkdir(parents=True, exist_ok=True)
    prefix = f"brew-cleanup-{release}"
    archive = destination / archive_name(release)
    with (
        archive.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz,
    ):
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for filename in [*MODULES, "brew-cleanup", "README.md", "LICENSE"]:
                data = (ROOT / filename).read_bytes()
                info = tarfile.TarInfo(prefix + "/" + filename)
                info.size = len(data)
                info.mode = 0o755 if filename in ("brew-cleanup", "brew_cleanup.py") else 0o644
                tar.addfile(info, io.BytesIO(data))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination / "checksums.txt").write_text(f"{checksum}  {archive.name}\n")
    (destination / "brew-cleanup.rb").write_text(formula(release, checksum))
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--formula",
        nargs=2,
        metavar=("VERSION", "CHECKSUMS"),
        help="print the formula for a published release instead of building",
    )
    args = parser.parse_args()
    if args.formula:
        release = args.formula[0].removeprefix("v")
        sys.stdout.write(formula(release, checksum_from(args.formula[1], release)))
    else:
        print(build(args.output))
