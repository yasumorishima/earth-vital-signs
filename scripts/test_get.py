"""Exercise build.get() against a local server: 404, dropped connections, a file replaced
mid-download, a server that ignores Range, and 429 Too Many Requests (passing after two refusals,
or never). Each case must fail closed or produce exact bytes. Then check that grid_years keeps the
committed table only when a server keeps refusing during the daily update."""
import http.server
import sys
import tempfile
import threading
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import build  # noqa: E402

SLEPT: list[float] = []
build.time.sleep = SLEPT.append  # no back-off waits in tests; record what get() asked for

STATE = {"mode": "drop", "v": 0, "served": 0, "refused": 0}
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
        refuse = (mode in ("429forever", "429bare") or (mode == "429" and STATE["refused"] < 2)
                  or (mode == "429mid" and self.headers.get("Range") and STATE["refused"] < 1))
        if refuse:
            STATE["refused"] += 1
            self.send_response(429)
            if mode != "429bare":
                self.send_header("Retry-After", "42")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if STATE.get("whole"):  # small-file path: one complete response
            self.send_response(200)
            self.send_header("Content-Length", str(len(A)))
            self.end_headers()
            self.wfile.write(A)
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


def case(name, mode, expect, small=False, slept=None):
    global ok
    STATE.update(mode=mode, v=0, served=0, refused=0, whole=small)
    SLEPT.clear()
    f = tmp / name
    t = time.time()
    try:
        got = build.get(url) if small else (build.get(url, f), f.read_bytes())[1]
        res = "A" if got == A else "B" if got == B else f"MIXED {len(got)}"
    except urllib.error.HTTPError as e:
        res = f"HTTP{e.code}"
    except OSError as e:
        res = "OSError: " + str(e)[:60]
    good = res.startswith(expect) and (slept is None or SLEPT == slept)
    if slept is not None:
        res += f", waits {SLEPT}"
    ok &= good
    print(("PASS" if good else "FAIL"), name, "->", res, f"({STATE['served']} requests, {time.time()-t:.1f}s)")


case("dropped connections resume to exact bytes", "drop", "A")
case("404 raises at once, no retries", "404", "HTTP404")
case("file replaced mid-download starts over", "change", "B")
case("server ignoring Range fails closed", "norange", "OSError: http")
# the small-file path reads one response; serve it whole so only the 429 handling is tested
case("small file: 429 twice then served, honouring Retry-After", "429", "A", small=True,
     slept=[42.0, 42.0])
case("small file: 429 every time raises after 4 tries", "429forever", "HTTP429", small=True,
     slept=[42.0, 42.0, 42.0])
case("large file: 429 twice then resumed to exact bytes", "429", "A", slept=[42.0, 42.0])
case("large file: 429 every time fails closed", "429forever", "OSError: http", slept=[42.0, 42.0, 42.0])
case("large file: 429 on a Range request after progress, then resumed", "429mid", "A", slept=[42.0])
case("small file: 429 without Retry-After waits a minute per attempt", "429bare", "HTTP429", small=True,
     slept=[60.0, 120.0, 180.0])
case("large file: 429 without Retry-After waits a minute per attempt", "429bare", "OSError: http",
     slept=[60.0, 120.0, 180.0])


# grid_years: in the daily update a refusing server keeps the committed table (STALE); a backfill,
# or an error that could mean wrong bytes, still raises.
import email.message  # noqa: E402

import pandas as pd  # noqa: E402

data = Path(tempfile.mkdtemp())
build.DATA = data


def commit_table(last_days_ago):
    end = pd.Timestamp(build.TODAY) - pd.Timedelta(days=last_days_ago)
    pd.DataFrame({"date": pd.date_range(end=end, periods=5), "r": [1.0, 2, 3, 4, 5],
                  "r_anom_1991_2020": 0.0}).to_csv(data / "t.csv", index=False)


commit_table(3)


def http(code):
    return urllib.error.HTTPError(url, code, "x", email.message.Message(), None)


