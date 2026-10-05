#!/usr/bin/env python3
"""Looks for a Jetson Linux release newer than versions.env's (README.md, "A new L4T release").

The newest release with the same major version is the target. With --update, its BSP is
downloaded once to compute its SHA-256, the pin, and versions.env's L4T_VERSION, BSP_URL and
BSP_SHA256 are rewritten. Newer major versions are only reported: a new major can move to another
Ubuntu release or drop the Orin Nano. When $GITHUB_OUTPUT is set, they go there as report=, the
newest of each, space-separated.

The release list is NVIDIA's Jetson Linux archive page, where each release is a link whose text
is its version, plus the main page's link to the current release's BSP. NVIDIA's apt repository
isn't used: it carries updates that have no BSP. Nor is the BSP URL guessed from the version:
NVIDIA's paths differ between releases (R36.5.2's is under releases/, R39.2.1's under release/).
"""

import argparse
import hashlib
import html
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ARCHIVE_URL = "https://developer.nvidia.com/embedded/jetson-linux-archive"
MAIN_URL = "https://developer.nvidia.com/embedded/jetson-linux"
# NVIDIA's site answers a browser-like User-Agent.
USER_AGENT = "Mozilla/5.0"

LINK = re.compile(r"""<a\b[^>]*\bhref=["']([^"']+)["'][^>]*>(.*?)</a>""", re.S | re.I)
# Link texts on the archive page, such as "39.2.1 >", "39.2 >" or "38.2/38.2.1 >".
VERSIONS_TEXT = re.compile(r"^\d+\.\d+(?:\.\d+)?(?:/\d+\.\d+(?:\.\d+)?)*$")
BSP = re.compile(r"""(https?://[^\s"'<>]*?Jetson_Linux_r(\d+\.\d+\.\d+)_aarch64\.tbz2)""", re.I)


def die(message):
    print(f"check-release.py: {message}", file=sys.stderr)
    sys.exit(1)


def version(text):
    parts = [int(p) for p in text.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def show(v):
    return ".".join(map(str, v))


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", "replace")


def archive_versions(url):
    """{version: release page URL} from the archive page's links."""
    found = {}
    for href, text in LINK.findall(fetch(url)):
        text = re.sub(r"<[^>]+>|\s+", " ", html.unescape(text)).strip().rstrip("> ").strip()
        if VERSIONS_TEXT.match(text):
            for v in text.split("/"):
                found.setdefault(version(v), urllib.parse.urljoin(url, href))
    return found


def bsp_links(url):
    """{version: [BSP URLs]} from one page."""
    found = {}
    for link, v in BSP.findall(fetch(url)):
        found.setdefault(version(v), [])
        if link not in found[version(v)]:
            found[version(v)].append(link)
    return found


def resolve(url):
    """The URL after redirects, and its length."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.url, int(response.headers.get("Content-Length") or 0)


def sha256(url, length):
    digest = hashlib.sha256()
    size = 0
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    if length and size != length:
        die(f"downloaded {size} bytes of {url}, but the server said {length}")
    return digest.hexdigest()


def rewrite(path, values):
    text = path.read_text()
    for key, value in values.items():
        text, count = re.subn(rf"^{key}=.*$", lambda _: f"{key}={value}", text, flags=re.M)
        if count != 1:
            die(f"{path} has {count} {key}= lines, not 1")
    path.write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", type=Path,
                        default=Path(__file__).resolve().parent.parent / "versions.env",
                        help="the versions file to read and, with --update, rewrite")
    parser.add_argument("--current", help="compare against this version, not the file's")
    parser.add_argument("--archive-url", default=ARCHIVE_URL)
    parser.add_argument("--update", action="store_true",
                        help="download the target's BSP, and rewrite the versions file")
    args = parser.parse_args()

    if args.current:
        current = version(args.current)
    else:
        env = dict(re.findall(r"^(\w+)=(.*)$", args.env.read_text(), re.M))
        if "L4T_VERSION" not in env:
            die(f"no L4T_VERSION in {args.env}")
        current = version(env["L4T_VERSION"])

    archive = archive_versions(args.archive_url)
    if not archive:
        die(f"no release versions on {args.archive_url}; has the page changed?")
    main_page = bsp_links(MAIN_URL)
    newer = sorted(v for v in set(archive) | set(main_page) if v > current)

    newest_by_major = {}
    for v in newer:
        if v[0] > current[0]:
            newest_by_major[v[0]] = v
    report = [show(v) for v in newest_by_major.values()]
    for v in report:
        print(f"R{v} is out: a newer major, so it isn't built automatically.")
    if report and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            print(f"report={' '.join(report)}", file=output)

    same_major = [v for v in newer if v[0] == current[0]]
    if not same_major:
        print(f"R{show(current)} is the newest R{current[0]} release.")
        return
    target = max(same_major)
    print(f"R{show(target)} is newer than R{show(current)}.")

    if target in main_page:
        source, links = MAIN_URL, main_page[target]
    else:
        source = archive[target]
        links = bsp_links(source).get(target, [])
    if len(links) != 1:
        die(f"{source} has {len(links)} BSP links for R{show(target)}, not 1: {links}")
    url, length = resolve(links[0])
    if not url.startswith("https://") or not url.lower().endswith(
            f"jetson_linux_r{show(target)}_aarch64.tbz2"):
        die(f"R{show(target)}'s BSP link {links[0]} ends at {url}")
    print(f"BSP: {url} ({length} bytes), from {source}")

    if args.update:
        digest = sha256(url, length)
        print(f"SHA-256: {digest}")
        rewrite(args.env, {"L4T_VERSION": show(target), "BSP_URL": url, "BSP_SHA256": digest})
        print(f"Updated {args.env}")


if __name__ == "__main__":
    try:
        main()
    except OSError as error:
        die(error)
