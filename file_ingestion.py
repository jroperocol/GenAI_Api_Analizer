"""Utilities for extracting text from uploaded files."""

from __future__ import annotations

from io import BytesIO
from typing import Iterable, List, Tuple

import pandas as pd
from docx import Document
from pypdf import PdfReader

SUPPORTED_TYPES = ["pdf", "docx", "xlsx", "txt", "png", "jpg", "jpeg"]
IMAGE_TYPES = {"png", "jpg", "jpeg"}


def _read_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(file_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _read_docx(file_bytes: bytes) -> str:
    document = Document(BytesIO(file_bytes))
    return "\n".join(p.text for p in document.paragraphs)


def _read_xlsx(file_bytes: bytes) -> str:
    workbook = pd.read_excel(BytesIO(file_bytes), sheet_name=None, dtype=str)
    blocks = []
    for sheet_name, frame in workbook.items():
        blocks.append(f"[Sheet: {sheet_name}]")
        blocks.append(frame.fillna("").to_csv(index=False))
    return "\n".join(blocks)


def extract_text_from_uploads(uploaded_files: Iterable) -> Tuple[str, List[str], List[str]]:
    """Return combined text, source filenames, and ingestion warnings."""
    combined_chunks: List[str] = []
    source_files: List[str] = []
    warnings: List[str] = []

    for uploaded in uploaded_files:
        name = uploaded.name
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        source_files.append(name)
        file_bytes = uploaded.getvalue()
        if extension == "pdf":
            combined_chunks.append(f"\n=== FILE: {name} ===\n{_read_pdf(file_bytes)}")
        elif extension == "docx":
            combined_chunks.append(f"\n=== FILE: {name} ===\n{_read_docx(file_bytes)}")
        elif extension == "xlsx":
            combined_chunks.append(f"\n=== FILE: {name} ===\n{_read_xlsx(file_bytes)}")
        elif extension == "txt":
            combined_chunks.append(f"\n=== FILE: {name} ===\n{file_bytes.decode('utf-8', errors='ignore')}")
        elif extension in IMAGE_TYPES:
            warnings.append(f"{name}: Image text extraction is limited in V1.")
            combined_chunks.append(f"\n=== FILE: {name} ===\n[Image file uploaded. OCR not enabled in V1.]")
        else:
            warnings.append(f"{name}: Unsupported file type.")

    return "\n".join(combined_chunks).strip(), source_files, warnings
