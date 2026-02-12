"""PDF parsing tool — Azure Document Intelligence for structured extraction."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import register
from .definitions import ToolDefinition
from .util import resolve_path as _resolve_path, is_allowed as _is_allowed

log = logging.getLogger("conduit.tools.pdf")

MAX_RESULT_SIZE = 50 * 1024  # 50KB


async def _parse_pdf(path: str, pages: str = "") -> str:
    """Parse a PDF using Azure Document Intelligence. Extracts text, tables, and structure."""
    p = _resolve_path(path)
    if not p.exists():
        return f"Error: File not found: {path}"
    if not p.is_file():
        return f"Error: Not a file: {path}"
    if not _is_allowed(p):
        return f"Error: Access denied — {path} is not in an allowed directory"
    if p.suffix.lower() != ".pdf":
        return f"Error: Not a PDF file: {path}"

    endpoint = os.getenv("AZURE_DOC_INTEL_ENDPOINT", "")
    key = os.getenv("AZURE_DOC_INTEL_KEY", "")
    if not endpoint or not key:
        return "Error: Azure Document Intelligence not configured (missing AZURE_DOC_INTEL_ENDPOINT or AZURE_DOC_INTEL_KEY)"

    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
        from azure.core.credentials import AzureKeyCredential

        client = DocumentIntelligenceClient(endpoint, AzureKeyCredential(key))

        with open(p, "rb") as f:
            kwargs = {}
            if pages:
                kwargs["pages"] = pages

            poller = client.begin_analyze_document(
                "prebuilt-layout",
                body=f,
                content_type="application/octet-stream",
                **kwargs,
            )
            result = poller.result()

    except Exception as e:
        log.error("Document Intelligence error for %s: %s", path, e)
        return f"Error parsing PDF: {e}"

    output_parts = [f"**Parsed: {p.name}**\n"]

    # Extract text by page
    if result.pages:
        for page in result.pages:
            page_num = page.page_number
            lines = []
            if page.lines:
                for line in page.lines:
                    lines.append(line.content)
            if lines:
                output_parts.append(f"--- Page {page_num} ---")
                output_parts.append("\n".join(lines))
                output_parts.append("")

    # Extract tables
    if result.tables:
        for i, table in enumerate(result.tables):
            output_parts.append(f"**Table {i + 1}** ({table.row_count} rows x {table.column_count} cols)")

            # Build grid
            grid = {}
            for cell in table.cells:
                grid[(cell.row_index, cell.column_index)] = cell.content

            # Render as markdown table
            for row in range(table.row_count):
                cells = []
                for col in range(table.column_count):
                    cells.append(grid.get((row, col), ""))
                output_parts.append("| " + " | ".join(cells) + " |")
                if row == 0:
                    output_parts.append("| " + " | ".join(["---"] * table.column_count) + " |")

            output_parts.append("")

    output = "\n".join(output_parts)
    if len(output) > MAX_RESULT_SIZE:
        output = output[:MAX_RESULT_SIZE] + "\n\n... [truncated]"

    return output


def register_all():
    """Register PDF parsing tools."""
    register(ToolDefinition(
        name="parse_pdf",
        description="Parse a PDF file using Azure Document Intelligence. Extracts text, tables, and document structure. Much more accurate than basic text extraction, especially for tables and complex layouts. Use for sales reports, inventory documents, financial reports, etc.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or ~-relative path to the PDF file",
                },
                "pages": {
                    "type": "string",
                    "description": "Page range to parse (e.g. '1-3', '1,3,5'). Optional, defaults to all pages.",
                },
            },
            "required": ["path"],
        },
        handler=_parse_pdf,
        permission="none",
    ))
