"""Fetches the model files into an image while it is built, and checks they are exactly the ones
this file names (the model's published revision, and the SHA-256 of each file). Run once, at build
time:

    python fetch_model.py /model

Nothing is downloaded when the service runs."""

import hashlib
import sys
import urllib.request
from pathlib import Path

# sentence-transformers/all-MiniLM-L6-v2 (Apache 2.0), at one fixed commit.
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
FILES = {
    "onnx/model.onnx": (
        "model.onnx",
        "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
    ),
    "tokenizer.json": (
        "tokenizer.json",
        "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
    ),
}
URL = "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/{revision}/{path}"


def main(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for path, (name, expected) in FILES.items():
        with urllib.request.urlopen(URL.format(revision=REVISION, path=path), timeout=120) as reply:
            data = reply.read()
        found = hashlib.sha256(data).hexdigest()
        if found != expected:
            sys.exit(
                f"{path} is not the file this build expects (its SHA-256 is {found}, and it should "
                f"be {expected}), so nothing was kept."
            )
        (target / name).write_bytes(data)
        print(f"{name}: {len(data)} bytes, checked")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
