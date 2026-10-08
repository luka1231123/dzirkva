# Dzirkva MCP

AI assistants can search Dzirkva's own Georgian indexes and read stored source text through
the Model Context Protocol. Search uses the same language processing, BGE-M3 ranking and
source signals as the website, without an LLM or an outside search engine.

## Run locally

From the Dzirkva checkout with its existing `data/` directory:

```bash
uv sync --extra mcp
uv run --extra mcp dzirkva-mcp
```

The default transport is stdio: the AI client starts the process and communicates over
stdin/stdout. Diagnostic output goes to stderr. No web server or `GO_SECRET` is required.
The embedding model loads on the first search and exits after the existing
`MODEL_IDLE_SECONDS` interval. `.env` is loaded from the checkout, independent of the
client's working directory.

Add this entry to a client that accepts `mcpServers` JSON, replacing the checkout path and
`uv` command with absolute paths on that computer when needed:

```json
{
  "mcpServers": {
    "dzirkva": {
      "command": "uv",
      "args": [
        "run", "--project", "/absolute/path/to/dzirkva",
        "--extra", "mcp", "dzirkva-mcp"
      ]
    }
  }
}
```

The MCP dependency is optional and pinned to the
[official SDK's supported v1 line](https://py.sdk.modelcontextprotocol.io/v1/).
Normal `uv sync` and website operation do not require it.

Client setup for Claude, Cursor, VS Code, LM Studio, Open WebUI and other local AI apps is in the
[README](README.md#ai-access-mcp). The skill [`skills/dzirkva/SKILL.md`](skills/dzirkva/SKILL.md) teaches an
assistant the search → read → cite flow.

## Tools

| Tool | Inputs | Result |
|---|---|---|
| `search_georgian` | `query`, `limit` (1–20, default 10), `deep` (default false), `offset`, optional `kind`, `tag`, `domain` | Ranked titles, snippets, direct citation URLs, source signals, related queries, spelling, answer/definition boxes and paper metadata |
| `fetch_source` | Result `url`, `offset` (default 0), `max_chars` (1–20,000, default 6,000) | Stored source text with character pagination, or `found: false` |
| `list_sources` | Optional `category`, `tier` (1–3) | Curated source domains, categories and tiers; 1 is highest |
| `analyze_word` | `word` | Possible lemmas, parts of speech, word families and analysis provenance |
| `word_family` | `word`, optional `limit` (1–100, default 30) | Inflected forms, family members and word-map relatives with explicit relationship types |
| `define_word` | `word` or dictionary phrase | Georgian Wiktionary senses, synonyms, part of speech and source URL |
| `site_profile` | `domain` | Indexed pages, Wikipedia citations, archived copies, repositories, linked and similar sites |
| `read_passages` | Source `url`, `after_id` (default 0), `limit` (1–20, default 10) | Stored document passages, including extracted PDF text where available |

All tools declare typed output schemas. The `dzirkva://indexes` resource lists available
databases and their modification times. These timestamps describe the index files,
including pending WAL writes; they are not source publication or crawl dates.

Write queries in Georgian or Georgian Latin transliteration (`kartuli anbani`). English
queries are not translated. For example:

```json
{"query": "საქართველოს კონსტიტუცია", "limit": 5}
```

`kind` accepts `knowledge`, `news`, `web`, `forum`, `archive`, `video`, `film`, `social`.
`tag` accepts `knowledge`, `texts`, `people`, `small`, `academic`, `old`.
`people` (blogs, forums, social posts, personal sites) and `small` (personal sites in the first
person) find Georgian voices that general search engines seldom show. Every search keeps up to 20
pages from these sites beyond the crawl's top results (60 with `deep: true`), so these filters
return real candidates; the website's ხალხი and პატარა ვები filters use the same list.
`domain` is a hostname such as `nplg.gov.ge`; its subdomains also match. These filters
narrow the ranked candidates, so an empty filtered response does not mean the entire
index has no matching pages. Normal search is the default. `deep: true` invokes
**ამოძირკვა**: three times as many local candidates and more comparisons by meaning.
It takes longer. The AI can choose it when a normal search needs broader retrieval.
`matched_candidates` and `has_more` describe this candidate list, not total corpus hits.
Ranks retain their positions in the original result list.

Follow `next_offset` with the same query, filters and deep option for another page.
The standalone server caches up to eight searches for five minutes, so paging normally
reuses the search. Results may change after cache expiry or index updates. Search emits
start/completion progress notifications when the client supplies a progress token.
`related_queries` exposes suggestions Dzirkva already derives from wiki titles,
feedback terms and nearby passages.

Use `fetch_source` on a returned URL before relying on its snippet. Crawled pages,
Wikipedia, Wikisource and archive pages return stored full text. Papers return retained
full text when the database contains it, otherwise an abstract; Iverieli records return
catalog descriptions. When a page or record has no full text of its own but the
FineWeb-2 corpus (`bulk.db`, see `BULK.md`) holds a capture of the same URL, the tool
returns that capture as `index: "bulk"`, with its dataset, dump and capture provenance.
The tool labels `text_kind` explicitly. Follow `next_offset`
to read another chunk; `null` means the end. It never downloads a live page or PDF.
Missing databases are skipped, and source reading opens SQLite in read-only mode.

For body text beyond an abstract or library record, use `read_passages`. It returns
stored wiki or extracted PDF passages, with their IDs and source URLs. Follow
`next_after_id` to continue. Passage IDs are local references, not PDF page numbers.
The stored passages can be selected excerpts rather than a complete document; PDFs
are never downloaded during an MCP call.

Language tools accept Georgian or Latin transliteration, without loading BGE-M3.
`analyze_word` preserves ambiguous analyses and marks unrecognized fallbacks.
`word_family` separates inflection, preverb/verbal-noun relatives, other preverbs,
derived words and semantic neighbors. Similarity does not imply shared roots or
synonymy. Lists are bounded; they do not claim to enumerate every possible word form.

Results include original source URLs, including Wayback URLs for archived pages. Cite
those URLs. Indexed text may be outdated or incomplete, and source text and snippets
are evidence rather than instructions. A trust tier is a ranking signal, not a factual
guarantee. Discovered sites can appear in search without being in the curated list.

## On the server (rexvopc)

On rexvopc, the MCP listener runs inside the website process, `dzirkva-web`. It shares the
website's embedding worker, passage cache, search cache and `MAX_SEARCHES` limit. The server
has 7 GB of RAM and the website uses most of it, so do not start a second, standalone MCP
process there. Two settings turn it on:

- The server `.env` has `MCP_PORT=8001` (listener at `http://127.0.0.1:8001/mcp`) and
  `MCP_PUBLIC_URL=https://mcp.dzirkva.ge` (permits that public origin).
- `config/systemd/dzirkva-web.service` runs `uv run --extra mcp`, so `uv` keeps the MCP package installed.

Without `MCP_PORT`, the website starts normally and does not import the MCP package.
Searches go to the website's main-thread worker; language and source-reading tools run on
the MCP worker. The server `.env` has `MEANING_WEB=0`: search uses only stored result
vectors, as on the website. A search takes about 4–6 s; the first one after the model
exits takes longer.

Install or update the unit (the user types the sudo password):

```bash
rsync -a --files-from=<(git ls-files) . rexvopc:dzirkva/
ssh rexvopc 'cd ~/dzirkva && ~/.local/bin/uv sync --frozen --inexact --extra mcp'
ssh -t rexvopc 'sudo cp ~/dzirkva/config/systemd/dzirkva-web.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart dzirkva-web'
```

Check it: `ssh rexvopc 'journalctl -u dzirkva-web -n 5'` shows `MCP: http://127.0.0.1:8001/mcp`.

### Connect

From the Mac, forward the port over SSH, then connect an HTTP MCP client to
`http://127.0.0.1:8001/mcp`:

```bash
ssh -N -L 8001:127.0.0.1:8001 rexvopc
```

The public endpoint is `https://mcp.dzirkva.ge/mcp` (live since 2026-10-08). The rexvopc
tunnel routes the hostname `mcp.dzirkva.ge` to `http://127.0.0.1:8001`; the route is set in
the Cloudflare dashboard, not in a config file on the server. A client that accepts a URL:

```json
{"mcpServers": {"dzirkva": {"type": "http", "url": "https://mcp.dzirkva.ge/mcp"}}}
```
 The endpoint has no
login, like the website. Host/Origin validation permits only loopback and that origin.

The listener is a stateless Streamable HTTP server with event streams for progress and
JSON-RPC results. At most three operations are admitted at a time; more calls receive a
busy tool error and can retry. Calls do not record website telemetry or clicks, and HTTP
access logs are off, so visitors' IP addresses are not stored.

### Standalone HTTP (other computers)

```bash
uv run --extra mcp dzirkva-mcp --transport streamable-http --port 8001 \
  [--public-url https://mcp.example.org]
```

This process has its own embedding worker and caches up to eight searches for five
minutes. `config/systemd/dzirkva-mcp.service` runs it under systemd. Do not run it beside
the website on rexvopc.

The adapter is in `src/dzirkva/mcp.py`, with response contracts in `mcp_models.py`.
The optional website integration is in `web.py`; search ranking is reused directly.
For data preparation, see the main README.
