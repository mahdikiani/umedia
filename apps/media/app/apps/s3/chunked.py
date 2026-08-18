"""Decode S3 `aws-chunked` / STREAMING-AWS4-HMAC-SHA256-PAYLOAD bodies.

MinIO's Go client (and AWS SDK v2) PUT objects as signed chunks. The
gateway stores the inner bytes, not the chunk framing.
"""


def unwrap_aws_chunked_body(body: bytes) -> bytes:
    """Return the payload inside aws-chunked framing, or `body` unchanged."""
    if not body or b";chunk-signature=" not in body[:256]:
        first_line = body.split(b"\r\n", 1)[0]
        if not first_line or any(byte not in b"0123456789abcdefABCDEF" for byte in first_line):
            return body
    offset = 0
    parts: list[bytes] = []
    while offset < len(body):
        line_end = body.find(b"\r\n", offset)
        if line_end < 0:
            return body
        header = body[offset:line_end]
        size_token = header.split(b";", 1)[0]
        try:
            size = int(size_token, 16)
        except ValueError:
            return body
        offset = line_end + 2
        if size == 0:
            return b"".join(parts)
        chunk = body[offset:offset + size]
        if len(chunk) != size:
            return body
        parts.append(chunk)
        offset += size
        if body[offset:offset + 2] == b"\r\n":
            offset += 2
    return b"".join(parts) if parts else body
