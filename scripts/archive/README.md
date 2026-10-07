# Archived scripts

Finished or paused one-off jobs. Their data stays in use: `data/iverieli.db`, `data/papers.db`, and passages with
site `iverieli` or `papers` in `data/passages.db`. Run from the project root, for example
`uv run python scripts/archive/papers_collect.py`; each one is resumable.

| Script | Job | State |
|---|---|---|
| `find_repos.py` | find OAI-PMH endpoints of Georgian journals and repositories | done |
| `papers_collect.py` | paper metadata → `data/papers.db` | done |
| `papers_text.py` | paper PDFs → passages | stopped, resumable |
| `iverieli_collect.py` | National Library catalog → `data/iverieli.db` | done |
| `iverieli_text.py` | Iverieli PDFs → passages | paused at 1,820 of 90,804 items |
| `watchdog.sh` | restart `scripts/build_passages.py` when it stalls (Mac `stat`) | paused at 11,028 of 17,124 papers |
