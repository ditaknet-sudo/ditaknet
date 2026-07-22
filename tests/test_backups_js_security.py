from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKUPS_JS = ROOT / "ditaknet/static/js/backups.js"
BACKUPS_TEMPLATE = ROOT / "ditaknet/templates/settings/backups.html"


def test_backup_table_escapes_all_server_controlled_html_fields() -> None:
    source = BACKUPS_JS.read_text(encoding="utf-8")

    assert "function escapeHtml(value)" in source
    for field in (
        "b.filename",
        "b.backup_type",
        "b.app_version",
        "b.size_display || b.size_bytes",
        "b.includes_summary",
        "b.status",
    ):
        assert f"escapeHtml({field}" in source
    assert "box.textContent" in source
    assert "command.textContent = v.offline_restore_command" in source


def test_backup_page_exposes_instructions_but_no_live_restore_action() -> None:
    source = BACKUPS_JS.read_text(encoding="utf-8")
    template = BACKUPS_TEMPLATE.read_text(encoding="utf-8")

    assert "/validate`" in source
    assert "/restore`" not in source
    assert "submitRestore" not in source
    assert 'id="restore-submit-btn"' not in template
    assert 'id="restore-command"' in template
