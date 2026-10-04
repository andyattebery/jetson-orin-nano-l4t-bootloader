#!/usr/bin/env bash
# Builds nvidia-l4t-bootloader for carriers without an EEPROM, and collects the L4T release around
# it for scripts/publish.sh (README.md). Runs as root on x86-64 Ubuntu 24.04, as the workflow does
# in its job container: NVIDIA's signing tools are x86 binaries.
#
# The steps, each stamped with the time in the log:
#   1. NVIDIA's BSP, pinned by BSP_SHA256 (versions.env), and NVIDIA's host prerequisites.
#   2. The MB2 BCT's carrier-EEPROM read turned off: cvb_eeprom_read_size = <0x0>, NVIDIA's
#      documented setting for a carrier without an EEPROM.
#   3. NVIDIA's tools build the BUP and the capsule, and repack nvidia-l4t-bootloader around it.
#   4. The checks: check-capsule.py against NVIDIA's capsule, and check-nvidia-debs.py against
#      NVIDIA's apt index. Only then does anything reach --out.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
export DEBIAN_FRONTEND=noninteractive

usage() {
    cat >&2 <<EOF
Usage: sudo $(basename "$0") --work-dir DIR --out DIR

--work-dir DIR  An existing directory with 15 GiB free. The BSP download is kept in DIR, and the
                build runs in DIR/R<L4T_VERSION>/, which must not exist yet.
--out DIR       Created if it's missing, and must hold no .deb. The release's debs, the rebuilt
                bootloader among them, are moved there once the checks pass.
EOF
    exit 2
}

die() {
    echo "build.sh: $*" >&2
    exit 1
}

# Each step is stamped with the time, so a CI log shows how long each one took.
step() {
    echo
    echo "==> $(date -u '+%H:%M:%S UTC') $*"
}

WORK=""
OUT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --work-dir)
            [[ $# -ge 2 ]] || usage
            WORK="$2"
            shift 2
            ;;
        --out)
            [[ $# -ge 2 ]] || usage
            OUT="$2"
            shift 2
            ;;
        -h | --help)
            usage
            ;;
        *)
            echo "build.sh: unknown argument: $1" >&2
            usage
            ;;
    esac
done
[[ -n "$WORK" && -n "$OUT" ]] || usage

[[ "$(id -u)" -eq 0 ]] || die "run it as root (sudo)"
[[ "$(uname -m)" == x86_64 ]] || die "NVIDIA's signing tools are x86 binaries; this host is $(uname -m)"
# Read in a subshell, so that os-release's variables don't mix with versions.env's.
# shellcheck source=/dev/null
os="$(. /etc/os-release && echo "${ID:-} ${VERSION_ID:-}")"
[[ "$os" == "ubuntu 24.04" ]] || die "this build runs on Ubuntu 24.04 only, as in CI; this is $os"

# shellcheck source=../versions.env
. "$REPO/versions.env"
for v in L4T_VERSION BSP_URL BSP_SHA256 NVIDIA_INDEX_URL REPACK_SUFFIX BUP_SPEC CAPSULE; do
    [[ -n "${!v:-}" ]] || die "$v is not set in versions.env"
done
[[ "$BSP_SHA256" =~ ^[0-9a-f]{64}$ ]] || die "BSP_SHA256 in versions.env is not a SHA-256"
[[ "$REPACK_SUFFIX" =~ ^[a-z]+[0-9]+$ ]] ||
    die "REPACK_SUFFIX in versions.env must be letters then a number, like tp1"

[[ -d "$WORK" ]] || die "--work-dir $WORK does not exist"
WORK="$(cd "$WORK" && pwd -P)"
TREE="$WORK/R$L4T_VERSION"
# nvdebrepack.sh splits its -i argument on whitespace.
[[ "$TREE" != *[[:space:]]* ]] || die "the build path $TREE contains whitespace, which nvdebrepack.sh can't take"
[[ ! -e "$TREE" ]] || die "$TREE already exists; remove it and run again"
avail="$(df --output=avail -B1 "$WORK" | tail -n 1 | tr -d ' ')"
((avail >= 15 * 1024 ** 3)) || die "$((avail / 1024 ** 3)) GiB free in $WORK; the build needs 15"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd -P)"
if compgen -G "$OUT/*.deb" > /dev/null; then
    die "$OUT already holds .deb files; use an empty directory"