def grid(name, err, expect, real_get=None, **kw):
    """err is raised by grid_year; with real_get, grid_year downloads through the real get() from the
    local server in that mode instead. RAISED must be the very error grid_year raised."""
    global ok
    seen = []

    def boom(*a, **k):
        if real_get:
            STATE.update(mode=real_get, v=0, served=0, refused=0, whole=False)
            try:
                build.get(url, tmp / "grid.nc")
            except Exception as e:  # noqa: BLE001
                seen.append(e)
                raise
        seen.append(err)
        raise err
    build.grid_year = boom
    build.STALE.clear()
    build.FAIL.clear()
    try:
        df = build.grid_years("t", "u{y}", "v", {"r": None}, 0.0, 2020, kw.get("backfill"), kw.get("only"), tmp)
        res = ("KEPT" if list(df.columns) == ["date", "r"] and df.r.tolist() == [1.0, 2, 3, 4, 5]
               and len(build.STALE) == 1 and "bytes" not in build.STALE[0]
               else f"WRONG {list(df.columns)} stale={build.STALE}")
        if build.FAIL:
            res = "KEPT-BUT-GATE-FAILED" if res == "KEPT" else res
    except Exception as e:  # noqa: BLE001
        res = "RAISED" if seen and e is seen[-1] else f"RAISED OTHER {type(e).__name__}: {e}"
    build.FAIL.clear()
    good = res == expect
    ok &= good
    print(("PASS" if good else "FAIL"), "grid_years:", name, "->", res)


grid("429 in the daily update keeps the committed table", http(429), "KEPT")
grid("real get(): 429 every time keeps the committed table", None, "KEPT", real_get="429forever")
grid("real get(): Range ignored still raises", None, "RAISED", real_get="norange")
grid("503 in the daily update keeps the committed table", http(503), "KEPT")
grid("stall (no progress) keeps the committed table", OSError("u: no progress at 5 of 9 bytes"), "KEPT")
grid("server ignoring Range still raises", OSError("u: server ignored Range (status 200)"), "RAISED")
grid("file kept changing still raises", OSError("u: file kept changing during download"), "RAISED")
grid("403 still raises", http(403), "RAISED")
grid("429 during a backfill still raises", http(429), "RAISED", backfill=2025)
grid("429 with --sst-years still raises", http(429), "RAISED", only=[2026])
commit_table(15)
grid("kept table older than 14 days fails the gate", http(429), "KEPT-BUT-GATE-FAILED")
commit_table(14)
grid("kept table exactly 14 days old passes", http(429), "KEPT")
(data / "t.csv").unlink()
grid("429 with no committed table still raises", http(429), "RAISED")

# an unreachable source: the small-file path retries with longer waits, then raises an error that
# build.py reports as transient (exit 75), unlike a 403 or wrong bytes
import socket  # noqa: E402

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    closed = f"http://127.0.0.1:{s.getsockname()[1]}/x"  # nothing listens here once closed
SLEPT.clear()
try:
    build.get(closed)
    res = "returned"
except urllib.error.URLError as e:
    res = "URLError" if not isinstance(e, urllib.error.HTTPError) else "HTTPError"
want = [30.0, 60.0, 120.0, 240.0, 300.0]
good = res == "URLError" and SLEPT == want
ok &= good
print(("PASS" if good else "FAIL"), f"unreachable source raises after {build.NET_TRIES} tries ->", res,
      f"waits {SLEPT}")
for name, e, want in [
    ("unreachable", urllib.error.URLError(OSError(101, "Network is unreachable")), True),
    ("connection reset", ConnectionResetError(), True),
    ("timeout", TimeoutError(), True),
    ("429 after retries", http(429), True),
    ("503 after retries", http(503), True),
    ("stall", OSError("u: no progress at 5 of 9 bytes"), True),
    ("403", http(403), False),
    ("404", http(404), False),
    ("server ignoring Range", OSError("u: server ignored Range (status 200)"), False),
    ("a bug", ValueError("x"), False),
]:
    good = build.transient(e) == want
    ok &= good
    print(("PASS" if good else "FAIL"), f"transient({name}) is {want}")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
