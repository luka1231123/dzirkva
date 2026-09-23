"""Barebone search page for testing. Run: uv run python -m dzirkva.web  → http://127.0.0.1:8000"""

import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from dzirkva.search import search

PAGE = """<!doctype html><meta charset="utf-8"><title>dzirkva</title>
<style>body{{font:16px sans-serif;max-width:760px;margin:24px auto;padding:0 16px}}
input{{width:75%;font-size:18px}} .r{{margin:14px 0}} .u{{color:#070;font-size:13px}} .m{{color:#888;font-size:12px}}</style>
<form><input name="q" value="{q}" autofocus> <button>ძებნა</button></form>{body}"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        q = parse_qs(urlparse(self.path).query).get("q", [""])[0].strip()
        body = ""
        if q:
            t = time.time()
            variants, results = search(q)
            body = f"<p class=m>{len(results)} results, {time.time() - t:.1f}s</p><details><summary class=m>variants</summary>"
            body += "".join(f"<div class=m>{escape(n)}: {escape(v)}</div>" for n, v in variants.items()) + "</details>"
            for r in results[:30]:
                tier = f"tier {r.tier} · " if r.tier else ""
                body += (f"<div class=r><a href='{escape(r.url)}'>{escape(r.title)}</a>"
                         f"<div class=u>{escape(r.url[:90])}</div><div>{escape(r.snippet)}</div>"
                         f"<div class=m>{tier}{r.georgian:.0%} Georgian · {len(r.variants)} variants · "
                         f"{escape(', '.join(sorted(r.engines)))} · score {r.score * 1000:.1f}</div></div>")
        html = PAGE.format(q=escape(q), body=body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html)


if __name__ == "__main__":
    print("http://127.0.0.1:8000")
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
