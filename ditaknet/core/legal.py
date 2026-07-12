"""Legal/trust document registry and safe status detection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs" / "legal"


@dataclass(frozen=True)
class LegalDocument:
    slug: str
    file_name: str
    title_key: str
    description_key: str

    @property
    def route(self) -> str:
        return f"/legal/{self.slug}"

    @property
    def path(self) -> Path:
        return DOCS_ROOT / self.file_name


LEGAL_DOCUMENTS: tuple[LegalDocument, ...] = (
    LegalDocument("privacy-policy", "PRIVACY_POLICY.md", "privacy_policy", "legal.privacy_policy.description"),
    LegalDocument("eula", "EULA.md", "eula", "legal.eula.description"),
    LegalDocument("dpa", "DPA.md", "data_processing_addendum", "legal.dpa.description"),
    LegalDocument("acceptable-use", "ACCEPTABLE_USE.md", "acceptable_use_policy", "legal.acceptable_use.description"),
    LegalDocument("security", "SECURITY_POLICY.md", "security_policy", "legal.security_policy.description"),
    LegalDocument("support-sla", "SUPPORT_SLA.md", "support_sla", "legal.support_sla.description"),
    LegalDocument("open-source-notices", "OPEN_SOURCE_NOTICES.md", "open_source_notices", "legal.open_source.description"),
    LegalDocument(
        "employee-monitoring-notice",
        "EMPLOYEE_MONITORING_NOTICE.md",
        "employee_monitoring_notice",
        "legal.employee_monitoring.description",
    ),
)

_DRAFT_MARKERS = ("draft", "needs owner review", "lawyer review", "not final legal advice")


def document_for_slug(slug: str) -> LegalDocument | None:
    normalized = slug.strip().lower()
    return next((doc for doc in LEGAL_DOCUMENTS if doc.slug == normalized), None)


def detect_document_status(doc: LegalDocument) -> dict[str, Any]:
    """Return public-safe status for a legal document file."""
    path = doc.path
    if not path.is_file():
        return {
            "slug": doc.slug,
            "title_key": doc.title_key,
            "description_key": doc.description_key,
            "status": "not_configured",
            "route": doc.route,
            "file": f"docs/legal/{doc.file_name}",
            "available": False,
            "needs_owner_review": True,
        }

    text = path.read_text(encoding="utf-8", errors="ignore")
    lowered = text.lower()
    status = "draft" if any(marker in lowered for marker in _DRAFT_MARKERS) else "available"
    return {
        "slug": doc.slug,
        "title_key": doc.title_key,
        "description_key": doc.description_key,
        "status": status,
        "route": doc.route,
        "file": f"docs/legal/{doc.file_name}",
        "available": True,
        "needs_owner_review": status == "draft",
    }


def legal_documents_status() -> list[dict[str, Any]]:
    return [detect_document_status(doc) for doc in LEGAL_DOCUMENTS]


def legal_summary() -> dict[str, str]:
    return {item["slug"]: item["status"] for item in legal_documents_status()}


def read_document(slug: str) -> dict[str, Any] | None:
    doc = document_for_slug(slug)
    if not doc:
        return None
    status = detect_document_status(doc)
    if doc.path.is_file():
        content = doc.path.read_text(encoding="utf-8", errors="ignore").strip()
    else:
        content = (
            "# Document not configured\n\n"
            "This document is being finalized. Contact support for the current version."
        )
    return {**status, "content": content}
