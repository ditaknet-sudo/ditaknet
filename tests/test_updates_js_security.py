from pathlib import Path


UPDATES_JS = (
    Path(__file__).resolve().parents[1] / "ditaknet" / "static" / "js" / "updates.js"
)


def _source() -> str:
    return UPDATES_JS.read_text(encoding="utf-8")


def test_remote_update_content_is_never_rendered_as_html() -> None:
    source = _source()

    assert ".innerHTML" not in source
    assert "body.replaceChildren()" in source
    assert "message.textContent = manifestMessage" in source
    assert "content.textContent = releaseNotes" in source
    assert "title.textContent = item.title || heading" in source
    assert 'message.appendChild(document.createTextNode(item.message || ""))' in source


def test_external_update_links_use_a_strict_https_host_allowlist() -> None:
    source = _source()

    assert '"github.com"' in source
    assert '"raw.githubusercontent.com"' in source
    assert '"ghcr.io"' in source
    assert 'parsed.protocol !== "https:"' in source
    assert "!allowedExternalHosts.has(hostname)" in source
    assert "trustedExternalUrl(item.url)" in source
    assert "trustedExternalUrl(data.release_notes_url || data.release_url" in source
    assert "window._updatesReleaseUrl = url" in source


def test_update_page_checks_http_status_before_treating_requests_as_successful() -> (
    None
):
    source = _source()

    assert "if (response && response.ok) return response" in source
    assert source.count("await requireOk(") >= 3
