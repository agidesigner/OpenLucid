"""Regression tests for the shared ``extract_text_from_source`` helper.

The helper was introduced to collapse two copy-pasted extraction paths
(``/ai/extract-text`` and ``/brandkits/{id}/extract-profile``). Drift
between them had silently broken PPTX upload on the brandkit path.

These tests verify:
  - format dispatch hits the right extractor per extension
  - the PPTX branch exists (the headline regression)
  - error paths raise HTTPException with proper status codes
  - normalization (dedup + cap) is applied before return
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException


def _fake_upload(name: str, content: bytes = b"ignored"):
    """Minimal UploadFile-shape double. Only the fields/methods the
    helper touches."""
    class _U:
        filename = name
        size = len(content)
        async def read(self):
            return content
    return _U()


class TestFormatDispatch:
    """Each filename extension must route to its specific extractor.
    Regression against the brandkit bug where .pptx fell through to
    utf-8 decode because the dispatch list was incomplete."""

    def _run(self, fname: str):
        from app.api import ai as ai_mod
        upload = _fake_upload(fname)
        # Patch every extractor to return a sentinel so we can see which fired
        with patch.object(ai_mod, "_extract_pdf_text", return_value="PDF-OUT") as p_pdf, \
             patch.object(ai_mod, "_extract_docx_text", return_value="DOCX-OUT") as p_docx, \
             patch.object(ai_mod, "_extract_excel_text", return_value="XLSX-OUT") as p_xlsx, \
             patch.object(ai_mod, "_extract_pptx_text", return_value="PPTX-OUT") as p_pptx:
            text, source, _ = asyncio.run(
                ai_mod.extract_text_from_source(file=upload, url=None)
            )
        return text, {
            "pdf": p_pdf.called, "docx": p_docx.called,
            "xlsx": p_xlsx.called, "pptx": p_pptx.called,
        }

    def test_pdf_dispatches_to_pdf_extractor(self):
        text, calls = self._run("foo.pdf")
        assert text == "PDF-OUT"
        assert calls == {"pdf": True, "docx": False, "xlsx": False, "pptx": False}

    def test_docx_dispatches_to_docx(self):
        _, calls = self._run("brief.docx")
        assert calls["docx"] is True
        assert sum(calls.values()) == 1

    def test_xlsx_dispatches_to_xlsx(self):
        _, calls = self._run("spec.xlsx")
        assert calls["xlsx"] is True
        assert sum(calls.values()) == 1

    def test_pptx_dispatches_to_pptx(self):
        """THE regression test: brandkit's old code forgot to import
        _extract_pptx_text, so .pptx fell through to utf-8 decode of
        the raw zip bytes. This guarantees .pptx hits _extract_pptx_text."""
        _, calls = self._run("deck.pptx")
        assert calls["pptx"] is True, "PPTX must dispatch to _extract_pptx_text, not the utf-8 fallthrough"
        assert sum(calls.values()) == 1

    def test_legacy_ppt_also_hits_pptx_branch(self):
        _, calls = self._run("deck.ppt")
        assert calls["pptx"] is True

    def test_txt_returns_decoded_content(self):
        from app.api import ai as ai_mod
        upload = _fake_upload("notes.txt", content="hello world".encode("utf-8"))
        text, source, _ = asyncio.run(
            ai_mod.extract_text_from_source(file=upload, url=None)
        )
        assert text == "hello world"
        assert source == "file"


class TestErrorPaths:
    """Bugs we fixed: tuple-return errors and silent garbage decode."""

    def test_unsupported_format_raises_http_400(self):
        from app.api import ai as ai_mod
        upload = _fake_upload("photo.png", content=b"\x89PNG...")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ai_mod.extract_text_from_source(file=upload, url=None))
        assert exc.value.status_code == 400
        assert "Unsupported" in exc.value.detail

    def test_missing_input_raises_http_400(self):
        from app.api import ai as ai_mod
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ai_mod.extract_text_from_source(file=None, url=None))
        assert exc.value.status_code == 400
        assert "file or URL" in exc.value.detail

    def test_empty_file_raises_http_400(self):
        from app.api import ai as ai_mod
        upload = _fake_upload("blank.pdf", content=b"")
        with patch.object(ai_mod, "_extract_pdf_text", return_value=""):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(ai_mod.extract_text_from_source(file=upload, url=None))
        assert exc.value.status_code == 400
        assert "Failed to extract" in exc.value.detail


class TestNormalizationApplied:
    """The shared helper must run extracted text through
    _normalize_extracted before returning. Otherwise the brandkit path
    would still feed raw noisy text to the LLM."""

    def test_dedup_and_cap_applied(self):
        from app.api import ai as ai_mod

        raw = "A\n" + "repeated footer\n" * 10 + "B\n" + "repeated footer\n" * 5 + "C"
        upload = _fake_upload("doc.pdf")
        with patch.object(ai_mod, "_extract_pdf_text", return_value=raw):
            text, _, _ = asyncio.run(
                ai_mod.extract_text_from_source(file=upload, url=None)
            )
        # Repeated line collapsed to one
        assert text.count("repeated footer") == 1
        # Unique content preserved
        assert "A" in text and "B" in text and "C" in text

    def test_cap_respected(self):
        from app.api import ai as ai_mod

        giant = ("X" * 100) + "\n"
        upload = _fake_upload("doc.pdf")
        with patch.object(ai_mod, "_extract_pdf_text", return_value=giant * 10_000):
            text, _, _ = asyncio.run(
                ai_mod.extract_text_from_source(file=upload, url=None)
            )
        assert len(text) <= ai_mod._EXTRACT_CAP_FILE


