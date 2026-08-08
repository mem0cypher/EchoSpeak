"""Safe, read-only public webpage retrieval and bounded structured extraction."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import threading
import time
from html.parser import HTMLParser
from typing import Any, Mapping, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, Field


class SafeWebRetrievalError(RuntimeError):
    def __init__(self, message: str, *, code: str = "safe_fetch_failed") -> None:
        super().__init__(message)
        self.code = code


class SafePageResult(BaseModel):
    schema_version: int = 1
    url: str
    final_url: str
    title: str = ""
    text: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)
    json_ld: list[Any] = Field(default_factory=list)
    semantic_attributes: list[dict[str, str]] = Field(default_factory=list)
    tables: list[list[list[str]]] = Field(default_factory=list)
    content_type: str = ""
    status_code: int = 0
    retrieved_at: float = Field(default_factory=time.time)
    etag: str = ""
    last_modified: str = ""
    cache_control: str = ""
    cache_identity: str = ""
    cache_revalidated: bool = False
    source_sha256: str = ""

    def tool_text(self) -> str:
        payload = {
            "url": self.final_url,
            "title": self.title,
            "retrieved_at": self.retrieved_at,
            "content_type": self.content_type,
            "cache_identity": self.cache_identity,
            "cache_revalidated": self.cache_revalidated,
            "metadata": self.metadata,
            "json_ld": self.json_ld[:12],
            "semantic_attributes": self.semantic_attributes[:80],
            "tables": self.tables[:12],
            "text": self.text,
        }
        return (
            "execution_status=success\nresult_state=data_found\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )


class _PageExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.metadata: dict[str, str] = {}
        self.json_ld: list[Any] = []
        self.semantic: list[dict[str, str]] = []
        self.tables: list[list[list[str]]] = []
        self._ignored = 0
        self._in_title = False
        self._json_ld_depth = 0
        self._json_ld_parts: list[str] = []
        self._table: Optional[list[list[str]]] = None
        self._row: Optional[list[str]] = None
        self._cell_parts: Optional[list[str]] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        lower = tag.casefold()
        if lower in {"script", "style", "noscript", "svg", "canvas"}:
            if lower == "script" and values.get("type", "").casefold() == "application/ld+json":
                self._json_ld_depth += 1
                self._json_ld_parts = []
            else:
                self._ignored += 1
        if lower == "title":
            self._in_title = True
        if lower == "meta":
            name = values.get("name") or values.get("property") or values.get("itemprop")
            content = values.get("content", "").strip()
            if name and content and len(self.metadata) < 80:
                self.metadata[name[:120]] = content[:1000]
        semantic = {
            key: value[:500]
            for key, value in values.items()
            if key in {"itemprop", "itemtype", "itemscope", "typeof", "property", "resource", "content"}
            and value
        }
        if semantic and len(self.semantic) < 200:
            semantic["tag"] = lower
            self.semantic.append(semantic)
        if lower == "table" and self._table is None and len(self.tables) < 20:
            self._table = []
        elif lower == "tr" and self._table is not None:
            self._row = []
        elif lower in {"td", "th"} and self._row is not None:
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        lower = tag.casefold()
        if lower == "title":
            self._in_title = False
        if lower == "script" and self._json_ld_depth:
            self._json_ld_depth -= 1
            raw = "".join(self._json_ld_parts).strip()
            if raw and len(raw) <= 250000 and len(self.json_ld) < 40:
                try:
                    self.json_ld.append(json.loads(raw))
                except (TypeError, ValueError):
                    pass
            self._json_ld_parts = []
        elif lower in {"script", "style", "noscript", "svg", "canvas"} and self._ignored:
            self._ignored -= 1
        if lower in {"td", "th"} and self._cell_parts is not None and self._row is not None:
            cell = re.sub(r"\s+", " ", " ".join(self._cell_parts)).strip()[:1000]
            self._row.append(cell)
            self._cell_parts = None
        elif lower == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row[:30])
            self._row = None
        elif lower == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table[:100])
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._json_ld_depth:
            self._json_ld_parts.append(data)
            return
        if self._ignored:
            return
        clean = re.sub(r"\s+", " ", str(data or "")).strip()
        if not clean:
            return
        if self._in_title:
            self.title_parts.append(clean)
        if self._cell_parts is not None:
            self._cell_parts.append(clean)
        if sum(len(item) for item in self.text_parts) < 100000:
            self.text_parts.append(clean)


class _CacheEntry(BaseModel):
    result: SafePageResult
    expires_at: float


_CACHE: dict[str, _CacheEntry] = {}
_CACHE_LOCK = threading.RLock()


def _normalize_url(raw: str) -> str:
    target = str(raw or "").strip().strip("\"'")
    if target.startswith("www."):
        target = "https://" + target
    parts = urlsplit(target)
    if parts.scheme.casefold() not in {"http", "https"}:
        raise SafeWebRetrievalError("Only HTTP and HTTPS URLs are allowed", code="scheme_not_allowed")
    if not parts.hostname or parts.username or parts.password:
        raise SafeWebRetrievalError("URL must have a public host and no embedded credentials", code="invalid_url")
    default_port = 443 if parts.scheme.casefold() == "https" else 80
    try:
        port = parts.port or default_port
    except ValueError as exc:
        raise SafeWebRetrievalError("URL contains an invalid port", code="invalid_port") from exc
    if port not in {80, 443}:
        raise SafeWebRetrievalError("Only standard HTTP/HTTPS ports are allowed", code="port_not_allowed")
    host = parts.hostname.casefold().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise SafeWebRetrievalError("Local destinations are not allowed", code="destination_not_public")
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if port != default_port:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parts.scheme.casefold(), netloc, parts.path or "/", parts.query, ""))


def _validated_public_addresses(url: str) -> list[str]:
    parts = urlsplit(url)
    host = str(parts.hostname or "")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SafeWebRetrievalError("The destination hostname could not be resolved", code="dns_failed") from exc
    addresses: list[str] = []
    for row in rows:
        address = str(row[4][0]).split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise SafeWebRetrievalError("DNS returned an invalid address", code="dns_invalid") from exc
        if not parsed.is_global:
            raise SafeWebRetrievalError(
                "The destination resolves to a non-public address",
                code="destination_not_public",
            )
        addresses.append(str(parsed))
    if not addresses:
        raise SafeWebRetrievalError("The destination has no public address", code="dns_failed")
    return sorted(set(addresses))


def _max_age(cache_control: str) -> int:
    match = re.search(r"(?i)(?:^|,)\s*max-age\s*=\s*(\d+)", str(cache_control or ""))
    if not match:
        return 0
    return min(int(match.group(1)), 3600)


def _cache_key(url: str, locale: str) -> str:
    return hashlib.sha256(f"GET\n{url}\n{locale.casefold()}\nv1".encode("utf-8")).hexdigest()


def _request_pinned_public_url(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout_seconds: float,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    """Connect only to an already-validated public address.

    The HTTP Host header and HTTPS SNI/certificate name remain the original
    hostname. This prevents a second resolver lookup from turning the DNS
    allow decision into a rebinding window.
    """
    import urllib3

    parts = urlsplit(url)
    hostname = str(parts.hostname or "")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    request_headers = dict(headers)
    host_header = f"[{hostname}]" if ":" in hostname else hostname
    request_headers["Host"] = host_header if port in {80, 443} else f"{host_header}:{port}"
    timeout = urllib3.Timeout(connect=min(5.0, timeout_seconds), read=timeout_seconds)
    last_error: Optional[Exception] = None
    for address in _validated_public_addresses(url):
        pool = None
        response = None
        try:
            if parts.scheme == "https":
                pool = urllib3.HTTPSConnectionPool(
                    address,
                    port=port,
                    timeout=timeout,
                    maxsize=1,
                    block=True,
                    retries=False,
                    cert_reqs="CERT_REQUIRED",
                    assert_hostname=hostname,
                    server_hostname=hostname,
                )
            else:
                pool = urllib3.HTTPConnectionPool(
                    address,
                    port=port,
                    timeout=timeout,
                    maxsize=1,
                    block=True,
                    retries=False,
                )
            response = pool.urlopen(
                "GET",
                path,
                headers=request_headers,
                redirect=False,
                preload_content=False,
                retries=False,
            )
            response_headers = {
                str(key).casefold(): str(value) for key, value in response.headers.items()
            }
            status = int(response.status or 0)
            if 300 <= status < 400 or status == 304:
                return status, response_headers, b""
            declared = int(response_headers.get("content-length") or 0)
            if declared > max_bytes:
                raise SafeWebRetrievalError(
                    "Public page exceeds the response-size limit", code="response_too_large"
                )
            body = response.read(max_bytes + 1, decode_content=True, cache_content=False)
            if len(body) > max_bytes:
                raise SafeWebRetrievalError(
                    "Public page exceeds the response-size limit", code="response_too_large"
                )
            return status, response_headers, body
        except SafeWebRetrievalError:
            raise
        except Exception as exc:
            last_error = exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()
            if pool is not None:
                pool.close()
    raise SafeWebRetrievalError("Public page retrieval failed", code="transport_error") from last_error


def _extract_page(body: bytes, *, content_type: str, max_text_chars: int) -> tuple[str, str, dict[str, str], list[Any], list[dict[str, str]], list[list[list[str]]]]:
    encoding_match = re.search(r"(?i)charset=([a-z0-9._-]+)", content_type)
    encoding = encoding_match.group(1) if encoding_match else "utf-8"
    text = body.decode(encoding, errors="replace")
    if "json" in content_type.casefold():
        try:
            structured = json.loads(text)
            pretty = json.dumps(structured, ensure_ascii=False, indent=2)[:max_text_chars]
            return "", pretty, {}, [structured], [], []
        except (TypeError, ValueError):
            return "", re.sub(r"\s+", " ", text).strip()[:max_text_chars], {}, [], [], []
    parser = _PageExtractor()
    parser.feed(text)
    visible = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()[:max_text_chars]
    title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()[:500]
    return title, visible, parser.metadata, parser.json_ld, parser.semantic, parser.tables


def fetch_public_page(
    url: str,
    *,
    max_bytes: int = 2_000_000,
    max_text_chars: int = 24_000,
    timeout_seconds: float = 12.0,
    max_redirects: int = 3,
    locale: str = "en",
) -> SafePageResult:
    """Retrieve one public page without browser credentials or interaction."""

    target = _normalize_url(url)
    key = _cache_key(target, locale)
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None and cached.expires_at > now:
            return cached.result.model_copy(deep=True)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.8",
        "Accept-Language": locale[:32],
        "User-Agent": "EchoSpeak-SafeResearch/1.0",
    }
    if cached is not None:
        if cached.result.etag:
            headers["If-None-Match"] = cached.result.etag
        if cached.result.last_modified:
            headers["If-Modified-Since"] = cached.result.last_modified
    current = target
    status_code = 0
    response_headers: dict[str, str] = {}
    body = b""
    for redirect_index in range(max_redirects + 1):
        status_code, response_headers, body = _request_pinned_public_url(
            current,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
        if status_code == 304 and cached is not None:
            result = cached.result.model_copy(update={
                "retrieved_at": time.time(),
                "cache_revalidated": True,
            })
            with _CACHE_LOCK:
                _CACHE[key] = _CacheEntry(result=result, expires_at=time.time() + max(30, _max_age(result.cache_control)))
            return result
        if 300 <= status_code < 400:
            if redirect_index >= max_redirects:
                raise SafeWebRetrievalError("Redirect limit exceeded", code="redirect_limit")
            location = str(response_headers.get("location") or "").strip()
            if not location:
                raise SafeWebRetrievalError("Redirect response omitted Location", code="invalid_redirect")
            current = _normalize_url(urljoin(current, location))
            continue
        break
    if status_code < 200 or status_code >= 300:
        raise SafeWebRetrievalError(f"Public page returned HTTP {status_code}", code="http_error")
    content_type = str(response_headers.get("content-type") or "").split(";", 1)[0].strip().casefold()
    allowed_types = {
        "text/html", "application/xhtml+xml", "application/json", "application/ld+json", "text/plain"
    }
    if content_type not in allowed_types:
        raise SafeWebRetrievalError("Unsupported public page content type", code="content_type_not_allowed")
    title, text, metadata, json_ld, semantic, tables = _extract_page(
        body, content_type=content_type, max_text_chars=max_text_chars
    )
    if not text and not json_ld and not tables:
        raise SafeWebRetrievalError("Public page contained no extractable information", code="no_data")
    cache_control = str(response_headers.get("cache-control") or "")
    result = SafePageResult(
        url=target,
        final_url=current,
        title=title,
        text=text,
        metadata=metadata,
        json_ld=json_ld,
        semantic_attributes=semantic,
        tables=tables,
        content_type=content_type,
        status_code=status_code,
        retrieved_at=time.time(),
        etag=str(response_headers.get("etag") or ""),
        last_modified=str(response_headers.get("last-modified") or ""),
        cache_control=cache_control,
        cache_identity=key,
        source_sha256=hashlib.sha256(body).hexdigest(),
    )
    ttl = _max_age(cache_control)
    if "no-store" not in cache_control.casefold():
        with _CACHE_LOCK:
            _CACHE[key] = _CacheEntry(result=result, expires_at=time.time() + ttl)
    return result


__all__ = ["SafePageResult", "SafeWebRetrievalError", "fetch_public_page"]
