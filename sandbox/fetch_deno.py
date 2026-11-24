"""Download the Deno release zip at build time, check its SHA-256 and unpack the binary.

Usage: python fetch_deno.py <url> <sha256> <folder>

Nothing is downloaded when the sandbox runs: the binary is in the image.
"""

import hashlib
import os
import sys
import urllib.request
import zipfile

MOST_BYTES = 200 * 1024 * 1024


def main(url: str, expected: str, folder: str) -> None:
    if not url.startswith("https://github.com/denoland/deno/releases/download/"):
        raise SystemExit("Only a Deno release from GitHub may be downloaded.")
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 (https only, checked above)
        data = response.read(MOST_BYTES + 1)
    if len(data) > MOST_BYTES:
        raise SystemExit("The Deno download is larger than expected.")
    found = hashlib.sha256(data).hexdigest()
    if found != expected:
        raise SystemExit(
            f"The Deno download does not match its checksum: expected {expected}, got {found}."
        )
    os.makedirs(folder, exist_ok=True)
    archive = os.path.join(folder, "deno.zip")
    with open(archive, "wb") as handle:
        handle.write(data)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.namelist() != ["deno"]:
            raise SystemExit("The Deno download holds more than the one program.")
        bundle.extract("deno", folder)
    os.remove(archive)
    # Execute only. A process started from a file nobody can read is not dumpable, so the scripts
    # that run at the same time cannot read each other's memory or descriptors through /proc.
    os.chmod(os.path.join(folder, "deno"), 0o111)


if __name__ == "__main__":
    main(*sys.argv[1:4])
