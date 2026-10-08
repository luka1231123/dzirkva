# Georgian web corpus

`data/bulk.db` is an independent SQLite FTS5 index of the Georgian (`kat_Geor`) part of
[FineWeb-2](https://huggingface.co/datasets/HuggingFaceFW/fineweb-2). The download is three Parquet files,
6,854,013,236 bytes, pinned to revision `af9c13333eb981300149d5ca60a8e9d659b276b9`.
The upstream dataset card reports about 3.7 million Georgian records before the local filters and deduplication.
This is extracted web text with original URLs and capture dates, rather than a ready-made SQLite database.

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

The importer keeps text of at least 300 characters with at least 50% Georgian letters, valid HTTP(S) URLs,
and source provenance. Wikimedia pages already covered by the dedicated indexes are excluded. Equal text
shares one indexed body while alternate source URLs remain readable; exact URL duplicates keep the newest
capture. HTTP/HTTPS and tracking variants with different text remain separate stored records and are grouped
by canonical URL during search. A live crawler match takes precedence over an older corpus match.

No pages are fetched and no embeddings are generated during import. Search uses the finished word index;
it does not open Parquet or load the corpus into memory. Capture dates appear in snippets. These are historical
captures, not a promise that the original page is still available. The ongoing crawler and feeds provide updates.

Finalization optimizes FTS, checks SQLite and FTS integrity, and produces a self-contained database without WAL
sidecars. Copy the finalized file to the server using a temporary filename, then publish it by atomic rename
and restart the web service so cached readers and coverage counts open the new file. Never replace `crawl.db`
or `passages.db` with this corpus. The server needs only SQLite for corpus search, not DuckDB or the downloads.

The database attribution is FineWeb-2, HuggingFaceFW, under
[ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/). The dataset card also refers to
[Common Crawl's terms](https://commoncrawl.org/terms-of-use). Individual webpage rights remain with their
owners. Dataset revision, license, capture identifier, Common Crawl dump and WARC path are retained in the
database and exposed when MCP reads stored corpus text.
