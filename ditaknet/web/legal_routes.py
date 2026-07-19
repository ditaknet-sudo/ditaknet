"""Public legal document pages for self-hosted dashboard."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from ditaknet.core.legal import LEGAL_DOCUMENTS, document_for_slug, read_document

router = APIRouter(include_in_schema=False)


def _read_markdown(slug: str) -> str:
    payload = read_document(slug)
    if not payload:
        raise HTTPException(status_code=404, detail="Legal document not found")
    return str(payload.get("content") or "")


@router.get("/legal", response_class=HTMLResponse)
async def legal_index():
    links = "\n".join(
        f'<li><a href="{doc.route}">{doc.slug}</a></li>' for doc in LEGAL_DOCUMENTS
    )
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>DitakNet Legal</title></head>
<body style="font-family:system-ui;max-width:48rem;margin:2rem auto;padding:0 1rem;line-height:1.6">
<h1>DitakNet Legal Documents</h1>
<p>Draft documents — not final legal advice.</p>
<ul>{links}</ul>
</body></html>"""
    )


@router.get("/legal/{slug}", response_class=HTMLResponse)
async def legal_page(slug: str):
    markdown = _read_markdown(slug)
    body = (
        markdown.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{slug} — DitakNet</title></head>
<body style="font-family:system-ui;max-width:48rem;margin:2rem auto;padding:0 1rem;line-height:1.6">
<pre style="white-space:pre-wrap;word-break:break-word">{body}</pre>
<p><a href="/legal">Back to legal index</a></p>
</body></html>"""
    )


@router.get("/legal/{slug}/download", response_class=PlainTextResponse)
async def legal_download(slug: str):
    doc = document_for_slug(slug)
    if not doc:
        raise HTTPException(status_code=404, detail="Legal document not found")
    markdown = _read_markdown(slug)
    return PlainTextResponse(
        markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{doc.file_name}"'},
    )
