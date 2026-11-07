"""REA public-web snapshotter (separate, egress-capable, unprivileged sidecar).

The REA evidence worker stays on an internal Docker network without egress.
This component fetches only one HTTPS page and same-origin public JavaScript
with pinned, public DNS addresses. It does not run JS, log in, send cookies,
crawl links, invoke APIs, or bypass anti-bot measures.
"""
from __future__ import annotations

from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, quote

TOKEN_FILE = "/run/secrets/rea_lab_token"
MAX_HTML = 1_500_000
MAX_SCRIPT = 750_000
MAX_TOTAL = 12_000_000
MAX_SCRIPTS = 40
MAX_INLINE = 150_000
MAX_REQUEST = 4096
MAX_RESULT = 15_000_000
DNS_TIMEOUT = 6
READ_TIMEOUT = 8
MAX_REDIRECTS = 2
DOMAIN = re.compile(r"(?=.{4,253}\Z)(?:(?!-)[a-z0-9-]{1,63}(?<!-)\.)+[a-z]{2,63}\Z")
USER_AGENT = "REA-WebIntelligence/1.0 (public static source snapshot; no browser)"
SENSITIVE_SUFFIXES = (".local", ".internal", ".localhost", ".test", ".invalid", ".onion")
SCRIPT_TYPES = ("javascript", "ecmascript", "text/plain", "application/octet-stream")


class CaptureError(ValueError):
    pass


def canonical_url(value):
    """Strict HTTPS URL without credentials, IP literals, custom ports or fragments."""
    if not isinstance(value, str) or not 12 <= len(value) <= 2048:
        raise CaptureError("invalid_web_url")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CaptureError("invalid_web_url")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
        port = parts.port
    except (ValueError, UnicodeError):
        raise CaptureError("invalid_web_url") from None
    if (parts.scheme != "https" or parts.username is not None or parts.password is not None
            or port not in (None, 443) or not DOMAIN.fullmatch(host)
            or host.endswith(SENSITIVE_SUFFIXES)):
        raise CaptureError("public_https_domain_required")
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=~-._")
    query = quote(parts.query, safe="/?:@!$&'()*+,;=~-._%")
    return "https://" + host + path + ("?" + query if query else "")


def safe_display_url(url):
    """Never retain query arguments (which can carry access tokens) in evidence."""
    p = urlsplit(url)
    return f"https://{p.hostname}{p.path or '/'}"


def public_addresses(host):
    """Reject ALL non-global DNS answers, not only the first address."""
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise CaptureError("public_dns_resolution_failed") from None
    resolved = []
    for family, stype, proto, canonical, sockaddr in answers:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global:
            raise CaptureError("non_public_dns_address")
        endpoint = (family, sockaddr[0], 443)
        if endpoint not in resolved:
            resolved.append(endpoint)
    if not resolved:
        raise CaptureError("public_dns_resolution_failed")
    return resolved


def fetch(url, expected_host=None, limit=MAX_SCRIPT, redirects=MAX_REDIRECTS):
    """Pinned DNS-to-socket HTTPS fetch with TLS SNI/hostname verification."""
    url = canonical_url(url)
    parts = urlsplit(url)
    if expected_host is not None and parts.hostname != expected_host:
        raise CaptureError("cross_origin_fetch_blocked")
    addresses = public_addresses(parts.hostname)
    raw = None
    tls = None
    for family, ip, port in addresses:
        try:
            raw = socket.socket(family, socket.SOCK_STREAM)
            raw.settimeout(READ_TIMEOUT)
            raw.connect((ip, port))
            tls = ssl.create_default_context().wrap_socket(raw, server_hostname=parts.hostname)
            break
        except (OSError, ssl.SSLError):
            if raw is not None:
                raw.close()
            raw = None
    if tls is None:
        raise CaptureError("tls_connection_failed")
    try:
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        headers = [
            f"GET {target} HTTP/1.1",
            f"Host: {parts.hostname}",
            f"User-Agent: {USER_AGENT}",
            "Accept: text/html,application/javascript,text/javascript,*/*;q=0.5",
            "Accept-Encoding: identity",
            "Connection: close", "", ""
        ]
        tls.sendall("\r\n".join(headers).encode("ascii"))
        response = http.client.HTTPResponse(tls)
        response.begin()
        code = response.status
        kind = response.getheader("Content-Type", "").lower()
        encoding = response.getheader("Content-Encoding", "identity").lower()
        location = response.getheader("Location")
        if code in (301, 302, 303, 307, 308):
            if redirects == 0 or not location:
                raise CaptureError("redirect_limit_reached")
            redirected = canonical_url(urljoin(url, location))
            if urlsplit(redirected).hostname != parts.hostname:
                raise CaptureError("cross_origin_redirect_blocked")
            # Close this socket before following the next validated URL.
            return fetch(redirected, parts.hostname, limit, redirects - 1)
        if code != 200:
            raise CaptureError(f"public_http_status_{code}")
        if encoding not in ("identity", ""):
            raise CaptureError("compressed_response_unsupported")
        length = response.getheader("Content-Length")
        if length and int(length) > limit:
            raise CaptureError("web_resource_too_large")
        data = response.read(limit + 1)
        if len(data) > limit:
            raise CaptureError("web_resource_too_large")
        return data, kind, safe_display_url(url)
    except (OSError, http.client.HTTPException, UnicodeError, ValueError) as exc:
        if isinstance(exc, CaptureError):
            raise
        raise CaptureError("web_resource_unavailable") from None
    finally:
        tls.close()


class PublicScripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.inline = []
        self._in_script = False
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        a = dict(attrs)
        src = a.get("src")
        self._in_script = not bool(src)
        self._buffer = []
        if src and len(self.scripts) < MAX_SCRIPTS * 3:
            self.scripts.append(src)

    def handle_data(self, data):
        if self._in_script and sum(len(x) for x in self._buffer) < MAX_INLINE:
            self._buffer.append(data[:MAX_INLINE])

    def handle_endtag(self, tag):
        if tag == "script" and self._in_script:
            content = "".join(self._buffer)[:MAX_INLINE]
            if content.strip() and len(self.inline) < 12:
                self.inline.append(content)
            self._in_script = False
            self._buffer = []


def snapshot(target):
    url = canonical_url(target)
    host = urlsplit(url).hostname
    page, content_type, final_url = fetch(url, expected_host=host, limit=MAX_HTML)
    if "html" not in content_type:
        raise CaptureError("web_entry_not_html")
    text = page.decode("utf-8", "replace")
    parser = PublicScripts()
    parser.feed(text)
    files = [{"path": "page.html", "content": text, "url": final_url}]
    errors = []
    budget = MAX_TOTAL - len(page)
    seen = set()
    for value in parser.scripts:
        if len(files) - 1 >= MAX_SCRIPTS:
            break
        try:
            asset = canonical_url(urljoin(final_url, value))
            if urlsplit(asset).hostname != host:
                continue
            if asset in seen:
                continue
            seen.add(asset)
            data, kind, resolved = fetch(asset, expected_host=host, limit=min(MAX_SCRIPT, budget))
            if not any(marker in kind for marker in SCRIPT_TYPES):
                errors.append({"asset": safe_display_url(asset), "reason": "non_javascript_content_type"})
                continue
            if budget - len(data) < 0:
                break
            budget -= len(data)
            files.append({"path": f"scripts/asset_{len(files)-1:03}.js",
                          "content": data.decode("utf-8", "replace"), "url": resolved})
        except CaptureError as exc:
            errors.append({"asset": safe_display_url(urljoin(final_url, value))[:240],
                           "reason": str(exc)})
        if budget < 4096:
            break
    for i, inline in enumerate(parser.inline):
        if len(inline.encode("utf-8")) <= budget:
            files.append({"path": f"scripts/inline_{i:02}.js",
                          "content": inline, "url": final_url + "#inline"})
            budget -= len(inline.encode("utf-8"))
    return {"ok": True, "mode": "web", "url": final_url,
            "files": files, "asset_errors": errors[:50],
            "limits": {"max_assets": MAX_SCRIPTS, "max_total_bytes": MAX_TOTAL},
            "limitations": ["Public static HTML/JS only", "No authenticated data or server code",
                            "No JavaScript execution or dynamic browser capture",
                            "External-origin assets excluded", "No model-provider attribution"]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def respond(self, status, obj):
        raw = json.dumps(obj, separators=(",", ":")).encode()
        if len(raw) > MAX_RESULT:
            raw = b'{"ok":false,"error":"snapshot_response_too_large"}'
            status = 413
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self.respond(200, {"ok": True, "service": "web_capture"})
        else:
            self.respond(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        if self.path != "/capture":
            self.respond(404, {"ok": False, "error": "not_found"})
            return
        try:
            with open(TOKEN_FILE, encoding="ascii") as stream:
                expected = stream.read(256).strip()
            if not expected or not hmac.compare_digest(self.headers.get("X-REA-LAB-TOKEN", ""), expected):
                self.respond(403, {"ok": False, "error": "unauthorized"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= MAX_REQUEST:
                raise CaptureError("invalid_request_length")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or set(payload) != {"url"}:
                raise CaptureError("invalid_request")
            self.respond(200, snapshot(payload["url"]))
        except (CaptureError, ValueError, TypeError, OSError, UnicodeError) as exc:
            reason = str(exc) if isinstance(exc, CaptureError) else "capture_failed"
            self.respond(400, {"ok": False, "error": reason})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8767), Handler).serve_forever()
