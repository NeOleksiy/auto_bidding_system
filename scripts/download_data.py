"""Download the pinned original Criteo archive; no data is committed to Git."""

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

REVISION = "904188a63cbad78bee43cd26ff5ee4ac77903986"
BASE = "https://huggingface.co/datasets/criteo/criteo-attribution-dataset"
EXPECTED_SHA256 = "94ac7a465564349bc7ba008602211d5990a3c53cc133abc0aadef61ea2391a98"
ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / "data/raw/criteo_attribution_dataset.tsv.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"{BASE}/resolve/{REVISION}/{target.name}"
    if not target.exists():
        partial = target.with_suffix(".part")
        with urlopen(url, timeout=120) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        partial.replace(target)
    with target.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    if sha != EXPECTED_SHA256:
        raise ValueError("Archive checksum mismatch; remove the incomplete archive and retry")
    manifest = {"sha": REVISION, "url": url, "sha256": sha, "bytes": target.stat().st_size}
    (target.parent / "source_api.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
