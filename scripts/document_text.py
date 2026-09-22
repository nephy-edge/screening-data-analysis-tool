"""Extract readable text from uploaded supporting documents (PDF, Excel,
Word, plain text) for inclusion in AI context. Framework-agnostic (works on
raw bytes, no Streamlit/FastAPI import) so app.py's "Additional context for
the AI" uploader and web_api/main.py's /api/context-docs endpoint extract
identically instead of each reimplementing the parsers.
"""

import io


def _extract_text_file(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("PDF support requires the 'pypdf' package (pip install pypdf).")
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    return "\n".join(parts)


def _extract_excel_text(data: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines = []
    try:
        for ws in wb.worksheets:
            lines.append(f"=== Sheet: {ws.title} ===")
            for n, row in enumerate(ws.iter_rows(values_only=True), 1):
                vals = [str(v) for v in row if v is not None]
                if vals:
                    lines.append(" | ".join(vals))
                if n >= 2000:
                    lines.append("...[sheet truncated at 2000 rows]")
                    break
    finally:
        wb.close()
    return "\n".join(lines)


def _extract_docx_text(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError:
        raise RuntimeError(
            "Word support requires the 'python-docx' package (pip install python-docx)."
        )
    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def extract_document_text(name: str, data: bytes) -> dict:
    """Extract readable text from a document's raw bytes. Returns
    {"name", "text", "error"}; a missing parser or a parse failure surfaces
    as an "error" note instead of raising, so one bad upload doesn't break
    the others."""
    name = name or "document"
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    try:
        if suffix == "pdf":
            text = _extract_pdf_text(data)
        elif suffix in ("xlsx", "xlsm"):
            text = _extract_excel_text(data)
        elif suffix == "docx":
            text = _extract_docx_text(data)
        elif suffix in ("txt", "csv", "md", "json", "log"):
            text = _extract_text_file(data)
        else:
            return {
                "name": name,
                "text": "",
                "error": f"Unsupported file type '.{suffix}' - use PDF, Excel, Word (.docx), or text files.",
            }
    except Exception as e:
        return {"name": name, "text": "", "error": str(e)}
    return {"name": name, "text": text, "error": None}


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, {len(text) - limit:,} chars omitted]"


def combine_context_text(
    notes: str, documents: list, max_chars: int = 60000, per_doc: int = 15000
) -> str:
    """Combine free-text notes with extracted document text into one prompt
    block, truncated to keep AI calls bounded. `documents` is a list of
    {"name", "text", "error"} dicts as returned by extract_document_text."""
    parts = []
    notes = (notes or "").strip()
    if notes:
        parts.append("## Additional context notes\n" + notes)
    for doc in documents or []:
        if doc.get("error"):
            parts.append(f"## Uploaded document: {doc['name']}\n[Could not be used: {doc['error']}]")
        else:
            parts.append(f"## Uploaded document: {doc['name']}\n" + truncate_text(doc.get("text", ""), per_doc))
    text = "\n\n".join(parts)
    return truncate_text(text, max_chars) if len(text) > max_chars else text
