#!/usr/bin/env python3
"""
SpeakPaste release script.

Usage:
  python release.py              # tag and push current VERSION
  python release.py 1.5.0       # bump VERSION, commit, tag, push

GitHub Actions picks up the tag and publishes SpeakPaste.exe as a release.
Track: https://github.com/mohammad-rj/speakpaste/actions
"""

import re
import subprocess
import sys

SCRIPT = "speakpaste.py"


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FAILED: {cmd}\n{r.stderr.strip()}")
        sys.exit(1)
    return r.stdout.strip()


def read_version():
    src = open(SCRIPT, encoding="utf-8").read()
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', src)
    if not m:
        print("ERROR: VERSION not found in speakpaste.py")
        sys.exit(1)
    return m.group(1)


def bump_version(new_ver):
    src = open(SCRIPT, encoding="utf-8").read()
    src = re.sub(r'(VERSION\s*=\s*")[^"]+(")', rf'\g<1>{new_ver}\g<2>', src)
    open(SCRIPT, "w", encoding="utf-8").write(src)
    print(f"Version bumped to {new_ver}")


def main():
    if len(sys.argv) > 1:
        bump_version(sys.argv[1].lstrip("v"))

    version = read_version()
    tag = f"v{version}"

    # Guard: tag must not already exist
    existing = run("git tag --list").split("\n")
    if tag in existing:
        print(f"Tag {tag} already exists. Bump the version first.")
        sys.exit(1)

    # Guard: no unexpected uncommitted files
    status = run("git status --porcelain")
    dirty = [l for l in status.splitlines()
             if l.strip() and not any(l.endswith(f) for f in ("speakpaste.py", "README.md"))]
    if dirty:
        print("Uncommitted changes found:\n" + "\n".join(dirty))
        print("Commit or stash them first.")
        sys.exit(1)

    # Commit, tag, push
    run("git add speakpaste.py README.md")
    run(f'git commit -m "release: {tag}" --allow-empty')
    run(f"git tag {tag}")
    run("git push")
    run("git push --tags")

    print(f"\nRelease {tag} pushed.")
    print(f"Build:   https://github.com/mohammad-rj/speakpaste/actions")
    print(f"Release: https://github.com/mohammad-rj/speakpaste/releases/tag/{tag}")


if __name__ == "__main__":
    main()
