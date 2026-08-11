from __future__ import annotations

import asyncio

from workiva_mcp.tools import content, documents, linking, spreadsheets


def test_create_anchor_accepts_body_operation_location(monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        linking,
        "api_request",
        lambda *args, **kwargs: (202, {}, {"operationLocation": "/operations/anchor"}),
    )

    def fake_poll(location: str, label: str, **kwargs):
        calls.append((location, label))
        return {"revision": "rev-2", "data": [{"anchor": "anchor-1"}]}

    monkeypatch.setattr(linking, "poll_operation", fake_poll)

    result = linking._create_anchor_structured("table-1", "rev-1")

    assert result == {"anchor_id": "anchor-1", "revision": "rev-2", "error": None}
    assert calls == [("/operations/anchor", "Create anchor")]


def test_create_destination_link_accepts_body_operation_location(monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        linking,
        "api_request",
        lambda *args, **kwargs: (202, {}, {"operationLocation": "/operations/dest"}),
    )

    def fake_poll(location: str, label: str, **kwargs):
        calls.append((location, label))
        return {"revision": "rev-3"}

    monkeypatch.setattr(linking, "poll_operation", fake_poll)

    result = asyncio.run(
        linking.workiva_create_destination_link(
            "table", "table-1", "rev-2", "anchor-1",
            source_range_link="srl-1", source_table="src-tbl-1",
        )
    )

    assert result == "Destination link created. Revision: rev-3"
    assert calls == [("/operations/dest", "Create destination link")]


def test_document_export_accepts_body_operation_location(monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        documents,
        "api_request",
        lambda *args, **kwargs: (202, {}, {"operationLocation": "/operations/doc-export"}),
    )

    def fake_poll(location: str, label: str, **kwargs):
        calls.append((location, label))
        return {"downloadUrl": "https://download.example/doc.pdf"}

    monkeypatch.setattr(documents, "poll_operation", fake_poll)

    result = asyncio.run(documents.workiva_export_document("doc-1", "pdf"))

    assert result == "Export ready: https://download.example/doc.pdf"
    assert calls == [("/operations/doc-export", "Export document")]


def test_document_section_create_accepts_body_operation_location(monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        documents,
        "api_request",
        lambda *args, **kwargs: (202, {}, {"operationLocation": "/operations/section-create"}),
    )

    def fake_poll(location: str, label: str, **kwargs):
        calls.append((location, label))
        return {"id": "section-1", "name": "MD&A"}

    monkeypatch.setattr(documents, "poll_operation", fake_poll)

    result = asyncio.run(documents.workiva_create_document_section("doc-1", "MD&A"))

    assert result == "Created section: MD&A (id: section-1)"
    assert calls == [("/operations/section-create", "Create section")]


def test_spreadsheet_export_accepts_body_operation_location(monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        spreadsheets,
        "api_request",
        lambda *args, **kwargs: (202, {}, {"operationLocation": "/operations/ss-export"}),
    )

    def fake_poll(location: str, label: str, **kwargs):
        calls.append((location, label))
        return {"downloadUrl": "https://download.example/book.xlsx"}

    monkeypatch.setattr(spreadsheets, "poll_operation", fake_poll)

    result = asyncio.run(spreadsheets.workiva_export_spreadsheet("ss-1", "xlsx"))

    assert result == "Export ready: https://download.example/book.xlsx"
    assert calls == [("/operations/ss-export", "Export spreadsheet")]


def test_spreadsheet_publish_links_accepts_body_operation_location(monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        spreadsheets,
        "api_request",
        lambda *args, **kwargs: (202, {}, {"operationLocation": "/operations/publish-links"}),
    )

    def fake_poll(location: str, label: str, **kwargs):
        calls.append((location, label))
        return {"status": "completed"}

    monkeypatch.setattr(spreadsheets, "poll_operation", fake_poll)

    result = asyncio.run(spreadsheets.workiva_publish_links("ss-1", "spreadsheet"))

    assert result == "Links published for spreadsheet ss-1."
    assert calls == [("/operations/publish-links", "Publish links")]


def test_style_guide_export_accepts_body_operation_location(monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        content,
        "api_request",
        lambda *args, **kwargs: (202, {}, {"operationLocation": "/operations/style-export"}),
    )

    def fake_poll(location: str, label: str, **kwargs):
        calls.append((location, label))
        return {"downloadUrl": "https://download.example/style.zip"}

    monkeypatch.setattr(content, "poll_operation", fake_poll)

    result = asyncio.run(content.workiva_export_style_guide("style-1"))

    assert result == "Export ready: https://download.example/style.zip"
    assert calls == [("/operations/style-export", "Export style guide")]


def test_style_guide_import_accepts_body_operation_location(monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        content,
        "api_request",
        lambda *args, **kwargs: (202, {}, {"operationLocation": "/operations/style-import"}),
    )

    def fake_poll(location: str, label: str, **kwargs):
        calls.append((location, label))
        return {"status": "completed"}

    monkeypatch.setattr(content, "poll_operation", fake_poll)

    result = asyncio.run(content.workiva_import_style_guide("style-1"))

    assert result == "Style guide imported."
    assert calls == [("/operations/style-import", "Import style guide")]
