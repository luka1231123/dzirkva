"""Download only FineWeb-2's Georgian shards, with pinned provenance and resumable transfers.

Run on the Mac, then import with scripts/import_fineweb.py. No page crawling or embedding.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess

import httpx

DATASET = "HuggingFaceFW/fineweb-2"
REVISION = "af9c13333eb981300149d5ca60a8e9d659b276b9"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=REVISION)
    parser.add_argument("--directory", type=Path, default=Path("data/fineweb-2"))
    args = parser.parse_args()
    if not args.revision.isalnum():
        parser.error("revision must be a commit hash or a simple ref")
    args.directory.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60) as client:
        info = client.get(f"https://huggingface.co/api/datasets/{DATASET}/revision/{args.revision}")
        info.raise_for_status()
        revision = info.json()["sha"]
        response = client.get(f"https://huggingface.co/api/datasets/{DATASET}/tree/{revision}/data/kat_Geor",
                              params={"recursive": "true", "expand": "false"})
        response.raise_for_status()
    files = [{"path": item["path"], "bytes": item["size"], "sha256": item.get("lfs", {}).get("oid"),
              "url": f"https://huggingface.co/datasets/{DATASET}/resolve/{revision}/{item['path']}"}
             for item in response.json() if item["type"] == "file" and item["path"].endswith(".parquet")]
    if not files:
        raise RuntimeError("no Georgian Parquet shards found")
    manifest = {"dataset": DATASET, "revision": revision, "license": "ODC-BY-1.0",
                "subset": "kat_Geor", "files": files}
    (args.directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{len(files)} Georgian files, {sum(f['bytes'] for f in files):,} bytes, revision {revision}", flush=True)

    def download(item):
        source = Path(item["path"])
        path = args.directory / source.parent.name / source.name
        path.parent.mkdir(exist_ok=True)
        print(f"download {path}: {item['bytes']:,} bytes", flush=True)
        if not path.exists() or path.stat().st_size != item["bytes"]:
            subprocess.run(["curl", "--fail", "--location", "--retry", "5", "--retry-all-errors",
                            "--connect-timeout", "20", "--continue-at", "-", "--silent", "--show-error",
                            "--output", str(path), item["url"]], check=True)
        if path.stat().st_size != item["bytes"]:
            raise RuntimeError(f"size mismatch: {path}")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if item["sha256"] and digest != item["sha256"]:
            raise RuntimeError(f"checksum mismatch: {path}")
        path.with_suffix(path.suffix + ".sha256").write_text(digest + "\n")
        print(f"verified {path}: {digest}", flush=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(download, files))


if __name__ == "__main__":
    main()
