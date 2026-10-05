from apps.s3.chunked import unwrap_aws_chunked_body


def test_unwrap_aws_chunked_payload() -> None:
    inner = b"from minio cli\n"
    wrapped = (
        f"{len(inner):x};chunk-signature={'ab' * 32}\r\n".encode()
        + inner
        + b"\r\n0;chunk-signature=" + b"cd" * 32 + b"\r\n\r\n"
    )
    assert unwrap_aws_chunked_body(wrapped) == inner


def test_unwrap_leaves_plain_bodies_alone() -> None:
    assert unwrap_aws_chunked_body(b"hello\n") == b"hello\n"
    assert unwrap_aws_chunked_body(b"") == b""
