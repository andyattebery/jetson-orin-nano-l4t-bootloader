# jetson-orin-nano-l4t-bootloader

Builds NVIDIA's `nvidia-l4t-bootloader` for Jetson Orin Nano and NX modules on carriers without an
EEPROM, and publishes it to a Debian repo with the rest of the Jetson Linux (L4T) release. A daily
check does this for each new NVIDIA release with the same major version. A node reads that repo
with its own apt, so a new L4T release reaches the module's QSPI through `apt upgrade` and a
reboot.

## Why

NVIDIA's boot firmware reads a carrier-board EEPROM in MB2, and hangs on a carrier without one,
such as the Turing Pi 2. NVIDIA documents the fix, a setting in the MB2 BCT: "EEPROM is an optional
component for a customized carrier board. If the carrier board is designed without an EEPROM, the
following modifications will be needed on the MB2 BCT file". The change is
`cvb_eeprom_read_size = <0x0>` (Jetson Linux r39.2 Developer Guide, Jetson Module Adaptation and
Bring-Up, Jetson Orin NX and Nano Series, "EEPROM Modifications").

NVIDIA's `nvidia-l4t-bootloader` carries capsules built without that setting, and its install
script stages one for QSPI on every install. So this repo rebuilds the package with NVIDIA's own
tools, around a capsule built from the BSP with the setting. It publishes the rebuild with
NVIDIA's other packages for the release, unchanged. The research behind it:
[orin-nano-qspi-updates.md](https://github.com/andyattebery/homelab-infrastructure/blob/main/research/turing-pi-cluster/orin-nano-qspi-updates.md).

## Files

| Path | What |
|---|---|
| `versions.env` | The L4T release, its BSP pin, the rebuild's version suffix, and where it's published. The daily check rewrites the release and its pin. |
| `scripts/build.sh` | Builds the rebuild and collects the release (x86-64 Ubuntu 24.04, as root). |
| `scripts/check-capsule.py` | Compares the rebuilt capsule with NVIDIA's, image by image. |
| `scripts/check-nvidia-debs.py` | Checks the other debs against NVIDIA's apt index. |
| `scripts/check-release.py` | Looks for a newer Jetson Linux release and rewrites `versions.env`. jetson-orin-nano-l4t-minimal's, unchanged (Traps). |
| `scripts/commit-versions.py` | Commits `versions.env` through Forgejo's API, for the workflow. |
| `scripts/publish.sh` | Uploads the release to Forgejo's Debian registry and checks its index. |
| `.forgejo/workflows/build.yml` | Runs `build.sh`, then `publish.sh`: on a push that changes `versions.env`, by hand, and daily when NVIDIA has a newer release. |

## What a build does

`scripts/build.sh`:
1. Downloads NVIDIA's BSP, pinned by `BSP_SHA256`, and runs NVIDIA's host prerequisites.
2. Sets `cvb_eeprom_read_size = <0x0>` in the two MB2 BCT files the p3767 configs use.
3. Builds the BUP (`l4t_generate_soc_bup.sh -e "$BUP_SPEC" t23x`) and the capsule
   (`generate_capsule/l4t_generate_soc_capsule.sh`). The capsule is signed with the same public
   EDK2 test certificates as NVIDIA's.
4. Repacks NVIDIA's package with `tools/Debian/nvdebrepack.sh`, replacing only the capsule.
   - The version becomes NVIDIA's plus `+<REPACK_SUFFIX>`, for example `39.2.1-20260806224157+tp1`.
   - The install script and the dependencies stay NVIDIA's.
5. Collects the release. That's every BSP deb except two kinds, plus the rebuild: 74 debs for
   R39.2.1.
   - NVIDIA's own bootloader is left out, because the rebuild replaces it.
   - So are three dGPU packages NVIDIA's apt pool doesn't carry: `nvidia-l4t-dgpu-apt-source`,
     `-dgpu-config` and `-dgpu-x11`.

`scripts/publish.sh` then uploads them, NVIDIA's first and the rebuild last.

## The checks

The build stops, and nothing is published, unless:
- the edit changed exactly one line in each of the two files, to `<0x0>`;
- the rebuilt capsule matches NVIDIA's (`scripts/check-capsule.py`):
  - the same FW version;
  - the same images;
  - differences only in `mb2`, `VER` (the build stamp) and the QSPI's backup GPTs. Every build gives
    each GPT new random disk and partition GUIDs, as NVIDIA's own capsule shows across its board
    specs. So the GPTs are compared with those GUIDs and their CRCs zeroed, and the rebuild's CRCs
    must check out;
  - `mb2` changed for every board spec;
- the repacked package has NVIDIA's version plus the suffix, NVIDIA's `Depends` and install script,
  and the rebuilt capsule;
- every other deb matches NVIDIA's apt index by SHA-256, and all of the release's packages are
  there except NVIDIA's own bootloader (`scripts/check-nvidia-debs.py`).

After uploading, `publish.sh` checks that the registry's index lists every deb by name, version
and SHA-256.

## A new L4T release

**Within the same major version, it's automatic.** Every day at 04:41 Central time, the workflow
runs [scripts/check-release.py](scripts/check-release.py), the release check from
[jetson-orin-nano-l4t-minimal](https://github.com/andyattebery/jetson-orin-nano-l4t-minimal),
unchanged.
1. **Finding releases:** it reads NVIDIA's
   [Jetson Linux archive](https://developer.nvidia.com/embedded/jetson-linux-archive), where each
   release is a link whose text is its version, and the main Jetson Linux page.
2. **The bump:** for the newest release with `versions.env`'s major version, it downloads the BSP
   once for its SHA-256 and rewrites `L4T_VERSION`, `BSP_URL` and `BSP_SHA256`.
   - `NVIDIA_INDEX_URL` follows `L4T_VERSION`: NVIDIA's apt suite is the release's major.minor.
   - `REPACK_SUFFIX` stays as it is.
3. **The build,** with all of its checks (The checks).
4. **The commit:** once the build has passed, the workflow commits `versions.env` to `main` through
   Forgejo's API, as `jetson-orin-nano-l4t-bootloader <noreply@invalid>`.
5. **The publish.**

A release that fails to build leaves `versions.env` as it was, so the next day's check tries it
again. That includes a release whose packages aren't in NVIDIA's apt index yet:
`check-nvidia-debs.py` stops its build until they are.

Forgejo mails a failed run to the repository's owner, if it has a mailer. To run the check by hand:
the Actions tab, "build", "Run workflow", with "Look for a newer NVIDIA release first" ticked.

**A newer major version is only reported, in the run's log.** It can drop the module or move
NVIDIA's apt index: R36's was under `jetson/t234/`, R39's is under `jetson/som/`.
jetson-orin-nano-l4t-minimal's check opens an issue for it. Moving to it is by hand:
1. Check that the release still builds `CAPSULE` from `BUP_SPEC`, and where its apt index is.
2. In `versions.env`, set `L4T_VERSION`, `BSP_URL` and `BSP_SHA256`, and the path in
   `NVIDIA_INDEX_URL` if NVIDIA moved it. NVIDIA publishes no checksum, so compute it once:
   `curl -fL <url> | sha256sum`.
3. Commit and push to `origin`, which is Forgejo. The workflow builds and publishes, and Forgejo
   push-mirrors the commit to GitHub.

**Either way,** on each node once the workflow has finished: `sudo apt update && sudo apt upgrade`,
then reboot.

Rebuilding a release that's already published needs a higher `REPACK_SUFFIX`, such as `tp2`. A new
release keeps the suffix.

**By hand**, on x86-64 Ubuntu 24.04 with 15 GiB free:
```
mkdir -p work
sudo scripts/build.sh --work-dir work --out out
FORGEJO_URL=https://<forgejo-host> PACKAGES_USER=<user> PACKAGES_TOKEN=<token> scripts/publish.sh out
```

## Using the repo on a node

```
# /etc/apt/sources.list.d/l4t-private.sources
Types: deb
URIs: https://<forgejo-host>/api/packages/homelab/debian
Suites: l4t-bootloader-no-carrier-eeprom
Components: main
Signed-By: /etc/apt/keyrings/l4t-private.asc

# /etc/apt/preferences.d/l4t-private
Package: nvidia-l4t-bootloader
Pin: release o=Nvidia
Pin-Priority: -1

Package: *
Pin: release n=l4t-bootloader-no-carrier-eeprom
Pin-Priority: 990
```

- **The key** is at `https://<forgejo-host>/api/packages/homelab/debian/repository.key`.
- **The pin:** everything the repo carries wins at 990 against NVIDIA's 600. So a new release
  reaches the node only with its rebuild, and NVIDIA's own bootloader never installs (-1).
- **First boot:** the homelab's Jetson sets all three up then, from
  `turingpi/jetson/cloud-init/user-data.tpl` in homelab-infrastructure.

## The runner

The workflow runs on a Forgejo Actions runner with the label `l4t-build`:
`l4t-build:docker://docker.io/library/ubuntu:24.04@sha256:a853f94d226358a79c740cfc7bce0c289748f3fe3488d921d038ccd752c61b60`.
- It's x86-64, because NVIDIA's signing tools are x86 binaries.
- **Registered on this repository only.** Forgejo runs push workflows when a mirror syncs, so a
  runner registered on the user would be offered jobs from every mirrored repository.
- **Secrets:** the repository has the Actions secret `PACKAGES_TOKEN`, a token with
  `write:package`, and the variable `PACKAGES_USER`.
- **Deployment:** homelab-infrastructure's `docker_compose_forgejo_runner` role deploys it.

## Traps

- **Run apt on a node only after the workflow has finished, and never `dist-upgrade` while it
  runs.** The workflow uploads one deb at a time. It runs every day at 04:41 Central time, and a run
  that finds a new release publishes it.
  - Mid-upload, `apt upgrade` keeps back everything tied to the bootloader by exact versions.
  - `apt dist-upgrade`, and `full-upgrade`, remove `nvidia-l4t-bootloader` and `nvidia-l4t-bsp`
    instead, to move the rest. Later upgrades don't reinstall them.
  - homelab-infrastructure's `ansible/tests/apt-sources/verify-l4t-pin.yml` shows both.
- **The registry refuses a version it already has**, with 409, even for identical bytes.
  `publish.sh` treats 409 as already published, and its index check catches a stored file that
  differs. To change a published version, raise `REPACK_SUFFIX`.
- **A failed publish isn't retried.** The commit comes before it, so the next day's check finds
  nothing newer. Run the workflow by hand without the check: it builds and publishes
  `versions.env`'s release. If an upload got 500, the next trap comes first.
- **A 500 on upload can leave a deb stored but missing from the index.** Forgejo rebuilds the index
  after the upload commits. A re-run then gets 409 for that deb, and the index check keeps failing.
  To recover, delete that version and run the workflow again:
  `curl -X DELETE --user <user>:<token> https://<forgejo-host>/api/packages/homelab/debian/pool/l4t-bootloader-no-carrier-eeprom/main/<name>/<version>/arm64`.
- **The label's image is pinned by digest,** and the runner doesn't pull an image it already has.
  To move to a newer `ubuntu:24.04`, change the digest in the runner's labels.
- **Only the capsule for `jetson-orin-nano-devkit-super` is rebuilt.** The package's other capsules
  stay NVIDIA's, so the rebuild isn't for a module whose install script picks one of those.
- **Push to Forgejo.** GitHub is its push mirror, and GitHub doesn't run `.forgejo/workflows/`.
- **The workflow commits to `main`.** A release from the daily check is a commit by
  `jetson-orin-nano-l4t-bootloader`, so pull before pushing.
- **A push that changes `versions.env` without a new release or a higher `REPACK_SUFFIX` fails at
  the publish.** It rebuilds the bootloader, whose bytes differ from the published one (a new build
  stamp and new GPT GUIDs), so the registry answers 409 and the index check fails. The registry is
  unchanged. Don't avoid it with `[skip ci]`: Forgejo then also skips moving the daily check to the
  new commit, and the check's next commit gets 409.
- **`scripts/check-release.py` is a copy** of jetson-orin-nano-l4t-minimal's, byte for byte. A fix
  to one belongs in the other, and nothing checks that they match.
