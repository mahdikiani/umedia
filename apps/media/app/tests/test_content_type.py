from apps.media_files.content_type import serve_content_type


def test_plain_text_gets_utf8_charset() -> None:
    assert serve_content_type("hello.txt", "text/plain") == "text/plain; charset=utf-8"


def test_markdown_html_misdetect_is_served_as_plain_utf8() -> None:
    assert (
        serve_content_type("notes.md", "text/html")
        == "text/plain; charset=utf-8"
    )


def test_markdown_octet_stream_is_served_as_plain_utf8() -> None:
    assert (
        serve_content_type("سیدیوسف.md", "application/octet-stream")
        == "text/plain; charset=utf-8"
    )


def test_existing_charset_is_kept() -> None:
    assert (
        serve_content_type("a.txt", "text/plain; charset=iso-8859-1")
        == "text/plain; charset=iso-8859-1"
    )


def test_pdf_is_unchanged() -> None:
    assert serve_content_type("doc.pdf", "application/pdf") == "application/pdf"
