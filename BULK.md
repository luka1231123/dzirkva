# Georgian web corpus

`data/bulk.db` is an independent SQLite FTS5 index of the Georgian (`kat_Geor`) part of
[FineWeb-2](https://huggingface.co/datasets/HuggingFaceFW/fineweb-2). The download is three Parquet files,
6,854,013,236 bytes, pinned to revision `af9c13333eb981300149d5ca60a8e9d659b276b9`.
The upstream dataset card reports about 3.7 million Georgian records before the local filters and deduplication.
This is extracted web text with original URLs and capture dates, rather than a ready-made SQLite database.

The verified October 2026 build contains **3,505,415 indexed documents** after filtering and deduplication.
The self-contained SQLite file is 39,958,290,432 bytes; its Zstandard level-9 download is 11,340,514,815 bytes.
The raw database SHA-256 is `0fd102c83ad6fd55dec01f97540892801cd5f438180a3bf2281ca5583a3e1cac`.
Its BLAKE3 checksum is `5f82295a201fc9c828095875b8b048b71bfcbe749b75c7b10a05461b0a0ca435`.
It passed SQLite `quick_check` and FTS5's full external-content integrity check on the Mac.

Download and build on the Mac:

```bash
uv run --frozen python scripts/download_fineweb.py
uv run --frozen python scripts/import_fineweb.py \
  data/fineweb-2/test/000_00000.parquet \
  data/fineweb-2/train/000_00000.parquet \
  data/fineweb-2/train/001_00000.parquet --finalize
```

Transfers resume with curl; file lengths and the published SHA-256 hashes are checked. The manifest records
the revision, URLs, sizes and checksums. Import uses two DuckDB threads, a 512 MB DuckDB memory limit and
bounded SQLite transactions. Each file has a durable row checkpoint. Only completed files should be passed
to the importer; a `.sha256` marker identifies a completed verified download.
The importer defaults to a 256 MiB SQLite page cache; `--sqlite-cache-mb` changes it. Search readers use 8 MiB.

The importer keeps text of at least 300 characters with at least 50% Georgian letters, valid HTTP(S) URLs,
and source provenance. Wikimedia pages already covered by the dedicated indexes are excluded. Equal text
shares one indexed body while alternate source URLs remain readable; exact URL duplicates keep the newest
capture. HTTP/HTTPS and tracking variants with different text remain separate stored records and are grouped
by canonical URL during search. A live crawler match takes precedence over an older corpus match.

No pages are fetched and no embeddings are generated during import. Search uses the finished word index;
it does not open Parquet or load the corpus into memory. Capture dates appear in snippets. These are historical
captures, not a promise that the original page is still available. The ongoing crawler and feeds provide updates.

Finalization logs each stage: FTS optimization, compaction, integrity checks, and WAL removal.
After optimization it automatically runs offline `VACUUM` when at least 5% of the database pages are free;
`--compact --finalize` forces compaction. Explicit document IDs stay stable, and the external FTS index is
checked against its documents afterward. Compaction needs temporary disk space for another database copy
and its transaction journal. It produces a self-contained database without WAL sidecars. Copy the finalized file to the server using a temporary filename, then publish it by atomic rename
and restart the web service so cached readers and coverage counts open the new file. Never replace `crawl.db`
or `passages.db` with this corpus. The server needs only SQLite for corpus search, not DuckDB or the downloads.

For a compressed transfer (after finalization):

```bash
zstd -T2 -9 -f data/bulk.db -o data/bulk.db.zst
rsync --whole-file --partial -t data/bulk.db.zst rexvopc:dzirkva/data/bulk-incoming.db.zst
ssh rexvopc 'cd ~/dzirkva && systemd-run --user --wait --pipe --working-directory="$PWD" \
  -p CPUQuota=10% -p MemoryMax=300M -p IOSchedulingClass=idle \
  nice -n 19 flock data/ingest.lock \
  zstd -q -d -f data/bulk-incoming.db.zst -o data/bulk-incoming.db'
```

Verify the unpacked SHA-256 against the Mac file, then rename `data/bulk-incoming.db` to `data/bulk.db` and
restart `dzirkva-web`. Stage under `data/` on the server: its `/tmp` is RAM-backed and unsuitable for this corpus.
`--whole-file` avoids the Mac's openrsync building a large delta-comparison table for a partial compressed file;
an interrupted transfer restarts the straight copy. A direct SSH connection on the same network can avoid the
Cloudflare tunnel, using the existing host key to verify the server.
The unpack job holds the ingestion lock so a scheduled ingestion batch cannot overlap its 10% CPU budget.

To overlap transfer and unpacking on a slow connection, stream the level-3 compressed copy instead
(12,877,574,880 bytes for this build). This uses less decoder CPU than level 9. Install Ubuntu's small `b3sum`
package first; its native BLAKE3 implementation avoids the old server CPU's expensive SHA-256 verification.
Compute the Mac reference with `b3sum --num-threads 2 --no-names data/bulk.db` or Python's `blake3` package.

```bash
zstd -T2 -3 -f data/bulk.db -o data/bulk.db.fast.zst
ssh rexvopc 'cd ~/dzirkva && systemd-run --user --wait --pipe --working-directory="$PWD" \
  -p CPUQuota=10% -p MemoryMax=300M -p IOSchedulingClass=idle \
  nice -n 19 flock data/ingest.lock /bin/bash -o pipefail -c \
  "zstd -q -dc | tee data/bulk-incoming.db | b3sum --num-threads 1 --no-names - > data/bulk-incoming.db.blake3"' \
  < data/bulk.db.fast.zst
```

Publish only after the pipeline exits normally and the written database's BLAKE3 equals the Mac reference.
An interrupted stream leaves an incomplete staging file and must restart from the beginning.

The database attribution is FineWeb-2, HuggingFaceFW, under
[ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/). The dataset card also refers to
[Common Crawl's terms](https://commoncrawl.org/terms-of-use). Individual webpage rights remain with their
owners. Dataset revision, license, capture identifier, Common Crawl dump and WARC path are retained in the
database and exposed when MCP reads stored corpus text.
