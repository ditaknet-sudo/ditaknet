"""Static security wiring checks for all state-changing HTML routers."""

from __future__ import annotations

from pathlib import Path


WEB_ROOT = Path(__file__).resolve().parents[1] / "ditaknet" / "web"


def test_every_html_router_with_post_routes_enables_csrf_dependency() -> None:
    missing: list[str] = []

    for path in sorted(WEB_ROOT.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "@router.post" not in source:
            continue
        if "dependencies=[Depends(verify_web_csrf)]" not in source:
            missing.append(path.name)

    assert missing == [], f"HTML POST routers without CSRF dependency: {missing}"


def test_production_csrf_code_has_no_test_environment_bypass() -> None:
    security_source = (WEB_ROOT.parent / "security.py").read_text(encoding="utf-8")

    assert "PYTEST_CURRENT_TEST" not in security_source
    assert 'os.getenv("PYTEST' not in security_source


def test_browser_fetch_wrapper_adds_csrf_header_to_mutations() -> None:
    app_javascript = (
        WEB_ROOT.parent / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'headers.set("X-CSRF-Token", token)' in app_javascript
    assert 'target.origin === window.location.origin' in app_javascript
