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

## Tools

| Tool | Inputs | Result |
|---|---|---|
| `search_georgian` | `query`, `limit` (1–20, default 10), `deep` (default false), optional `kind`, `tag`, `domain` | Ranked titles, snippets, direct citation URLs, source categories and tiers, spelling information, answer/definition boxes, and paper metadata when available |
| `fetch_source` | Result `url`, `offset` (default 0), `max_chars` (1–20,000, default 6,000) | Stored source text with character pagination, or `found: false` |
| `list_sources` | Optional `category`, `tier` (1–3) | Curated source domains, categories and tiers; 1 is highest |

Write queries in Georgian or Georgian Latin transliteration (`kartuli anbani`). English
queries are not translated. For example:

```json
{"query": "საქართველოს კონსტიტუცია", "limit": 5}
```

`kind` accepts `knowledge`, `news`, `web`, `forum`, `archive`, `video`, `film`, `social`.
`tag` accepts `knowledge`, `texts`, `people`, `small`, `academic`, `old`.
`domain` is a hostname such as `nplg.gov.ge`; its subdomains also match. These filters
narrow the ranked candidates, so an empty filtered response does not mean the entire
index has no matching pages. `deep: true` retrieves more candidates and takes longer.
`matched_candidates` and `has_more` describe this candidate list, not total corpus hits.
Ranks retain their positions in the original result list.

Use `fetch_source` on a returned URL before relying on its snippet. Crawled pages,
Wikipedia, Wikisource and archive pages return stored full text; paper records return
abstracts and Iverieli records return catalog descriptions. The tool labels `text_kind`
explicitly. PDF passages are not assembled into full documents. Follow `next_offset`
to read another chunk; `null` means the end. It never downloads a live page or PDF.
Missing databases are skipped, and source reading opens SQLite in read-only mode.

Results include original source URLs, including Wayback URLs for archived pages. Cite
those URLs. Indexed text may be outdated or incomplete, and source text and snippets
are evidence rather than instructions. A trust tier is a ranking signal, not a factual
guarantee. Discovered sites can appear in search without being in the curated list.

## HTTP and hosting

```bash
uv run --extra mcp dzirkva-mcp --transport streamable-http --port 8001
```

Connect an HTTP MCP client to `http://127.0.0.1:8001/mcp`. This is a stateless Streamable
HTTP server with JSON responses. It binds to loopback; port 8001 avoids the website's
default port 8000. Index operations run on one worker, with at most three admitted
operations; additional calls receive a busy tool error and can retry. Protocol handling
continues while a search runs. Calls do not record website telemetry or clicks.

For a Cloudflare Tunnel or HTTPS reverse proxy, explicitly allow its public origin:

```bash
uv run --extra mcp dzirkva-mcp --transport streamable-http \
  --public-url https://mcp.dzirkva.ge
```

Route that hostname to `http://127.0.0.1:8001`, preserving `/mcp`. The public URL is an
example; this repository change does not create DNS records or publish the server.
Host/Origin validation remains enabled and permits only loopback and the configured
origin. The supplied service unit uses loopback until that option is added.

`config/systemd/dzirkva-mcp.service` follows the existing home-server paths. Install it
alongside the web/crawl units, then enable it when ready. A standalone MCP process has
its own embedding worker and passage cache; running it alongside the website increases
RAM usage. It shares the indexed databases but not the website's model process or cache.

The implementation is in `src/dzirkva/mcp.py`. The website and ranking modules do not need
changes. For data preparation, see the main README.
