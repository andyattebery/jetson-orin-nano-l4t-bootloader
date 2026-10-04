#!/usr/bin/env python3
"""Compares a rebuilt bootloader capsule with NVIDIA's, image by image.

Usage: check-capsule.py STOCK.Cap NEW.Cap

Passes only if the two capsules:
- are the same kind of update: FMP image type, FW version and lowest supported version all equal;
- carry the same images, keyed by partition name and board spec;
- differ only in mb2 and VER, the build stamp;
- differ in mb2 for every spec that has one, which shows the MB2 BCT change reached every board.

Layouts:
- EFI capsule: EFI_CAPSULE_HEADER, then an FMP capsule header with one payload item. The item is an
  image header, an authentication block (a monotonic count and a WIN_CERTIFICATE), and the FMP
  payload header ("MSS1"), and then NVIDIA's BUP blob.
- BUP blob, from the BSP's bootloader/BUP_generator.py: a "=16sIIIIII" header (magic, version,
  blob size, header size, entry count, blob type, uncompressed size), then entries of
  "=40sIIII128s" (partition name, offset from the blob's start, length, version, operating mode,
  spec) starting at the header size.
"""
import struct
import sys
import uuid

FMP_CAPSULE_GUID = uuid.UUID("6dcbd5ed-e82d-4c44-bda1-7194199ad92a")
NVIDIA_IMAGE_TYPE = uuid.UUID("bf0d4599-20d4-414e-b2c5-3595b1cda402")
BUP_MAGIC = b"NVIDIA__BLOB__V3"
BUP_HEADER = "=16sIIIIII"
BUP_ENTRY = "=40sIIII128s"
# Images allowed to differ. mb2 carries the MB2 BCT, where the change is; VER is the build stamp.
MAY_DIFFER = {"mb2", "VER"}
MUST_DIFFER = "mb2"


def fail(msg):
    sys.exit(f"check-capsule.py: {msg}")


def parse(path):
    with open(path, "rb") as f:
        d = f.read()
    if uuid.UUID(bytes_le=d[0:16]) != FMP_CAPSULE_GUID:
        fail(f"{path}: not an FMP capsule")
    header_size, _flags, image_size = struct.unpack_from("<III", d, 16)
    if image_size != len(d):
        fail(f"{path}: capsule header says {image_size} bytes, the file has {len(d)}")

    fmp = header_size
    _version, drivers, items = struct.unpack_from("<IHH", d, fmp)
    if drivers != 0 or items != 1:
        fail(f"{path}: expected no embedded drivers and one payload, found {drivers} and {items}")
    (item_offset,) = struct.unpack_from("<Q", d, fmp + 8)

    img = fmp + item_offset
    (image_header_version,) = struct.unpack_from("<I", d, img)
    image_type = uuid.UUID(bytes_le=d[img + 4:img + 20])
    # Version 1 is 32 bytes. Version 2 adds UpdateHardwareInstance, and version 3 ImageCapsuleSupport.
    image_header_len = {1: 32, 2: 40, 3: 48}.get(image_header_version)
    if image_header_len is None:
        fail(f"{path}: unknown FMP image header version {image_header_version}")

    auth = img + image_header_len
    (cert_len,) = struct.unpack_from("<I", d, auth + 8)
    payload = auth + 8 + cert_len
    if d[payload:payload + 4] != b"MSS1":
        fail(f"{path}: no FMP payload header after the authentication block")
    payload_header_size, fw_version, lowest_version = struct.unpack_from("<III", d, payload + 4)

    blob = payload + payload_header_size
    if d[blob:blob + 16] != BUP_MAGIC:
        fail(f"{path}: no {BUP_MAGIC.decode()} blob where the FMP payload header puts it")
    _magic, _bup_version, blob_size, bup_header_size, count, _blob_type, _uncompressed = \
        struct.unpack_from(BUP_HEADER, d, blob)
    if blob + blob_size != len(d):
        fail(f"{path}: the BUP blob doesn't end at the end of the file")

    images = {}
    entry_size = struct.calcsize(BUP_ENTRY)
    for i in range(count):
        name, offset, length, _ver, _mode, spec = struct.unpack_from(
            BUP_ENTRY, d, blob + bup_header_size + i * entry_size)
        key = (name.rstrip(b"\0").decode(), spec.rstrip(b"\0").decode())
        if key in images:
            fail(f"{path}: image {key} appears twice")
        images[key] = d[blob + offset:blob + offset + length]
    return {"type": image_type, "fw_version": fw_version, "lowest_version": lowest_version,
            "images": images}


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__.split("\n\n")[1])
    stock, new = parse(sys.argv[1]), parse(sys.argv[2])

    for c, path in ((stock, sys.argv[1]), (new, sys.argv[2])):
        if c["type"] != NVIDIA_IMAGE_TYPE:
            fail(f"{path}: FMP image type {c['type']}, expected {NVIDIA_IMAGE_TYPE}")
    for field in ("fw_version", "lowest_version"):
        if stock[field] != new[field]:
            fail(f"{field}: NVIDIA's is {stock[field]:#x}, the rebuild's {new[field]:#x}")

    if stock["images"].keys() != new["images"].keys():
        only_stock = sorted(stock["images"].keys() - new["images"].keys())
        only_new = sorted(new["images"].keys() - stock["images"].keys())
        fail(f"different images. Only in NVIDIA's: {only_stock}. Only in the rebuild: {only_new}")

    differ = sorted(k for k in stock["images"] if stock["images"][k] != new["images"][k])
    unexpected = [k for k in differ if k[0] not in MAY_DIFFER]
    if unexpected:
        fail(f"images other than {sorted(MAY_DIFFER)} differ: {unexpected}")
    mb2 = sorted(k for k in stock["images"] if k[0] == MUST_DIFFER)
    if not mb2:
        fail("no mb2 image in the capsule")
    unchanged = [k for k in mb2 if k not in differ]
    if unchanged:
        fail(f"mb2 is identical to NVIDIA's for {unchanged}, so the change didn't reach those boards")

    specs = sorted({k[1] for k in stock["images"]})
    print(f"check-capsule.py: OK. FW version {new['fw_version']:#x}, lowest {new['lowest_version']:#x}.")
    print(f"  {len(new['images'])} images over {len(specs)} specs (common included); differing: "
          f"{len(differ)} ({', '.join(sorted({k[0] for k in differ}))}).")
    print(f"  mb2 changed for all {len(mb2)} specs: {', '.join(k[1] or 'common' for k in mb2)}")


if __name__ == "__main__":
    main()
