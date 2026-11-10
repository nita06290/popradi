"""ローカル確認用サーバー（Range対応: 音声のシークが効く）。

  python tools/serve.py site 8765
"""

import http.server
import os
import re
import sys
from functools import partial


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().send_head()
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        size = os.path.getsize(path)
        start = int(m.group(1)) if m and m.group(1) else 0
        end = int(m.group(2)) if m and m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_error(416)
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
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
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "site"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    RangeHandler.extensions_map.update({".webmanifest": "application/manifest+json", ".js": "text/javascript", ".vtt": "text/vtt"})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), partial(RangeHandler, directory=root))
    print(f"serving {os.path.abspath(root)} on http://127.0.0.1:{port}", flush=True)
    srv.serve_forever()
