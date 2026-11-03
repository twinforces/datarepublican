"""Resume policy for large archive GETs (USASpending Full zips)."""

from download_utils import (
    _meta_from_dump,
    _parse_curl_headers,
    implausible_remote_length,
    should_resume_partial,
)


def test_partial_without_sidecar_resumes():
    # First GET died at curl 18. No .meta.json yet. Keep the 382MB.
    assert should_resume_partial(382 * 1024 * 1024, 2091892107, None, False) is True


def test_complete_same_size_does_not_count_as_partial():
    assert should_resume_partial(100, 100, None, False) is False


def test_sidecar_other_object_does_not_resume():
    saved = {"etag": "old"}
    assert should_resume_partial(50, 200, saved, False) is False


def test_sidecar_same_object_resumes():
    saved = {"etag": "abc"}
    assert should_resume_partial(50, 200, saved, True) is True


def test_socrata_no_length_no_sidecar_resumes():
    assert should_resume_partial(12345, None, None, False) is True


def test_implausible_tiny_remote_vs_gb_zip():
    assert implausible_remote_length(2_091_892_107, 2131) is True
    assert implausible_remote_length(2_091_892_107, 2_091_892_107) is False
    assert implausible_remote_length(500_000, 2131) is False


def test_parse_headers_follows_redirect_last_status():
    blob = (
        "HTTP/1.1 302 Found\r\n"
        "Location: https://cdn.example/file.zip\r\n"
        "\r\n"
        "HTTP/1.1 200 OK\r\n"
        "Content-Length: 99\r\n"
        "ETag: \"abc\"\r\n"
    )
    status, headers = _parse_curl_headers(blob)
    assert status == 200
    assert headers["content-length"] == "99"
    assert headers["etag"] == '"abc"'


def test_meta_from_content_range_when_length_missing():
    blob = (
        "HTTP/1.1 206 Partial Content\r\n"
        "Content-Range: bytes 0-0/2039742911\r\n"
        "Last-Modified: Wed, 14 Oct 2026 19:51:56 GMT\r\n"
    )
    meta = _meta_from_dump("https://example/file.zip", blob)
    assert meta is not None
    assert meta.status_code == 206
    assert meta.content_length == 2039742911
