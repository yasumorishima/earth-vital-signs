"""Exercise build.get() against a local server: 404, dropped connections, a file replaced
mid-download, and a server that ignores Range. Each case must fail closed or produce exact bytes."""
import http.server
import sys
import tempfile
import threading
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import build  # noqa: E402

build.time.sleep = lambda s: None  # no back-off waits in tests

STATE = {"mode": "drop", "v": 0, "served": 0}
A = bytes(range(256)) * 400          # 102,400 bytes
B = bytes(reversed(range(256))) * 400  # same length, different content


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        mode = STATE["mode"]
        if mode == "404":
            self.send_error(404)
            return
        body, lm = (A, "Mon, 01 Jan 2024 00:00:00 GMT")
        if mode == "change" and STATE["served"] >= 2 and STATE["v"] == 0:
            STATE["v"] = 1
        if STATE["v"]:
            body, lm = (B, "Tue, 02 Jan 2024 00:00:00 GMT")
        rng = self.headers.get("Range")
        start = int(rng.split("=")[1].rstrip("-")) if rng and mode != "norange" else 0
        piece = body[start:start + 30000]  # drop the connection every 30,000 bytes
        STATE["served"] += 1
        self.send_response(206 if start else 200)
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        self.send_header("Content-Length", str(len(body) - start))
        self.send_header("Last-Modified", lm)
        self.end_headers()
        self.wfile.write(piece)  # then close: a short read like PSL's


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}/f"
tmp = Path(tempfile.mkdtemp())
ok = True


def case(name, mode, expect):
    global ok
    STATE.update(mode=mode, v=0, served=0)
    f = tmp / name
    t = time.time()
    try:
        build.get(url, f)
        got = f.read_bytes()
        res = "A" if got == A else "B" if got == B else f"MIXED {len(got)}"
    except urllib.error.HTTPError as e:
        res = f"HTTP{e.code}"
    except OSError as e:
        res = "OSError: " + str(e)[:60]
    good = res.startswith(expect)
    ok &= good
    print(("PASS" if good else "FAIL"), name, "->", res, f"({STATE['served']} requests, {time.time()-t:.1f}s)")


case("dropped connections resume to exact bytes", "drop", "A")
case("404 raises at once, no retries", "404", "HTTP404")
case("file replaced mid-download starts over", "change", "B")
case("server ignoring Range fails closed", "norange", "OSError: http")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
