import pytest

from app.domain.file_kind import detect_file_kind, has_binary_bytes, is_wide_text

OFX_SGML = b"OFXHEADER:100\r\nDATA:OFXSGML\r\n\r\n<OFX>\r\n</OFX>"
OFX_XML = b'<?xml version="1.0" encoding="UTF-8"?>\n<?OFX OFXHEADER="200"?>\n<OFX></OFX>'


@pytest.mark.parametrize(
    ("head", "kind"),
    [
        (b"%PDF-1.4\n...", "pdf"),
        (OFX_SGML, "ofx"),
        (b"\n  " + OFX_SGML, "ofx"),
        (OFX_XML, "ofx"),
        (b"<OFX>\n<BANKMSGSRSV1>", "ofx"),
        # A namespaced root with no <?OFX?> instruction, and a header that starts with DATA:.
        (b'<OFX xmlns="http://ofx.net/types/2003/04">\n<BANKMSGSRSV1>', "ofx"),
        (b'<?xml version="1.0"?>\n<OFX xmlns="http://ofx.net/types/2003/04">', "ofx"),
        (b"DATA:OFXSGML\nVERSION:102\n\n<OFX>", "ofx"),
        (b"\xef\xbb\xbfOFXHEADER:100\nCHARSET:1252\n\n<OFX>", "ofx"),
        (b"<OFXFAKE,a,b>\n1,2,3\n", "csv"),
        (b"Date,Payee,Amount\n2026-01-01,Shop,-1.00\n", "csv"),
        (b"\xef\xbb\xbfDate;Payee;Amount\n", "csv"),
        ("Date,Payee\n".encode("utf-16"), "csv"),
        ("Date,Payee\n".encode("utf-32"), "csv"),
        (b"Date\tPayee\n", "csv"),
        # A CSV that only mentions OFX is still a CSV.
        (b"Date,Payee\n2026-01-01,<OFX>\n", "csv"),
    ],
)
def test_kind_comes_from_the_bytes(head: bytes, kind: str) -> None:
    assert detect_file_kind(head) == kind


@pytest.mark.parametrize(
    "head",
    [
        b"MZ\x90\x00\x03\x00\x00\x00,,,",  # an executable with commas
        b"\x89PNG\r\n\x1a\n,,,",
        b"PK\x03\x04,,,\x00",
        b"",
        b"   \n  ",
        b"plain words only",
    ],
)
def test_binary_and_non_tabular_files_are_not_importable(head: bytes) -> None:
    assert detect_file_kind(head) is None


def test_binary_chunks_and_wide_text_are_told_apart() -> None:
    assert has_binary_bytes(b"abc\x00def")
    assert not has_binary_bytes(b"a,b\r\n\tc\n")
    assert is_wide_text("x".encode("utf-16"))
    assert not is_wide_text(b"x,y")
