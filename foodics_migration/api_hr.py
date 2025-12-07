# apps/foodics_migration/foodics_migration/api_hr.py

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import frappe
import pandas as pd
from datetime import datetime


# ---------------------------
# Utilities
# ---------------------------

def _is_nan(v) -> bool:
    try:
        return isinstance(v, float) and math.isnan(v)
    except Exception:
        return False


def _clean_val(v):
    """Convert pandas NaN/'nan'/None/'' to None; strip strings."""
    if v is None:
        return None
    if _is_nan(v):
        return None
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def _coerce_date(v: Optional[str]) -> Optional[str]:
    """Return YYYY-MM-DD or None."""
    v = _clean_val(v)
    if not v:
        return None
    # Try common patterns; fallback to pandas
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except Exception:
            pass
    try:
        ts = pd.to_datetime(v, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.date().isoformat()
    except Exception:
        return None


def _coerce_gender(v: Optional[str]) -> Optional[str]:
    v = _clean_val(v)
    if not v:
        return None
    v_norm = v.lower()
    if v_norm in ("m", "male", "ذكر", "ذَكَر"):
        return "Male"
    if v_norm in ("f", "female", "انثى", "أنثى", "انثي", "أنثي"):
        return "Female"
    return None


def _set_if_value(doc, fieldname: str, value: Any):
    if value is not None and fieldname in doc.as_dict():
        doc.set(fieldname, value)


def _normalize_header(h: str) -> str:
    """Normalize Arabic headers: remove tatweel, trim spaces, unify common variants."""
    if h is None:
        return ""
    s = str(h).replace("ـ", "").strip()  # remove tatweel
    # common variants
    s = s.replace("الاســــم", "الاسم")
    s = s.replace("الاداره", "الادارة")
    # strip extra spaces
    while "  " in s:
        s = s.replace("  ", " ")
    return s


# ---------------------------
# Column mapping
# ---------------------------

# These are the *normalized* column names we expect after _normalize_header
FIELD_MAP = {
    "الاسم": "employee_name",
    "الجنس": "gender",
    "المسمى الوظيفي": "designation",
    "الفرع": "branch",
    "تاريخ الميلاد": "date_of_birth",
    "رقم الإقامة": "custom_residence_number",
    "تاريخ انتهاء الاقامه": "custom_residence_end_date",
    "تاريخ انتهاء الجواز": "valid_upto",
    "تاريخ المباشرة": "date_of_joining",
    "تاريخ بدايه العقد": "final_confirmation_date",
    "رقم الجوال": "cell_number",
    "الايميل": "company_email",
    "اسم البنك": "bank_name",
    "الايبان": "iban",
    "الجنسية": "custom_nationality",
    # You can extend with more if you later add custom fields on Employee
}

# Branch mapping from sheet → ERPNext
BRANCH_MAP = {
    "الادارة": "الرياض-الإدارة",
    "الاداره": "الرياض-الإدارة",
    "المستودع": "الرياض-المستودع",
    "التحلية": "الرياض-التحلية",
    "لبن": "الرياض-لبن",
    "غرناطة": "الرياض-غرناطة",
}


def _map_branch(sheet_val: Optional[str]) -> Optional[str]:
    v = _clean_val(sheet_val)
    if not v:
        return None
    return BRANCH_MAP.get(v, v)  # fall back to itself if not mapped


# ---------------------------
# DB helpers
# ---------------------------

def _get_existing_employee(upsert_key: str, row_vals: Dict[str, Any]) -> Optional[str]:
    key = _clean_val(upsert_key) or ""
    if not key:
        return None
    val = _clean_val(row_vals.get(key))
    if not val:
        return None
    return frappe.db.get_value("Employee", {key: val}, "name")


# ---------------------------
# Upsert one Employee
# ---------------------------

def _upsert_employee(
    row_vals: Dict[str, Any],
    default_company: Optional[str],
    naming_series: Optional[str],
    upsert_key: Optional[str],
) -> str:
    employee_name = _clean_val(row_vals.get("employee_name")) or ""
    if not employee_name:
        raise frappe.ValidationError("Missing required employee_name")

    # First Name required by ERPNext core
    first_name = employee_name
    gender = _coerce_gender(_clean_val(row_vals.get("gender"))) or "Male"

    branch_in = _clean_val(row_vals.get("branch"))
    branch_mapped = _map_branch(branch_in) if branch_in else None

    existing_name = _get_existing_employee(upsert_key or "", row_vals)

    if existing_name:
        print(f"  ↺ Update Employee: {existing_name} ({employee_name})")
        doc = frappe.get_doc("Employee", existing_name)
    else:
        print(f"  ➕ Create Employee: {employee_name}")
        payload = {
            "doctype": "Employee",
            "employee_name": employee_name,
            "first_name": first_name,
            "gender": gender,
        }
        if naming_series:
            payload["naming_series"] = naming_series
        if default_company:
            payload["company"] = default_company
        doc = frappe.get_doc(payload)

    # Core fields
    _set_if_value(doc, "employee_name", employee_name)
    _set_if_value(doc, "first_name", first_name)
    _set_if_value(doc, "gender", gender)
    if default_company:
        _set_if_value(doc, "company", default_company)
    _set_if_value(doc, "designation", _clean_val(row_vals.get("designation")))
    _set_if_value(doc, "custom_nationality", _clean_val(row_vals.get("custom_nationality")))
    _set_if_value(doc, "cell_number", _clean_val(row_vals.get("cell_number")))
    _set_if_value(doc, "company_email", _clean_val(row_vals.get("company_email")))
    _set_if_value(doc, "branch", branch_mapped)

    # Dates
    _set_if_value(doc, "date_of_birth", _coerce_date(row_vals.get("date_of_birth")))
    _set_if_value(doc, "date_of_joining", _coerce_date(row_vals.get("date_of_joining")))
    _set_if_value(doc, "final_confirmation_date", _coerce_date(row_vals.get("final_confirmation_date")))
    _set_if_value(doc, "valid_upto", _coerce_date(row_vals.get("valid_upto")))
    _set_if_value(doc, "custom_residence_end_date", _coerce_date(row_vals.get("custom_residence_end_date")))

    # Custom/simple fields
    _set_if_value(doc, "custom_residence_number", _clean_val(row_vals.get("custom_residence_number")))
    _set_if_value(doc, "iban", _clean_val(row_vals.get("iban")))
    _set_if_value(doc, "bank_name", _clean_val(row_vals.get("bank_name")))

    # If there is bank evidence, set Salary Mode = Bank (if field exists)
    if (doc.get("iban") or doc.get("bank_name")) and "salary_mode" in doc.as_dict():
        _set_if_value(doc, "salary_mode", "Bank")

    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)

    return doc.name