fi

step "Host packages"
# curl fetches the BSP and bzip2 unpacks it. NVIDIA's prerequisites script calls sudo, python3 runs
# the checks, and nvdebrepack.sh repacks with fakeroot. NVIDIA's script installs the rest.
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl bzip2 sudo python3 fakeroot
# NVIDIA's script installs through sudo, which drops DEBIAN_FRONTEND, so debconf must not ask.
echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections

step "BSP: Jetson Linux R$L4T_VERSION"
# Only a download that matches the pin takes the final name, so an interrupted one is fetched again.
BSP="$WORK/${BSP_URL##*/}"
if [[ ! -f "$BSP" ]]; then
    curl -fL --retry 3 -o "$BSP.partial" "$BSP_URL"
    echo "$BSP_SHA256  $BSP.partial" | sha256sum -c --quiet - || die "$BSP_URL does not match BSP_SHA256"
    mv "$BSP.partial" "$BSP"
fi
echo "$BSP_SHA256  $BSP" | sha256sum -c --quiet - ||
    die "$BSP does not match BSP_SHA256; remove it to download it again"
mkdir "$TREE"
tar -xpf "$BSP" -C "$TREE"
L4T="$TREE/Linux_for_Tegra"
cd "$L4T"

step "NVIDIA's host prerequisites"
./tools/l4t_flash_prerequisites.sh

step "Turn off the carrier-EEPROM read"
# The only two T234 files that set it for the p3767 configs. The diff must show exactly one line
# changed in each, to <0x0>.
mkdir "$TREE/pristine"
set_to_zero='cvb_eeprom_read_size[[:space:]]*=[[:space:]]*<0x0>'
for f in bootloader/tegra234-mb2-bct-common.dtsi bootloader/generic/BCT/tegra234-mb2-bct-misc-p3767-0000.dts; do
    pristine="$TREE/pristine/${f//\//_}"
    cp -p "$f" "$pristine"
    sed -E -i 's/(cvb_eeprom_read_size[[:space:]]*=[[:space:]]*)<0x100>/\1<0x0>/' "$f"
    removed="$(diff "$pristine" "$f" | grep '^<' || true)"
    added="$(diff "$pristine" "$f" | grep '^>' || true)"
    if [[ -z "$removed" || -z "$added" || "$(wc -l <<< "$removed")" -ne 1 ||
        "$(wc -l <<< "$added")" -ne 1 || ! "$added" =~ $set_to_zero ]]; then
        die "$f: expected one cvb_eeprom_read_size line changed to <0x0>, got: $removed / $added"
    fi
    echo "$f"
    echo "  $removed"
    echo "  $added"
done

step "BUP: $BUP_SPEC"
./l4t_generate_soc_bup.sh -e "$BUP_SPEC" t23x
[[ -f bootloader/payloads_t23x/bl_only_payload ]] || die "the BUP step wrote no bootloader/payloads_t23x/bl_only_payload"

step "Capsule: $CAPSULE"
./generate_capsule/l4t_generate_soc_capsule.sh -i "$L4T/bootloader/payloads_t23x/bl_only_payload" \
    -o "$TREE/$CAPSULE" t234

step "Compare with NVIDIA's capsule"
stock_debs=()
for f in bootloader/nvidia-l4t-bootloader_*_arm64.deb; do
    [[ -f "$f" ]] && stock_debs+=("$L4T/$f")
