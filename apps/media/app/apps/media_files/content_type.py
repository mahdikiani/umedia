"""Content-Type for streamed library bytes.

Browsers treat `text/*` without `charset` as ISO-8859-1 / Windows-1252.
UTF-8 markdown (Persian, etc.) then looks like garbage. libmagic also
often calls a `.md` file `text/html` when it contains `<`, and the
browser renders it as a broken page.
"""

import mimetypes

from apps.media_files.schemas import DEFAULT_CONTENT_TYPE

GENERIC_TYPES = {
    DEFAULT_CONTENT_TYPE,
    "binary/octet-stream",
    "application/x-unknown",
}


def index_content_type(name: str, *candidates: str | None) -> str:
    """MIME to store on a StorageObject: skip generic blob types and
    fall back to the filename when the plugin/browser only knew
    `application/octet-stream`.
    """
    for candidate in candidates:
        mime = (candidate or "").split(";", 1)[0].strip().lower()
        if mime and mime not in GENERIC_TYPES:
            return mime
    guessed = mimetypes.guess_type(name)[0]
    if guessed:
        return guessed
    return DEFAULT_CONTENT_TYPE


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

    `.md` files are always served as `text/plain; charset=utf-8` so the
    browser opens them inline instead of downloading.
    """
    raw = (stored or DEFAULT_CONTENT_TYPE).strip() or DEFAULT_CONTENT_TYPE
    mime, _, params = raw.partition(";")
    mime = mime.strip().lower() or DEFAULT_CONTENT_TYPE
    rest = params.strip()
    lowered = name.lower()
    if lowered.endswith(_MARKDOWN_SUFFIXES):
        # Browsers display `text/plain` inline; `text/markdown` and
        # mis-detected HTML/octet-stream usually trigger a download.
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
