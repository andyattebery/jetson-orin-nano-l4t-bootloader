#!/usr/bin/env python3
"""Commits versions.env to the repository's default branch through Forgejo's API.

Usage: commit-versions.py
Environment: FORGEJO_URL, REPOSITORY, SHA (the commit the run started from), RUN_NUMBER, and TOKEN,
the job's automatic token. The workflow passes all five.

The workflow runs it when the daily check found a newer release and its build passed, before the
publish (README.md, "A new L4T release"). The job has the repository as an archive, not a clone,
so the commit goes through the API. It replaces versions.env as of SHA: if the branch has changed
the file since, Forgejo answers 409 and nothing is committed. A commit made with the job's token
starts no workflow.

The commit names its own author and committer. Without them, Forgejo uses the token's user, at the
instance's no-reply address, which holds the instance's domain, and the push mirror would carry it
to GitHub.
"""
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

VERSIONS = Path(__file__).resolve().parent.parent / "versions.env"
# The name build.sh gives the rebuilt package's changelog entry.
IDENTITY = {"name": "jetson-orin-nano-l4t-bootloader", "email": "noreply@invalid"}


def die(message):
    sys.exit(f"commit-versions.py: {message}")


def call(method, url, token, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"token {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        die(f"{method} {url}: HTTP {error.code}: {error.read().decode('utf-8', 'replace')}")


def main():
    names = ["FORGEJO_URL", "REPOSITORY", "SHA", "RUN_NUMBER", "TOKEN"]
    unset = [n for n in names if not os.environ.get(n)]
    if unset:
        die(f"not set: {', '.join(unset)}")
    server, repository, sha, run, token = (os.environ[n] for n in names)
    content = VERSIONS.read_bytes()
    version = re.search(rb"^L4T_VERSION=(\S+)$", content, re.M)
    if not version:
        die(f"no L4T_VERSION in {VERSIONS}")
    url = f"{server.rstrip('/')}/api/v1/repos/{repository}/contents/versions.env"
    blob = call("GET", f"{url}?ref={sha}", token)["sha"]
    commit = call("PUT", url, token, {
        "content": base64.b64encode(content).decode(),
        "sha": blob,
        "message": f"Jetson Linux R{version.group(1).decode()}\n\nBuilt by run {run}.\n",
        "author": IDENTITY,
        "committer": IDENTITY,
    })["commit"]["sha"]
    print(f"commit-versions.py: committed {commit}")


if __name__ == "__main__":
    try:
        main()
    except OSError as error:
        die(error)
