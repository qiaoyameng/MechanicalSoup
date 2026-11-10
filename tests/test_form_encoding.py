"""Tests for explicit form submission encodings.

These tests use a local transport adapter that records the
PreparedRequest objects MechanicalSoup produces, so no network access or
third-party test server is required. They cover UTF-8 (the unchanged
default), Shift_JIS with unrepresentable characters, the accept-charset
mechanism, GET/POST parity, multipart text vs. binary handling and the
``_charset_`` hidden field.
"""
import io
from urllib.parse import parse_qsl, urlsplit

import pytest
import setpath  # noqa:F401, must come before 'import mechanicalsoup'
from bs4 import BeautifulSoup

import mechanicalsoup
from mechanicalsoup.form import (
    ACCEPT_CHARSET,
    DEFAULT_FORM_ENCODING,
    encode_form_text,
    resolve_form_encoding,
)
import requests


class RecordingAdapter(requests.adapters.HTTPAdapter):
    """Adapter capturing every PreparedRequest it is asked to send."""

    def __init__(self):
        super().__init__()
        self.requests = []

    def send(self, request, *args, **kwargs):
        self.requests.append(request)
        response = requests.Response()
        response.status_code = 200
        response._content = b"<html><body>Success!</body></html>"
        response.headers["Content-Type"] = "text/html"
        response.request = request
        return response


@pytest.fixture
def recorder():
    adapter = RecordingAdapter()
    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={"mock": adapter})
    return browser, adapter


def make_form(html):
    return BeautifulSoup(html, "lxml").form


def test_default_encoding_is_utf8_post(recorder):
    browser, adapter = recorder
    form = make_form(
        '<form method="post" action="mock://form.test/post">'
        '<input name="greeting" value="中文"></form>')
    browser.open_fake_page(str(form), url="mock://form.test/page")
    browser.select_form()
    browser.submit_selected()

    request = adapter.requests[-1]
    assert request.method == "POST"
    assert request.body == b"greeting=%E4%B8%AD%E6%96%87"
    content_type = request.headers["Content-Type"]
    assert "application/x-www-form-urlencoded" in content_type


def test_explicit_shift_jis_post(recorder):
    browser, adapter = recorder
    form = make_form(
        '<form method="post" action="mock://form.test/post">'
        '<input name="city" value="日本"></form>')
    browser.open_fake_page(str(form), url="mock://form.test/page")
    browser.select_form()
    browser.submit_selected(form_encoding="shift_jis")

    body = adapter.requests[-1].body
    assert body == b"city=%93%FA%96%7B"
    # Decoding with the declared encoding recovers the original text.
    _key, value = body.split(b"=", 1)
    assert bytes.fromhex(value.replace(b"%", b"").decode()).decode(
        "shift_jis") == "日本"