# ---------------------------
# Entry point: import from Excel
# ---------------------------

@frappe.whitelist()
def import_employees_from_excel(
    path: str,
    default_company: Optional[str] = None,
    naming_series: Optional[str] = None,
    commit_every: int = 50,
    dry_run: int = 1,
    sheet: Optional[str] = None,
    upsert_fallback_key: Optional[str] = None,
):
    """
    Import/Upsert Employees from an Excel file.

    Args:
        path: absolute path to the Excel file on the server.
        default_company: set Employee.company if provided.
        naming_series: e.g. "HR-EMP-.#####"
        commit_every: commit every N rows (when dry_run=0).
        dry_run: 1=do not write DB, only log actions. 0=write changes.
        sheet: sheet name or index (if omitted, first sheet).
        upsert_fallback_key: prefer this field as the upsert key
                             (if empty on a row, falls back to cell_number, then employee_name).
    """
    print(f"[info] Import Employees from: {path}")
    print(f"[info] dry_run={'True' if dry_run else 'False'} commit_every={commit_every} fallback_key={upsert_fallback_key or '(auto)'}")

    # Read Excel
    try:
        df = pd.read_excel(path, sheet_name=sheet) if sheet else pd.read_excel(path)
    except Exception as e:
        frappe.throw(f"Failed to read Excel: {e}")

    # Normalize headers
    orig_cols = list(df.columns)
    norm_cols = [_normalize_header(c) for c in orig_cols]
    col_renames = dict(zip(orig_cols, norm_cols))
    df.rename(columns=col_renames, inplace=True)
    print(f"[info] Columns (normalized): {list(df.columns)}")

    # Build column -> ERP field mapping (only those present in the sheet)
    col_to_field: Dict[str, str] = {}
    for col in df.columns:
        if col in FIELD_MAP:
            col_to_field[col] = FIELD_MAP[col]
    print(f"[info] Mapped columns -> Employee fields: {col_to_field}")

    # Require employee_name
    required_col = None
    for k, v in col_to_field.items():
        if v == "employee_name":
            required_col = k
            break
    if not required_col:
        frappe.throw("No 'employee_name' column detected after normalization. Aborting.")

    created = 0
    updated = 0
    processed = 0

    for idx, row in df.iterrows():
        # Build sanitized row_vals (ERP field -> value)
        row_vals: Dict[str, Any] = {}
        for col_norm, target_field in col_to_field.items():
            row_vals[target_field] = _clean_val(row.get(col_norm))

        # Mandatory: employee_name
        emp_name = _clean_val(row_vals.get("employee_name"))
        if not emp_name:
            print(f"[warn] Row {idx+1}: missing required ['employee_name']. Skipped.")
            continue

        # Per-row upsert key
        # 1) explicit upsert_fallback_key if provided and value present
        # 2) company_email
        # 3) cell_number
        # 4) employee_name
        per_row_upsert_key = None
        if upsert_fallback_key and _clean_val(row_vals.get(upsert_fallback_key)):
            per_row_upsert_key = upsert_fallback_key
        elif _clean_val(row_vals.get("company_email")):
            per_row_upsert_key = "company_email"
        elif _clean_val(row_vals.get("cell_number")):
            per_row_upsert_key = "cell_number"
        else:
            per_row_upsert_key = "employee_name"

        try:
            if int(dry_run):
                exists = _get_existing_employee(per_row_upsert_key, row_vals)
                if exists:
                    print(f"  ↺ (dry-run) Would UPDATE Employee: {exists} ({emp_name})")
                    updated += 1
                else:
                    print(f"  ➕ (dry-run) Would CREATE Employee: {emp_name}")
                    created += 1
            else:
                name_before = _get_existing_employee(per_row_upsert_key, row_vals)
                _upsert_employee(row_vals, default_company, naming_series, per_row_upsert_key)
                if name_before:
                    updated += 1
                else:
                    created += 1

                if commit_every and ((created + updated) % int(commit_every) == 0):
                    frappe.db.commit()
                    print(f"[info] ✔ committed batch ({created+updated} so far)")

            processed += 1

        except Exception as e:
            print(f"[error] Row {idx+1} failed ({emp_name}): {e}")

    if not int(dry_run):
        frappe.db.commit()
    print(f"[info] Done. processed={processed}, created={created}, updated={updated}")


# ---------------------------
# Backfill Salary Mode
# ---------------------------

@frappe.whitelist()
def backfill_salary_mode_from_bank():
    """
    Set Employee.salary_mode='Bank' where IBAN or Bank Name exists.
    """
    # Ensure salary_mode field exists on Employee
    if "salary_mode" not in frappe.get_meta("Employee").get_fieldnames():
        print("[warn] Employee has no field 'salary_mode'. Skipping backfill.")
        return

    rows = frappe.db.sql(
        """
        SELECT name, iban, bank_name, salary_mode
        FROM `tabEmployee`
        WHERE (coalesce(iban, '') <> '' OR coalesce(bank_name, '') <> '')
        """,
        as_dict=True,
    )
    changed = 0
    for r in rows:
        if r.get("salary_mode") != "Bank":
            frappe.db.set_value("Employee", r["name"], "salary_mode", "Bank", update_modified=False)
            changed += 1
    frappe.db.commit()
    print(f"[info] Salary Mode backfilled to 'Bank' on {changed} employee(s).")
