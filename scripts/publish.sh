#!/usr/bin/env bash
# Publishes the debs build.sh wrote to Forgejo's Debian registry (README.md): NVIDIA's first, the
# rebuilt bootloader last. Then it checks that the registry's index lists every one of them.
#
# Usage: publish.sh OUT_DIR
# Environment: FORGEJO_URL, PACKAGES_USER and PACKAGES_TOKEN, a token with write:package. The
# workflow passes all three.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

die() {
    echo "publish.sh: $*" >&2
    exit 1
}

[[ $# -eq 1 && -d "$1" ]] || die "usage: publish.sh OUT_DIR"
OUT="$(cd "$1" && pwd -P)"
for v in FORGEJO_URL PACKAGES_USER PACKAGES_TOKEN; do
    [[ -n "${!v:-}" ]] || die "$v is not set"
done
# shellcheck source=../versions.env
. "$REPO/versions.env"
for v in REPACK_SUFFIX REPO_OWNER REPO_DISTRIBUTION REPO_COMPONENT; do
    [[ -n "${!v:-}" ]] || die "$v is not set in versions.env"
done

base="${FORGEJO_URL%/}/api/packages/$REPO_OWNER/debian"
rebuild=""
nvidia=()
for deb in "$OUT"/*.deb; do
    [[ -f "$deb" ]] || continue
    case "${deb##*/}" in
        nvidia-l4t-bootloader_*+"$REPACK_SUFFIX"_arm64.deb)
            [[ -z "$rebuild" ]] || die "two rebuilt bootloaders in $OUT"
            rebuild="$deb"
            ;;
        *) nvidia+=("$deb") ;;
    esac
done
[[ -n "$rebuild" ]] || die "no rebuilt nvidia-l4t-bootloader (+$REPACK_SUFFIX) in $OUT"
[[ ${#nvidia[@]} -gt 0 ]] || die "no NVIDIA debs in $OUT"

resp="$(mktemp)"
upload() {
    local code
    code="$(curl -sS -o "$resp" -w '%{http_code}' --user "$PACKAGES_USER:$PACKAGES_TOKEN" \
        --upload-file "$1" "$base/pool/$REPO_DISTRIBUTION/$REPO_COMPONENT/upload")"
    case "$code" in
        201) echo "published ${1##*/}" ;;
        # Forgejo answers 409 for any version it already has, even with identical bytes. The index
        # check below catches one that differs.
        409) echo "already stored ${1##*/}" ;;
        *)
            echo "publish.sh: ${1##*/}: HTTP $code" >&2
            cat "$resp" >&2
            echo >&2
            exit 1
            ;;
    esac
}
# The rebuilt bootloader goes last. Until it's in the index, apt upgrade keeps back everything tied
# to it by exact versions (README.md, "Traps").
for deb in "${nvidia[@]}"; do
    upload "$deb"
done
upload "$rebuild"

# One check for the whole release: every deb, by name, version and SHA-256, in the index apt reads.
# It also catches a deb that was stored without its index being rebuilt: Forgejo rebuilds the index
# after the upload commits, so a failure there returns 500, and a re-run then gets 409.
index="$(mktemp)"
curl -fsS -o "$index" "$base/dists/$REPO_DISTRIBUTION/$REPO_COMPONENT/binary-arm64/Packages"
python3 - "$index" "$rebuild" "${nvidia[@]}" <<'EOF'
import hashlib
import re
import subprocess
import sys

listed = set()
for stanza in open(sys.argv[1]).read().split("\n\n"):
    fields = dict(re.findall(r"^([A-Za-z0-9-]+): (.*)$", stanza, re.M))
    if "Package" in fields:
        listed.add((fields["Package"], fields["Version"], fields.get("SHA256", "")))

missing = []
for deb in sys.argv[2:]:
    name, version = subprocess.run(["dpkg-deb", "-f", deb, "Package", "Version"], check=True,
                                   capture_output=True, text=True).stdout.split("\n")[:2]
    name, version = name.split(": ", 1)[1], version.split(": ", 1)[1]
    h = hashlib.sha256()
    with open(deb, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if (name, version, h.hexdigest()) not in listed:
        missing.append(f"{name} {version}")
if missing:
    sys.exit("publish.sh: not in the registry's index as built: " + ", ".join(missing) +
             '. See README.md, "Traps".')
print(f"publish.sh: all {len(sys.argv) - 2} debs are in the registry's index, by name, version and SHA-256.")
EOF
