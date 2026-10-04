#!/usr/bin/env python3
"""Checks the debs to publish against NVIDIA's apt index for the release.

Usage: check-nvidia-debs.py INDEX_URL OUT_DIR L4T_VERSION REBUILD_DEB

Passes only if:
- every deb in OUT_DIR except REBUILD_DEB is in NVIDIA's index with the same SHA-256, so apart from
  the rebuild, only NVIDIA's own files get published;
- every package of release L4T_VERSION in the index is in OUT_DIR, except NVIDIA's own
  nvidia-l4t-bootloader, which must not be. The rebuild replaces it, and apt merges identical
  versions from two repos, so a copy of NVIDIA's would take its -1 pin into this repo too.
"""
import hashlib
import os
import re
import sys
import urllib.request


def fail(msg):
    sys.exit(f"check-nvidia-debs.py: {msg}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__.split("\n\n")[1])
    url, out, version, rebuild = sys.argv[1:]

    with urllib.request.urlopen(url, timeout=60) as r:
        index = r.read().decode()
    index_sha, release = {}, set()
    # Kernel packages carry the release inside their version (6.8.12-tegra-39.2.1-...).
    of_release = re.compile(rf"(^|-){re.escape(version)}-")
    for stanza in index.split("\n\n"):
        fields = dict(re.findall(r"^([A-Za-z0-9-]+): (.*)$", stanza, re.M))
        if "Filename" not in fields:
            continue
        name = os.path.basename(fields["Filename"])
        index_sha[name] = fields["SHA256"]
        if of_release.search(fields["Version"]):
            release.add(name)
    if not release:
        fail(f"no package of release {version} in {url}")

    debs = sorted(f for f in os.listdir(out) if f.endswith(".deb"))
    if rebuild not in debs:
        fail(f"{rebuild} is not in {out}")
    nvidia = [f for f in debs if f != rebuild]
    for f in nvidia:
        if f not in index_sha:
            fail(f"{f} is not in NVIDIA's index")
        if sha256(os.path.join(out, f)) != index_sha[f]:
            fail(f"{f} differs from the file in NVIDIA's index")

    stock = [f for f in release if f.startswith("nvidia-l4t-bootloader_")]
    if len(stock) != 1:
        fail(f"expected one nvidia-l4t-bootloader of {version} in the index, found {stock}")
    if stock[0] in debs:
        fail(f"{stock[0]} is NVIDIA's own bootloader; the rebuild replaces it")
    missing = sorted(release - set(debs) - set(stock))
    if missing:
        fail(f"in NVIDIA's index for {version} but not in {out}: {missing}")

    others = sorted(set(nvidia) - release)
    print(f"check-nvidia-debs.py: OK. {len(nvidia)} of NVIDIA's debs match its index by SHA-256: "
          f"{len(release) - 1} of release {version} (all but its bootloader), and {len(others)} others "
          f"({', '.join(others) or 'none'}).")


if __name__ == "__main__":
    main()
