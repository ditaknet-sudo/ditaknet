"""Employee Attendance & Presence — corporate HR module (separate from device monitoring)."""

from ditaknet.core.hr.access import enforce_hr_access, get_user_department_scope
from ditaknet.core.hr.attendance import (
    calculate_attendance_day,
    list_attendance_days,
    manual_correction,
    manual_check_in_out,
    record_presence_event,
    refresh_attendance_for_date,
)
from ditaknet.core.hr.catalog import (
    create_department,
    create_employee_group,
    create_shift,
    create_shift_assignment,
    list_departments,
    list_employee_groups,
    list_shifts,
    resolve_shift_for_employee,
    update_department,
    update_employee_group,
    update_shift,
)
from ditaknet.core.hr.employees import create_employee, get_employee, get_employee_with_devices, list_employees, update_employee
from ditaknet.core.hr.reports import export_monthly_csv, monthly_report
from ditaknet.core.hr.settings import get_attendance_settings, update_attendance_settings
from ditaknet.core.hr.summary import attendance_dashboard_summary, today_attendance_summary

__all__ = [
    "enforce_hr_access",
    "get_user_department_scope",
    "create_department",
    "update_department",
    "list_departments",
    "create_employee_group",
    "update_employee_group",
    "list_employee_groups",
    "create_shift",
    "update_shift",
    "list_shifts",
    "create_shift_assignment",
    "resolve_shift_for_employee",
    "create_employee",
    "update_employee",
    "get_employee",
    "get_employee_with_devices",
    "list_employees",
    "calculate_attendance_day",
    "list_attendance_days",
    "refresh_attendance_for_date",
    "record_presence_event",
    "manual_check_in_out",
    "manual_correction",
    "monthly_report",
    "export_monthly_csv",
    "get_attendance_settings",
    "update_attendance_settings",
    "today_attendance_summary",
    "attendance_dashboard_summary",
]