done
[[ ${#stock_debs[@]} -eq 1 ]] ||
    die "expected one bootloader/nvidia-l4t-bootloader_*_arm64.deb, found ${#stock_debs[@]}"
STOCK_DEB="${stock_debs[0]}"
VER="${STOCK_DEB##*/nvidia-l4t-bootloader_}"
VER="${VER%_arm64.deb}"
[[ "$(dpkg-deb -f "$STOCK_DEB" Version)" == "$VER" ]] || die "$STOCK_DEB's version doesn't match its file name"
dpkg-deb --fsys-tarfile "$STOCK_DEB" | tar -xO "./opt/ota_package/t23x/$CAPSULE" > "$TREE/nvidia-$CAPSULE"
python3 "$REPO/scripts/check-capsule.py" "$TREE/nvidia-$CAPSULE" "$TREE/$CAPSULE"

step "Repack nvidia-l4t-bootloader $VER+$REPACK_SUFFIX"
# Only the capsule changes. The install script and the dependencies stay NVIDIA's.
tools/Debian/nvdebrepack.sh -v "$REPACK_SUFFIX" \
    -i "$TREE/$CAPSULE:/opt/ota_package/t23x/$CAPSULE" \
    -m "$CAPSULE rebuilt with cvb_eeprom_read_size = <0x0>, for carriers without an EEPROM." \
    -n "jetson-orin-nano-l4t-bootloader <noreply@invalid>" \
    "$STOCK_DEB"
# nvdebrepack.sh writes beside itself.
REBUILD="$L4T/tools/Debian/nvidia-l4t-bootloader_${VER}+${REPACK_SUFFIX}_arm64.deb"
[[ -f "$REBUILD" ]] || die "nvdebrepack.sh didn't write $REBUILD"
[[ "$(dpkg-deb -f "$REBUILD" Version)" == "$VER+$REPACK_SUFFIX" ]] || die "$REBUILD has the wrong version"
[[ "$(dpkg-deb -f "$REBUILD" Depends)" == "$(dpkg-deb -f "$STOCK_DEB" Depends)" ]] ||
    die "$REBUILD's Depends differs from NVIDIA's"
cmp <(dpkg-deb --ctrl-tarfile "$REBUILD" | tar -xO ./postinst) \
    <(dpkg-deb --ctrl-tarfile "$STOCK_DEB" | tar -xO ./postinst) ||
    die "$REBUILD's install script differs from NVIDIA's"
cmp <(dpkg-deb --fsys-tarfile "$REBUILD" | tar -xO "./opt/ota_package/t23x/$CAPSULE") "$TREE/$CAPSULE" ||
    die "$REBUILD doesn't carry the rebuilt capsule"
dpkg-deb -I "$REBUILD"

step "Collect and check the release"
# Every deb the BSP ships except NVIDIA's own bootloader, which the rebuild replaces, and three dGPU
# packages that NVIDIA's apt pool doesn't carry. They're named exactly, because
# nvidia-l4t-dgpu-tools is in the pool and stays.
RELEASE="$TREE/release"
mkdir "$RELEASE"
for dir in nv_tegra/l4t_deb_packages kernel bootloader tools; do
    found=0
    for deb in "$dir"/*.deb; do
        [[ -f "$deb" ]] || continue
        found=1
        case "${deb##*/}" in
            nvidia-l4t-bootloader_* | nvidia-l4t-dgpu-apt-source_* | nvidia-l4t-dgpu-config_* | nvidia-l4t-dgpu-x11_*)
                continue
                ;;
        esac
        cp "$deb" "$RELEASE/"
    done
    [[ "$found" -eq 1 ]] || die "no .deb in $dir"
done
cp "$REBUILD" "$RELEASE/"
python3 "$REPO/scripts/check-nvidia-debs.py" "$NVIDIA_INDEX_URL" "$RELEASE" "$L4T_VERSION" "${REBUILD##*/}"
mv "$RELEASE"/*.deb "$OUT/"

step "Done: $(find "$OUT" -maxdepth 1 -name '*.deb' | wc -l) debs in $OUT"
