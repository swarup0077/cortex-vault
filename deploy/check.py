"""Health check for a running Cortex Vault (deploy/azure.md §8): one search, sent the way the page sends it.

    python deploy/check.py <url> "<query>" "<file name expected first>"

The search box doesn't call a Gradio API with inputs: cortex.js triggers the box's submit event with the query as
event data ({q, scope, select}) through Gradio's queue, and the `search` endpoint takes no inputs (since the M6
re-skin). This does the same, reads the result cards, and prints OK (exit 0) when the first one is the expected
file, else FAIL (exit 1). Needs only httpx, which Gradio installs.
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import uuid

import httpx


def search(url: str, query: str, *, scope: str = "Both", timeout: float = 60) -> tuple[list[str], float]:
    """The file names of the result cards, in order, and the seconds from sending the search to its result."""
    base = url.rstrip("/")
    with httpx.Client(timeout=timeout) as http:
        dep = next(d for d in http.get(f"{base}/config").json()["dependencies"] if d.get("api_name") == "search")
        session = uuid.uuid4().hex[:11]
        t0 = time.perf_counter()
        http.post(f"{base}/gradio_api/queue/join", json={
            "data": [None], "event_data": {"q": query, "scope": scope, "select": ""}, "fn_index": dep["id"],
            "trigger_id": dep["targets"][0][0], "session_hash": session}).raise_for_status()
        out = None
        with http.stream("GET", f"{base}/gradio_api/queue/data", params={"session_hash": session}) as stream:
            for line in stream.iter_lines():
                msg = json.loads(line[5:]) if line.startswith("data:") else {}
                if msg.get("msg") == "process_completed":
                    if not msg.get("success"):
                        raise RuntimeError(f"the server reported an error: {msg.get('output')}")
                    out = msg["output"]["data"][0]
                    break
        if out is None:
            raise RuntimeError("the queue closed without a result")
    page = out.get("value", "") if isinstance(out, dict) else str(out)
    return [html.unescape(n) for n in re.findall(r'class="cv-card__name">([^<]*)<', page)], time.perf_counter() - t0


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.split("\n\n")[1].strip())
        return 2
    url, query, expected = argv
    names, secs = search(url, query)
    ok = bool(names) and names[0] == expected
    print(f'{"OK" if ok else "FAIL"} "{query}": #1 {names[0] if names else "nothing"} ({len(names)} results, '
          f"{secs:.2f} s)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
