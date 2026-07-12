"""Attendance module settings stored in app_settings."""

from __future__ import annotations

import json
from typing import Any

from ditaknet import database as db
from ditaknet.core.employee_presence import create_privacy_audit_log
from ditaknet.core.hr.access import enforce_hr_access
from ditaknet.core.licensing import license_service

DEFAULT_PRIVACY_NOTICE = (
    "This feature calculates attendance based on approved company devices connected "
    "to the organization network or approved heartbeat. It does not inspect internet "
    "traffic or track personal location."
)


def _bool(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


async def get_attendance_settings() -> dict[str, Any]:
    await enforce_hr_access()
    status = await license_service.status()
    return {
        "licensed": bool(status.get("employee_presence_enabled")),
        "enable_employee_attendance": _bool(
            await db.get_app_setting("enable_employee_attendance", "0")
        ),
        "default_shift_id": int(await db.get_app_setting("hr_default_shift_id", "0") or 0) or None,
        "presence_online_grace_minutes": int(
            await db.get_app_setting("presence_online_grace_minutes", "5") or 5
        ),
        "presence_away_after_minutes": int(
            await db.get_app_setting("presence_away_after_minutes", "15") or 15
        ),
        "presence_offline_after_minutes": int(
            await db.get_app_setting("presence_offline_after_minutes", "60") or 60
        ),
        "ignore_gap_minutes": int(await db.get_app_setting("hr_ignore_gap_minutes", "5") or 5),
        "count_remote_as_work_time": _bool(
            await db.get_app_setting("hr_count_remote_as_work_time", "1")
        ),
        "require_high_confidence_for_auto_attendance": _bool(
            await db.get_app_setting("hr_require_high_confidence", "0")
        ),
        "allow_ip_only_attendance": _bool(
            await db.get_app_setting("hr_allow_ip_only_attendance", "0")
        ),
        "allow_manual_corrections": _bool(
            await db.get_app_setting("hr_allow_manual_corrections", "1")
        ),
        "export_reports_enabled": _bool(
            await db.get_app_setting("hr_export_reports_enabled", "1")
        ),
        "privacy_notice_required": _bool(
            await db.get_app_setting("hr_privacy_notice_required", "1")
        ),
        "privacy_notice_text": await db.get_app_setting(
            "employee_presence_privacy_notice", DEFAULT_PRIVACY_NOTICE
        ),
    }


async def update_attendance_settings(*, actor: str = "system", **fields: Any) -> dict[str, Any]:
    await enforce_hr_access()
    mapping = {
        "enable_employee_attendance": "enable_employee_attendance",
        "default_shift_id": "hr_default_shift_id",
        "presence_online_grace_minutes": "presence_online_grace_minutes",
        "presence_away_after_minutes": "presence_away_after_minutes",
        "presence_offline_after_minutes": "presence_offline_after_minutes",
        "ignore_gap_minutes": "hr_ignore_gap_minutes",
        "count_remote_as_work_time": "hr_count_remote_as_work_time",
        "require_high_confidence_for_auto_attendance": "hr_require_high_confidence",
        "allow_ip_only_attendance": "hr_allow_ip_only_attendance",
        "allow_manual_corrections": "hr_allow_manual_corrections",
        "export_reports_enabled": "hr_export_reports_enabled",
        "privacy_notice_required": "hr_privacy_notice_required",
        "privacy_notice_text": "employee_presence_privacy_notice",
    }
    for key, db_key in mapping.items():
        if key not in fields or fields[key] is None:
            continue
        value = fields[key]
        if isinstance(value, bool):
            value = "1" if value else "0"
        await db.set_app_setting(db_key, str(value))
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="attendance_settings_changed",
        details=json.dumps({k: fields[k] for k in fields if k in mapping}),
    )
    return await get_attendance_settings()
