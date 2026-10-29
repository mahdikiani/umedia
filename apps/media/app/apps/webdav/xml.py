"""WebDAV XML: PROPFIND/PROPPATCH/LOCK request parsing and 207 bodies."""

from dataclasses import dataclass
from xml.etree import ElementTree  # noqa: S405
from xml.sax.saxutils import escape, quoteattr

from apps.media_files.content_type import serve_content_type
from apps.media_files.schemas import MediaFileRecord

from .locks import Lock
from .paths import etag, http_date, iso_date

DAV = "DAV:"
Prop = tuple[str, str]  # (namespace, local name)
PROLOG = '<?xml version="1.0" encoding="utf-8"?>\n'

SUPPORTED_LOCK = (
    "<D:lockentry><D:lockscope><D:exclusive/></D:lockscope>"
    "<D:locktype><D:write/></D:locktype></D:lockentry>"
)

LIVE_PROPS: list[str] = [
    "resourcetype",
    "displayname",
    "getcontentlength",
    "getcontenttype",
    "getlastmodified",
    "creationdate",
    "getetag",
    "supportedlock",
    "lockdiscovery",
]


class BadXmlError(ValueError):
    pass


def parse_xml(body: bytes) -> ElementTree.Element | None:
    """Parse a small request body; None for an empty one. DTDs are refused
    outright -- no client needs them and they are the entity-bomb vector."""
    if not body.strip():
        return None
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise BadXmlError("DTDs are not allowed")
    try:
        return ElementTree.fromstring(body)  # noqa: S314 -- DTD refused above
    except ElementTree.ParseError as exc:
        raise BadXmlError(str(exc)) from exc


def _split(tag: str) -> Prop:
    if tag.startswith("{"):
        namespace, _, name = tag[1:].partition("}")
        return namespace, name
    return "", tag


@dataclass(frozen=True)
class PropfindRequest:
    mode: str  # "allprop" | "propname" | "prop"
    props: list[Prop]


def parse_propfind(body: bytes) -> PropfindRequest:
    root = parse_xml(body)
    if root is None:
        return PropfindRequest("allprop", [])
    if _split(root.tag) != (DAV, "propfind"):
        raise BadXmlError("expected D:propfind")
    for child in root:
        namespace, name = _split(child.tag)
        if namespace != DAV:
            continue
        if name == "propname":
            return PropfindRequest("propname", [])
        if name == "prop":
            return PropfindRequest("prop", [_split(p.tag) for p in child])
    return PropfindRequest("allprop", [])


def parse_proppatch(body: bytes) -> list[Prop]:
    root = parse_xml(body)
    if root is None or _split(root.tag) != (DAV, "propertyupdate"):
        raise BadXmlError("expected D:propertyupdate")
    return [
        _split(prop.tag)
        for action in root
        for container in action
        if _split(container.tag) == (DAV, "prop")
        for prop in container
    ]


def parse_lockinfo_owner(body: bytes) -> str | None:
    """The raw `<D:owner>` inner XML of a LOCK body (None if absent), or
    raise when the body is not a write/exclusive lockinfo."""
    root = parse_xml(body)
    if root is None:
        return None
    if _split(root.tag) != (DAV, "lockinfo"):
        raise BadXmlError("expected D:lockinfo")
    for child in root:
        if _split(child.tag) == (DAV, "owner"):
            text = escape(child.text or "")
            inner = "".join(
                ElementTree.tostring(sub, encoding="unicode") for sub in child
            )
            return text + inner
    return ""


def _dav(name: str, value: str) -> str:
    return f"<D:{name}>{value}</D:{name}>" if value else f"<D:{name}/>"


def lock_entries(locks: list[Lock], *, timeout: int) -> str:
    return "".join(
        "<D:activelock><D:locktype><D:write/></D:locktype>"
        "<D:lockscope><D:exclusive/></D:lockscope>"
        f"<D:depth>{'infinity' if lock.depth_infinity else '0'}</D:depth>"
        f"<D:owner>{lock.owner_xml}</D:owner>"
        f"<D:timeout>Second-{timeout}</D:timeout>"
        f"<D:locktoken><D:href>{escape(lock.token)}</D:href></D:locktoken>"
        "</D:activelock>"
        for lock in locks
    )


