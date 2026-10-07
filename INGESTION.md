# Continuous expansion

The crawler remains at 25% of one CPU core and 1.2 GB. One additional ingestion job runs at a time,
with 10% CPU, 300 MB RAM and idle disk priority. Downloads pause before requests while a search is active.
Every downloader and the crawler share host request slots. No ingestion job runs an embedding model.

The systemd timer chooses the next due job after the preceding one finishes. Each job checkpoints its progress.

| Job | Frequency | Maximum per batch |
|---|---|---|
| RSS | 30 minutes | discover 10 hosts, poll 30 feeds, 10 MiB of bodies |
| Passage word index | 10 minutes until complete, then weekly | 20,000 existing passages |
| Personal-site scores | hourly | 2,000 historical pages; new pages score on ingestion |
| Paper metadata | hourly | one due repository, 20 XML pages, 10 MiB total (8 MiB per response); completed repositories refresh weekly |
| Repository discovery | daily | 20 academic candidates, 5 MiB total |
| Paper text | daily | 20 papers, 25 MiB total, 10 MiB per file |
| Archives | daily | 20 URLs, 20 MB total, 2 MB per response, seven seconds between requests |

These are conservative starting budgets. PDF and archive streams can cross their total budget by one final
64 KiB chunk. RSS checks size before retaining each chunk. Feed publication activity adjusts individual polling
intervals; HTTP validators avoid re-downloading unchanged content. Failed sources back off instead of retrying
continuously. Journals, trusted sources, accepted RSS sites and legacy feed hosts enter the registry gradually.

Complete Georgian feed text is searchable through the crawl index. Excerpts receive priority in the existing
slow crawl queue, and the running crawler notices the additions without a restart. Existing richer HTML text is
preserved. Discover continues showing personal sites, rather than being filled by general news feeds.

New paper PDFs retain complete Georgian text for word search in `papers.db`; up to 20 representative chunks
are selected for later embeddings. Existing PDF paragraphs become word-searchable through `passages_fts`,
including paragraphs without vectors. The initial passage backfill is partial until its checkpoint reaches its
target. Subsequent inserts, edits and deletions maintain the word index automatically.

OAI changes and reported deletions replace or retire outdated metadata/text. Repositories without deletion
reporting cannot notify us of vanished records. Temporary PDF failures are retried separately from textless scans.
Legacy zero-passage attempts remain available for explicit inspection with `--retry-legacy`.

Archive collection chooses the latest successful HTML capture reported by CDX, deduplicates captures and text,
and grows same-site links within a per-host budget. It stops an overloaded batch and retries on a later run.
Existing Common Crawl host discoveries are imported in bounded batches; no new bulk Common Crawl download is started.

## Operations

Deploy the committed code and install `config/systemd/dzirkva-ingest.service` and `.timer` under
`/etc/systemd/system/`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dzirkva-ingest.timer
sudo systemctl start dzirkva-ingest.service
journalctl -u dzirkva-ingest --no-pager -n 60
```

Read checkpoints:

```bash
cd ~/dzirkva
~/.local/bin/uv run --frozen python scripts/ingest.py --status
```

Status rows contain job name, next run time, last start time, exit code and run count. Times are Unix timestamps.
The service journal reports new feed items, indexed texts, queued URLs, PDF bytes and extraction outcomes,
archive bytes, word-index progress and scored hosts.

To run a named job manually with the same resource limits, stop the timer briefly, change to `~/dzirkva`, and run:

```bash
systemd-run --user --wait --pipe --working-directory="$PWD" -p CPUQuota=10% -p MemoryMax=300M -p IOSchedulingClass=idle \
  nice -n 19 ~/.local/bin/uv run --frozen python scripts/ingest.py --job paper_text
```

The runner's lock prevents overlap with a scheduled job. Valid names are listed in `scripts/ingest.py`.
PDF extraction requires `poppler-utils` on Ubuntu. Expanded extracted text is limited to 8 MiB per PDF.
The first PDF batch builds a passage URL index under the same resource limits; later replacements use indexed
lookups instead of scanning the entire passage database. This index is built by ingestion, not web startup.

Old-font PDF detection uses `data/pdf_words.db`, built from the existing vocabulary without loading it into a
Python dictionary. Refresh it on the Mac after rebuilding the vocabulary, then copy it to the server:

```bash
uv run python scripts/build_pdf_words.py
rsync -t data/pdf_words.db rexvopc:dzirkva/data/
```

Vectors remain a separate foreground task on the Mac (`scripts/build_passages.py`). Start from a fresh SQLite
backup of the live server database, and merge the resulting vectors by passage ID and matching text; replacing
the live database with an older Mac copy would discard new ingestion. The server can search newly indexed text
before vectors exist.