def test_unrepresentable_characters_become_numeric_references(recorder):
    browser, adapter = recorder
    html = (
        '<form method="post" action="mock://form.test/post" '
        'accept-charset="shift_jis">'
        '<input name="mixed" value="日本😀中文"></form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()
    browser.submit_selected(form_encoding="shift_jis")

    body = adapter.requests[-1].body.decode("ascii")
    # 日本 is representable in Shift_JIS; the emoji and Chinese characters
    # are replaced by decimal HTML numeric character references.
    ncr_emoji = "&#128512;".replace("&", "%26").replace(
        "#", "%23").replace(";", "%3B")
    expected_value = "%93%FA%96%7B" + ncr_emoji + "%92%86%95%B6"
    assert body == "mixed=" + expected_value


def test_get_query_uses_same_encoding_as_post_body(recorder):
    browser, adapter = recorder
    html = (
        '<form method="get" action="mock://form.test/search">'
        '<input name="q" value="日本"></form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()
    browser.submit_selected(form_encoding="shift_jis")

    get_request = adapter.requests[-1]
    query = urlsplit(get_request.url).query
    assert query == "q=%93%FA%96%7B"

    # Same form with method=post must produce an identical encoded pair.
    html_post = html.replace('method="get"', 'method="post"')
    browser.open_fake_page(html_post, url="mock://form.test/page")
    browser.select_form()
    browser.submit_selected(form_encoding="shift_jis")
    post_body = adapter.requests[-1].body.decode("ascii")
    assert post_body == query


def test_get_preserves_existing_action_query(recorder):
    browser, adapter = recorder
    html = (
        '<form method="get" '
        'action="mock://form.test/search?existing=a%20b&token=%E4%B8%AD">'
        '<input name="q" value="日本"></form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()
    browser.submit_selected(form_encoding="shift_jis")

    parts = urlsplit(adapter.requests[-1].url)
    assert parts.path == "/search"
    # The pre-encoded action query is forwarded byte-for-byte, without being
    # percent-encoded a second time, and the form data is appended after it.
    assert parts.query == (
        "existing=a%20b&token=%E4%B8%AD&q=%93%FA%96%7B")


def test_repeated_fields_and_submit_button_keep_order(recorder):
    browser, adapter = recorder
    html = (
        '<form method="post" action="mock://form.test/post">'
        '<input name="tag" value="a">'
        '<input name="tag" value="b">'
        '<input name="tag" value="c">'
        '<input type="submit" name="go" value="送信">'
        '</form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()
    browser.submit_selected(form_encoding="shift_jis")

    body = adapter.requests[-1].body.decode("ascii")
    pairs = parse_qsl(body, keep_blank_values=True,
                      encoding="latin-1", errors="replace")
    assert [name for name, _ in pairs] == ["tag", "tag", "tag", "go"]
    assert pairs[:3] == [("tag", "a"), ("tag", "b"), ("tag", "c")]
    # Submit button value encoded in Shift_JIS as well.
    submit_value = pairs[-1][1]
    assert submit_value.encode("latin-1") == "送信".encode("shift_jis")
    assert "go=%91%97%90M" in body


def test_no_unicode_normalization(recorder):
    browser, adapter = recorder
    # U+2126 OHM SIGN and U+03A9 GREEK CAPITAL LETTER OMEGA are canonically
    # equivalent but distinct code points; submission must not normalize them.
    ohm = "\u2126"
    omega = "\u03a9"
    html = (
        '<form method="post" action="mock://form.test/post">'
        f'<input name="a" value="{ohm}">'
        f'<input name="b" value="{omega}"></form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()
    browser.submit_selected()

    body = adapter.requests[-1].body.decode("ascii")
    pairs = dict(parse_qsl(body))
    assert pairs["a"] == ohm
    assert pairs["b"] == omega
    assert pairs["a"] != pairs["b"]


def test_charset_hidden_field_reports_actual_encoding(recorder):
    browser, adapter = recorder
    html = (
        '<form method="post" action="mock://form.test/post" '
        'accept-charset="shift_jis">'
        '<input type="hidden" name="_charset_" value="">'
        '<input name="city" value="日本"></form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()
    browser.submit_selected(form_encoding=ACCEPT_CHARSET)

    body = adapter.requests[-1].body.decode("ascii")
    pairs = dict(parse_qsl(body))
    assert pairs["_charset_"] == "shift_jis"
    assert "city=%93%FA%96%7B" in body


def test_accept_charset_picks_first_supported_encoding(recorder):
    browser, adapter = recorder
    html = (
        '<form method="post" action="mock://form.test/post" '
        'accept-charset="x-made-up, shift_jis, utf-8">'
        '<input name="city" value="日本"></form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()
    browser.submit_selected(form_encoding=ACCEPT_CHARSET)

    body = adapter.requests[-1].body
    assert b"city=%93%FA%96%7B" in body


def test_accept_charset_falls_back_to_utf8():
    form = BeautifulSoup(
        '<form accept-charset="x-made-up, y-unknown"></form>',
        "lxml").form
    assert resolve_form_encoding(form, ACCEPT_CHARSET) == DEFAULT_FORM_ENCODING

    no_attribute = BeautifulSoup("<form></form>", "lxml").form
    assert resolve_form_encoding(
        no_attribute, ACCEPT_CHARSET) == DEFAULT_FORM_ENCODING


def test_prepare_request_can_be_inspected_without_sending(recorder):
    browser, adapter = recorder
    html = (
        '<form method="post" action="mock://form.test/post" '
        'accept-charset="shift_jis">'
        '<input name="city" value="日本😀"></form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()

    prepared = browser.prepare_selected(form_encoding=ACCEPT_CHARSET)
    assert isinstance(prepared, requests.PreparedRequest)
    # Nothing has been sent yet.
    assert adapter.requests == []
    assert prepared.method == "POST"
    # The NCR is itself percent-escaped inside the urlencoded body.
    assert b"%26%23128512%3B" in prepared.body
    # The very same PreparedRequest can then be transmitted manually.
    response = browser.session.send(prepared)
    assert response.status_code == 200
    assert adapter.requests == [prepared]


def test_multipart_text_encoded_but_file_bytes_left_untouched(recorder):
    browser, adapter = recorder
    binary_content = bytes(range(256))
    html = (
        '<form method="post" action="mock://form.test/upload" '
        'enctype="multipart/form-data" accept-charset="shift_jis">'
        '<input name="note" value="日本">'
        '<input type="file" name="upload">'
        '</form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()
    browser["upload"] = io.BytesIO(binary_content)
    browser.submit_selected(form_encoding=ACCEPT_CHARSET)

    request = adapter.requests[-1]
    body = request.body
    content_type = request.headers["Content-Type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.split("boundary=", 1)[1].encode("ascii")

    # The text part is Shift_JIS encoded.
    text_part = (
        b'Content-Disposition: form-data; name="note"\r\n\r\n'
        + "日本".encode("shift_jis"))
    assert text_part in body
    # The raw binary file content is embedded byte-for-byte.
    assert binary_content in body
    assert b'name="upload"' in body
    # The body is delimited exactly by the advertised boundary.
    assert body.startswith(b"--" + boundary + b"\r\n")
    assert body.endswith(b"--" + boundary + b"--\r\n")


def test_multipart_unrepresentable_text_uses_numeric_references(recorder):
    browser, adapter = recorder
    html = (
        '<form method="post" action="mock://form.test/upload" '
        'enctype="multipart/form-data" accept-charset="shift_jis">'
        '<input name="note" value="a😀b"></form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()
    browser.submit_selected(form_encoding=ACCEPT_CHARSET)

    body = adapter.requests[-1].body
    assert b"a&#128512;b" in body


def test_multipart_filename_encoded_without_touching_file(recorder):
    browser, adapter = recorder
    file_obj = io.BytesIO(b"\x00\x01\xff\xfe")
    file_obj.name = "/tmp/日本.dat"
    html = (
        '<form method="post" action="mock://form.test/upload" '
        'enctype="multipart/form-data">'
        '<input type="file" name="upload"></form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()
    browser["upload"] = file_obj
    browser.submit_selected(form_encoding="shift_jis")

    body = adapter.requests[-1].body
    escaped = "".join(
        f"%{byte:02X}" if byte > 0x7E else chr(byte)
        for byte in "日本.dat".encode("shift_jis"))
    escaped_name = escaped
    assert f'filename="{escaped_name}"'.encode("ascii") in body
    assert b"\x00\x01\xff\xfe" in body


def test_response_decoding_is_unchanged(recorder):
    browser, adapter = recorder
    html = (
        '<form method="post" action="mock://form.test/post">'
        '<input name="q" value="日本"></form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()
    response = browser.submit_selected(form_encoding="shift_jis")

    # The response is parsed through the regular add_soup pipeline; the
    # submission encoding must not leak into response decoding settings.
    assert response.request is adapter.requests[-1]
    assert response.soup is not None


def test_response_encoding_is_independent_of_submission_encoding():
    # The response declares its own Shift_JIS encoding; submitting the form
    # in UTF-8 must not silently switch the response decoding to UTF-8.
    class ShiftJisAdapter(requests.adapters.HTTPAdapter):
        def send(self, request, *args, **kwargs):
            response = requests.Response()
            response.status_code = 200
            response._content = (
                "<html><body>応答</body></html>".encode("shift_jis"))
            response.headers["Content-Type"] = "text/html; charset=shift_jis"
            response.request = request
            return response

    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={"mock": ShiftJisAdapter()})
    browser.open_fake_page(
        '<form method="post" action="mock://form.test/post">'
        '<input name="q" value="日本"></form>',
        url="mock://form.test/page")
    browser.select_form()
    response = browser.submit_selected(form_encoding="utf-8")

    # add_soup honors the charset declared in the response Content-Type even
    # though the request body was encoded as UTF-8.
    assert response.soup.body.text == "応答"


def test_encode_form_text_helper():
    assert encode_form_text("abc", "shift_jis") == b"abc"
    assert encode_form_text("日本", "shift_jis") == "日本".encode("shift_jis")
    assert encode_form_text("😀", "shift_jis") == b"&#128512;"
    # Non-string values are stringified instead of raising.
    assert encode_form_text(123, "shift_jis") == b"123"


def test_low_level_browser_submit_and_prepare(recorder):
    _stateful, adapter = recorder
    browser = mechanicalsoup.Browser(
        requests_adapters={"mock": adapter})
    html = (
        '<form method="post" action="mock://form.test/post">'
        '<input name="city" value="日本"></form>')
    form = BeautifulSoup(html, "lxml").form

    prepared = browser.prepare_request(
        form, "mock://form.test/page", form_encoding="shift_jis")
    assert isinstance(prepared, requests.PreparedRequest)
    assert prepared.body == b"city=%93%FA%96%7B"
    assert adapter.requests == []

    response = browser.submit(
        form, "mock://form.test/page", form_encoding="shift_jis")
    assert response.status_code == 200
    assert adapter.requests[-1].body == b"city=%93%FA%96%7B"


def test_charset_field_is_not_invented_when_absent(recorder):
    browser, adapter = recorder
    html = (
        '<form method="post" action="mock://form.test/post" '
        'accept-charset="shift_jis">'
        '<input name="city" value="日本"></form>')
    browser.open_fake_page(html, url="mock://form.test/page")
    browser.select_form()
    browser.submit_selected(form_encoding=ACCEPT_CHARSET)

    body = adapter.requests[-1].body.decode("ascii")
    assert "_charset_" not in body
    assert body == "city=%93%FA%96%7B"
