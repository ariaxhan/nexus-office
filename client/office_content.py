"""Authenticated byte serving with precise ranges and isolated HTML previews."""
import mimetypes
import re


def bounds(value, length):
    if not value:
        return 0, max(0, length - 1), 200
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
    if not match or not any(match.groups()) or length == 0:
        raise ValueError('Invalid range')
    first, last = match.groups()
    if not first:
        start, end = max(0, length - int(last)), length - 1
    else:
        start, end = int(first), min(int(last) if last else length - 1, length - 1)
    if start > end or start >= length:
        raise ValueError('Range is outside file')
    return start, end, 206


def serve(handler, path, preview=False):
    size = path.stat().st_size
    try:
        start, end, code = bounds(handler.headers.get('range'), size)
    except ValueError:
        handler._send(416, b'', 'application/octet-stream', {'Content-Range': f'bytes */{size}'})
        return
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    handler.send_response(code)
    handler.send_header('Content-Type', mime)
    handler.send_header('Content-Length', str(end - start + 1 if size else 0))
    handler.send_header('Accept-Ranges', 'bytes')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Cache-Control', 'private, no-cache')
    if code == 206:
        handler.send_header('Content-Range', f'bytes {start}-{end}/{size}')
    if mime in ('text/html','image/svg+xml','application/xhtml+xml'):
        handler.send_header('Content-Security-Policy', "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'")
    elif mime not in ('application/pdf',) and not mime.startswith(('text/', 'audio/', 'image/', 'video/')):
        handler.send_header('Content-Disposition', 'attachment')
    handler.end_headers()
    if handler.command == 'HEAD':
        return
    with path.open('rb') as stream:
        stream.seek(start)
        remaining = end - start + 1 if size else 0
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk); remaining -= len(chunk)
