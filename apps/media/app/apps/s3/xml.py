from datetime import UTC, datetime
from typing import TypedDict
from xml.sax.saxutils import escape


class BucketItem(TypedDict):
    name: str
    creation_date: str


class ObjectItem(TypedDict):
    key: str
    size: int
    content_type: str
    last_modified: datetime
    etag: str


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def error_xml(code: str, message: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<Error><Code>{escape(code)}</Code>"
        f"<Message>{escape(message)}</Message></Error>"
    ).encode()


def list_buckets_xml(buckets: list[BucketItem]) -> bytes:
    contents = "".join(
        "<Bucket>"
        f"<Name>{escape(bucket['name'])}</Name>"
        f"<CreationDate>{escape(bucket['creation_date'])}</CreationDate>"
        "</Bucket>"
        for bucket in buckets
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<ListAllMyBucketsResult>"
        "<Owner><ID>umedia</ID><DisplayName>umedia</DisplayName></Owner>"
        f"<Buckets>{contents}</Buckets>"
        "</ListAllMyBucketsResult>"
    ).encode()


def list_objects_v2_xml(
    *,
    bucket: str,
    prefix: str,
    delimiter: str,
    objects: list[ObjectItem],
    common_prefixes: list[str],
    is_truncated: bool,
    max_keys: int,
    continuation_token: str | None,
    next_continuation_token: str | None,
) -> bytes:
    contents = "".join(
        "<Contents>"
        f"<Key>{escape(item['key'])}</Key>"
        f"<LastModified>{_timestamp(item['last_modified'])}</LastModified>"
        f"<ETag>{escape(item['etag'])}</ETag>"
        f"<Size>{item['size']}</Size>"
        "<StorageClass>STANDARD</StorageClass>"
        "</Contents>"
        for item in objects
    )
    prefixes = "".join(
        f"<CommonPrefixes><Prefix>{escape(value)}</Prefix></CommonPrefixes>"
        for value in common_prefixes
    )
    current_token = (
        f"<ContinuationToken>{escape(continuation_token)}</ContinuationToken>"
        if continuation_token is not None
        else ""
    )
    next_token = (
        "<NextContinuationToken>"
        f"{escape(next_continuation_token)}"
        "</NextContinuationToken>"
        if next_continuation_token is not None
        else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<ListBucketResult>"
        f"<Name>{escape(bucket)}</Name>"
        f"<Prefix>{escape(prefix)}</Prefix>"
        f"<Delimiter>{escape(delimiter)}</Delimiter>"
        f"<MaxKeys>{max_keys}</MaxKeys>"
        f"<KeyCount>{len(objects)}</KeyCount>"
        f"<IsTruncated>{str(is_truncated).lower()}</IsTruncated>"
        f"{current_token}{next_token}{prefixes}{contents}"
        "</ListBucketResult>"
    ).encode()


def list_multipart_uploads_xml(bucket: str) -> bytes:
    """Empty ListMultipartUploads -- we reject multipart, so nothing in-flight."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<ListMultipartUploadsResult "
        'xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Bucket>{escape(bucket)}</Bucket>"
        "<IsTruncated>false</IsTruncated>"
        "</ListMultipartUploadsResult>"
    ).encode()


def ownership_controls_xml() -> bytes:
    """Cyberduck probes GetBucketOwnershipControls before PutObject."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<OwnershipControls "
        'xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<Rule><ObjectOwnership>BucketOwnerEnforced</ObjectOwnership></Rule>"
        "</OwnershipControls>"
    ).encode()


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_delete_objects_body(body: bytes) -> tuple[list[str], bool]:
    """`Delete` XML → (keys, quiet). Invalid XML raises ValueError."""
    if not body.strip():
        raise ValueError("Delete body is empty")
    from xml.etree import ElementTree

    try:
        root = ElementTree.fromstring(body)  # noqa: S314 -- S3 client XML, not untrusted files
    except ElementTree.ParseError as error:
        raise ValueError("Delete body is not valid XML") from error
    if _xml_local_name(root.tag) != "Delete":
        raise ValueError("Delete body must be a Delete document")
    quiet = False
    keys: list[str] = []
    for child in root:
        name = _xml_local_name(child.tag)
        if name == "Quiet":
            quiet = (child.text or "").strip().lower() == "true"
            continue
        if name != "Object":
            continue
        for field in child:
            if _xml_local_name(field.tag) == "Key" and field.text:
                keys.append(field.text)
    return keys, quiet


def delete_result_xml(
    *,
    deleted: list[str],
    errors: list[tuple[str, str, str]],
    quiet: bool,
) -> bytes:
    """DeleteObjects response. `errors` is (key, code, message)."""
    deleted_xml = "" if quiet else "".join(
        f"<Deleted><Key>{escape(key)}</Key></Deleted>" for key in deleted
    )
    errors_xml = "".join(
        "<Error>"
        f"<Key>{escape(key)}</Key>"
        f"<Code>{escape(code)}</Code>"
        f"<Message>{escape(message)}</Message>"
        "</Error>"
        for key, code, message in errors
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<DeleteResult "
        'xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"{deleted_xml}{errors_xml}"
        "</DeleteResult>"
    ).encode()
