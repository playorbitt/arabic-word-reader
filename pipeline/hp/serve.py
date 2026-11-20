"""Static file server with HTTP Range support (needed so the browser can jump inside long audio files)."""
import os, re, sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from .common import OUTPUTS


class Handler(SimpleHTTPRequestHandler):
    def send_head(self):
        rng = self.headers.get('Range'); path = self.translate_path(self.path)
        m = re.match(r'bytes=(\d*)-(\d*)', rng or '')
        if not m or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path); a, b = m.group(1), m.group(2)
        start, end = (max(0, size - int(b)), size - 1) if a == '' else (int(a), int(b) if b else size - 1)
        end = min(end, size - 1)
        if start >= size: self.send_error(416); return None
        f = open(path, 'rb'); f.seek(start)
        self.send_response(206); self.send_header('Content-Type', self.guess_type(path)); self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Range', f'bytes {start}-{end}/{size}'); self.send_header('Content-Length', str(end - start + 1))
        self.end_headers(); self._left = end - start + 1
        return f

    def copyfile(self, source, out):
        left = getattr(self, '_left', None)
        if left is None: return super().copyfile(source, out)
        while left > 0:
            chunk = source.read(min(65536, left))
            if not chunk: break
            try: out.write(chunk)
            except (BrokenPipeError, ConnectionResetError): break
            left -= len(chunk)
        self._left = None

    def end_headers(self):
        if not self.headers.get('Range'): self.send_header('Accept-Ranges', 'bytes')
        super().end_headers()

    def log_message(self, *a): pass


def serve(port=8767):
    os.chdir(OUTPUTS)
    print(f'serving {OUTPUTS} on http://127.0.0.1:{port}/ (Range supported)', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
