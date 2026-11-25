"""A small static file server for development, with byte-range support.

    python tools/dev_server.py DIRECTORY [--mount URL_PREFIX=DIR ...] [--port 8090] [--bind 0.0.0.0]

The basemap is one PMTiles file that the browser reads in pieces with HTTP
Range requests; Python's built-in server cannot answer those. In production
Caddy serves the same files (specification section 9.3). Standard library only.

--mount serves another folder under a path, such as data=/srv/fdmr-data/publish,
so rebuilding the UI into DIRECTORY never disturbs the data. Pages and JSON are
sent with no-cache, so a browser always sees the newest cycle.
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import re
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/octet-stream", ".pmtiles")
mimetypes.add_type("application/octet-stream", ".fdf")
mimetypes.add_type("application/x-protobuf", ".pbf")

RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


class RangeHandler(SimpleHTTPRequestHandler):
    mounts: dict[str, str] = {}

    def translate_path(self, path):
        first = path.lstrip("/").split("/", 1)[0].split("?", 1)[0]
        if first in self.mounts:
            rest = path.lstrip("/")[len(first):] or "/"
            base = self.directory
            try:
                self.directory = self.mounts[first]
                return super().translate_path(rest)
            finally:
                self.directory = base
        return super().translate_path(path)

    def send_head(self):
        header = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not header or not os.path.isfile(path):
            return super().send_head()
        m = RANGE.match(header.strip())
        size = os.path.getsize(path)
        if not m or (not m.group(1) and not m.group(2)):
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return None
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        else:  # the last N bytes
            start, end = max(0, size - int(m.group(2))), size - 1
        if start >= size or start > end:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        end = min(end, size - 1)
        f = open(path, "rb")
        f.seek(start)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)
        self._remaining = None

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        p = self.path.split("?", 1)[0]
        if p.endswith((".json", ".html", "/")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--mount", action="append", default=[], metavar="PREFIX=DIR")
    args = ap.parse_args()
    RangeHandler.mounts = dict(m.split("=", 1) for m in args.mount)
    server = ThreadingHTTPServer((args.bind, args.port), partial(RangeHandler, directory=args.directory))
    print(f"serving {args.directory} on http://{args.bind}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
