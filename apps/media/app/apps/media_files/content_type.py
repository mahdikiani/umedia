"""Content-Type for streamed library bytes.

Browsers treat `text/*` without `charset` as ISO-8859-1 / Windows-1252.
UTF-8 markdown (Persian, etc.) then looks like garbage. libmagic also
often calls a `.md` file `text/html` when it contains `<`, and the
browser renders it as a broken page.
"""

from apps.media_files.schemas import DEFAULT_CONTENT_TYPE
_MARKDOWN_SUFFIXES = (".md", ".markdown")
_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_UTF8_PREFIXES = (
    "text/",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/xhtml+xml",
)


def serve_content_type(name: str, stored: str | None) -> str:
    """MIME for Content-Type when streaming `name`.

    `.md` that was indexed as HTML/octet-stream is served as `text/plain`
    so the browser shows the source. Textual types get `charset=utf-8`.
    """
    raw = (stored or DEFAULT_CONTENT_TYPE).strip() or DEFAULT_CONTENT_TYPE
    mime, _, params = raw.partition(";")
    mime = mime.strip().lower() or DEFAULT_CONTENT_TYPE
    rest = params.strip()
    lowered = name.lower()
    if lowered.endswith(_MARKDOWN_SUFFIXES) and (
        mime in _HTML_TYPES or mime == DEFAULT_CONTENT_TYPE
    ):
        mime = "text/plain"
        rest = ""
    if _needs_utf8(mime) and "charset=" not in rest.lower():
        return f"{mime}; charset=utf-8" if not rest else f"{mime}; {rest}; charset=utf-8"
    if rest:
        return f"{mime}; {rest}"
    return mime


def _needs_utf8(mime: str) -> bool:
    return mime.startswith(_UTF8_PREFIXES) or mime in {
        "text/markdown",
        "text/x-markdown",
        "application/json",
    }
