#!/usr/bin/env python3
"""Commits versions.env to the repository's default branch, with git and the job's token.

Usage: commit-versions.py
Environment: FORGEJO_URL, REPOSITORY, SHA (the commit the run started from), RUN_NUMBER, and TOKEN,
the job's automatic token. The workflow passes all five, and installs git.

The workflow runs it when the daily check found a newer release and its build passed, before the
publish (README.md, "A new L4T release"). The job has the repository as an archive, not a clone,
so this clones the default branch, replaces versions.env there and pushes. If the branch has
changed versions.env since SHA, nothing is committed. If the branch moves before the push, git
refuses the push. A push made with the job's token starts no workflow.

It doesn't use Forgejo's contents API: with the job's token, Forgejo 15 answers its writes with 500
(README.md, "Traps"). The token goes in a Basic Authorization header, as actions/checkout sends it,
through git's environment, so it's in neither the URL nor a command line.

The commit names its own author and committer, so nothing about the instance or the job's container
reaches GitHub through the push mirror.
"""
import base64
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

VERSIONS = Path(__file__).resolve().parent.parent / "versions.env"
# The name build.sh gives the rebuilt package's changelog entry.
NAME, EMAIL = "jetson-orin-nano-l4t-bootloader", "noreply@invalid"


def die(message):
    sys.exit(f"commit-versions.py: {message}")


def git(env, cwd, *args):
    """Runs git in cwd and returns its output. git's own errors go to stderr, into the job's log."""
    result = subprocess.run(["git", *args], cwd=cwd, env=env, stdout=subprocess.PIPE, text=True)
    if result.returncode != 0:
        die(f"git {args[0]} failed with exit status {result.returncode}")
    return result.stdout.strip()


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
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = dict(
        os.environ,
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="http.extraHeader",
        GIT_CONFIG_VALUE_0=f"Authorization: basic {basic}",
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME=NAME,
        GIT_AUTHOR_EMAIL=EMAIL,
        GIT_COMMITTER_NAME=NAME,
        GIT_COMMITTER_EMAIL=EMAIL,
    )
    with tempfile.TemporaryDirectory() as tmp:
        git(env, tmp, "clone", "--quiet", f"{server.rstrip('/')}/{repository}.git", "repo")
        repo = Path(tmp) / "repo"
        branch = git(env, repo, "symbolic-ref", "--short", "HEAD")
        if git(env, repo, "rev-parse", f"{sha}:versions.env") != git(env, repo, "rev-parse", "HEAD:versions.env"):
            die(f"{branch} has changed versions.env since {sha}, so nothing is committed")
        (repo / "versions.env").write_bytes(content)
        git(env, repo, "commit", "--quiet", "--message", f"Jetson Linux R{version.group(1).decode()}",
            "--message", f"Built by run {run}.", "--", "versions.env")
        commit = git(env, repo, "rev-parse", "HEAD")
        git(env, repo, "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}")
    print(f"commit-versions.py: committed {commit} to {branch}")


if __name__ == "__main__":
    try:
        main()
    except OSError as error:
        die(error)