def lock_response(lock: Lock, *, timeout: int) -> bytes:
    body = (
        PROLOG
        + '<D:prop xmlns:D="DAV:">'
        + _dav("lockdiscovery", lock_entries([lock], timeout=timeout))
        + "</D:prop>"
    )
    return body.encode()


def live_props(
    record: MediaFileRecord | None, locks: list[Lock], *, lock_timeout: int,
) -> dict[str, str]:
    """Prop local name -> inner XML. `record is None` is the library root."""
    if record is None or record.type == "folder":
        props = {
            "resourcetype": "<D:collection/>",
            "displayname": escape(record.name) if record else "",
            "getcontenttype": "httpd/unix-directory",
        }
        if record is not None:
            props["getlastmodified"] = http_date(record.updated_at)
            props["creationdate"] = iso_date(record.created_at)
            props["getetag"] = escape(etag(record))
    else:
        props = {
            "resourcetype": "",
            "displayname": escape(record.name),
            "getcontentlength": str(record.size),
            "getcontenttype": escape(
                serve_content_type(record.name, record.content_type),
            ),
            "getlastmodified": http_date(record.updated_at),
            "creationdate": iso_date(record.created_at),
            "getetag": escape(etag(record)),
        }
    props["supportedlock"] = SUPPORTED_LOCK
    props["lockdiscovery"] = lock_entries(locks, timeout=lock_timeout)
    return props


def _prop_xml(name: str, inner: str) -> str:
    return f"<D:{name}>{inner}</D:{name}>" if inner else f"<D:{name}/>"


def _foreign(prop: Prop) -> str:
    namespace, name = prop
    return f"<x:{name} xmlns:x={quoteattr(namespace)}/>" if namespace else f"<{name}/>"


def response_entry(
    href: str, available: dict[str, str], request: PropfindRequest,
) -> str:
    if request.mode == "propname":
        body = "".join(f"<D:{name}/>" for name in available)
        return _entry(href, [(200, body)])
    if request.mode == "allprop":
        body = "".join(_prop_xml(n, v) for n, v in available.items())
        return _entry(href, [(200, body)])
    found, missing = [], []
    for prop in request.props:
        if prop[0] == DAV and prop[1] in available:
            found.append(_prop_xml(prop[1], available[prop[1]]))
        else:
            missing.append(_foreign(prop) if prop[0] != DAV else f"<D:{prop[1]}/>")
    groups: list[tuple[int, str]] = []
    if found:
        groups.append((200, "".join(found)))
    if missing:
        groups.append((404, "".join(missing)))
    return _entry(href, groups)


_STATUS = {
    200: "HTTP/1.1 200 OK",
    403: "HTTP/1.1 403 Forbidden",
    404: "HTTP/1.1 404 Not Found",
}


def _entry(href: str, groups: list[tuple[int, str]]) -> str:
    propstats = "".join(
        f"<D:propstat><D:prop>{body}</D:prop><D:status>{_STATUS[code]}</D:status>"
        "</D:propstat>"
        for code, body in groups
    )
    return f"<D:response><D:href>{escape(href)}</D:href>{propstats}</D:response>"


def multistatus(entries: list[str]) -> bytes:
    return (
        PROLOG + '<D:multistatus xmlns:D="DAV:">' + "".join(entries)
        + "</D:multistatus>"
    ).encode()


def proppatch_entry(href: str, props: list[Prop]) -> str:
    """Live (DAV:) properties are protected -> 403; dead ones are accepted
    and dropped (UMedia stores no dead properties)."""
    protected = "".join(f"<D:{n}/>" for ns, n in props if ns == DAV)
    accepted = "".join(_foreign(p) for p in props if p[0] != DAV)
    groups: list[tuple[int, str]] = []
    if accepted:
        groups.append((200, accepted))
    if protected:
        groups.append((403, protected))
    return _entry(href, groups)


def error_body(condition: str) -> bytes:
    return (
        PROLOG + f'<D:error xmlns:D="DAV:"><D:{condition}/></D:error>'
    ).encode()
