// Run on a google.com result page (hl=ka, gl=ge) while `scripts/versus.py listen` runs: sends the query and the
// first-page results (title, shown site line, snippet) → eval/student_google.jsonl. Wait a second before the next page.
// Google's page rules block fetch and images to 127.0.0.1, so the record goes as a page address; the listener
// answers 204 and the page stays. A loop inside the page is slow (a hidden tab's timers run once a minute):
// load each query page, then run this.
(() => {
  const q = new URLSearchParams(location.search).get('q'), seen = new Set(), top = [];
  for (const h of document.querySelectorAll('a h3')) {
    const a = h.closest('a');
    let b = a;  // the result box: the first parent with more text than the title
    for (let i = 0; i < 6 && b.parentElement; i++) { b = b.parentElement; if (b.innerText.length > h.innerText.length + 120) break; }
    const lines = b.innerText.split('\n').map(s => s.trim()).filter(Boolean);
    const cite = (a.querySelector('cite') || b.querySelector('cite'))?.innerText || '';
    const u = /^https?:/.test(cite) ? cite.split(' · ')[0] : (lines[1] || '');  // videos: "Facebook · page name"
    if (seen.has(h.innerText + u)) continue;
    seen.add(h.innerText + u);
    top.push({ t: h.innerText, u, s: lines[lines.length - 1].slice(0, 200) });
  }
  const rec = { q, captcha: location.pathname.startsWith('/sorry'), top: top.slice(0, 10) };
  location.href = 'http://127.0.0.1:8765/?d=' + encodeURIComponent(JSON.stringify(rec));
  return `${top.length} results${rec.captcha ? ', CAPTCHA' : ''}`;
})()
