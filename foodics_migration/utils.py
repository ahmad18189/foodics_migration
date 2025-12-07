import json
import time
from dataclasses import dataclass
from typing import Dict, Generator, Optional
from urllib.parse import urlparse

import frappe
import requests


@dataclass(frozen=True)
class FoodicsConfig:
    # required
    foodics_base_url: str
    foodics_token: str
    foodics_per_page: int
    foodics_currency: str
    foodics_price_list: str

    # optional / knobs (you said you added all of them)
    request_timeout_seconds: int = 60
    max_retries: int = 5
    retry_backoff_max_seconds: int = 30
    default_uom: str = "Nos"
    default_warehouse: Optional[str] = None
    commit_every_n_records: int = 1
    respect_foodics_deleted: int = 1  # 1->True
    log_level: str = "INFO"


def _validate_base_url(url: str):
    # PER YOUR DECISION: do not auto-normalize. Fail if scheme is missing or invalid.
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        frappe.throw(
            "Foodics Settings: `foodics_base_url` must include a scheme and host "
            "(e.g. `https://api.foodics.com/v5`)."
        )


def _assert_price_list_exists(name: str):
    if not frappe.db.exists("Price List", name):
        frappe.throw(f"Price List '{name}' not found. Create it and try again.")


def load_foodics_settings() -> FoodicsConfig:
    s = frappe.get_single("Foodics Settings")

    # Required field checks
    if not s.foodics_base_url:
        frappe.throw("Foodics Settings: `foodics_base_url` is required.")
    _validate_base_url(s.foodics_base_url)

    if not s.foodics_token:
        frappe.throw("Foodics Settings: `foodics_token` is required.")

    if not s.foodics_currency:
        frappe.throw("Foodics Settings: `foodics_currency` is required.")

    if not s.foodics_price_list:
        frappe.throw("Foodics Settings: `foodics_price_list` is required.")
    _assert_price_list_exists(s.foodics_price_list)

    # per_page bounds
    try:
        per_page = int(s.foodics_per_page or 100)
    except Exception:
        frappe.throw("Foodics Settings: `foodics_per_page` must be an integer.")
    if not (1 <= per_page <= 100):
        frappe.throw("Foodics Settings: `foodics_per_page` must be between 1 and 100.")

    # Optional knobs with sane defaults
    cfg = FoodicsConfig(
        foodics_base_url=str(s.foodics_base_url).rstrip("/"),
        foodics_token=s.foodics_token,
        foodics_per_page=per_page,
        foodics_currency=s.foodics_currency,
        foodics_price_list=s.foodics_price_list,
        request_timeout_seconds=int(s.request_timeout_seconds or 60),
        max_retries=int(s.max_retries or 5),
        retry_backoff_max_seconds=int(s.retry_backoff_max_seconds or 30),
        default_uom=s.default_uom or "Nos",
        default_warehouse=(s.default_warehouse or None),
        commit_every_n_records=max(1, int(s.commit_every_n_records or 1)),
        respect_foodics_deleted=int(s.respect_foodics_deleted or 1),
        log_level=(s.log_level or "INFO").upper(),
    )
    return cfg


def make_foodics_session(cfg: FoodicsConfig) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {cfg.foodics_token}",
        "Accept": "application/json",
    })
    return s


def paginated_get(
    cfg: FoodicsConfig,
    session: requests.Session,
    endpoint: str,
    params: Optional[Dict] = None,
) -> Generator[dict, None, None]:
    """
    Generator over Foodics pages. Yields items in 'data' array.
    Respects cfg.max_retries and retry_backoff_max_seconds.
    """
    page = 1
    next_url = None
    total = 0
    logger = frappe.logger()
    logger.setLevel(cfg.log_level)

    while True:
        if next_url:
            url = next_url
            req_params = None
        else:
            url = f"{cfg.foodics_base_url}/{endpoint.lstrip('/')}"
            req_params = {"page": page, "per_page": cfg.foodics_per_page}
            if params:
                req_params.update(params)

        # retry loop
        last_exc = None
        for attempt in range(cfg.max_retries + 1):
            try:
                resp = session.get(url, params=req_params, timeout=cfg.request_timeout_seconds)
                if resp.status_code in (429, 502, 503, 504):
                    raise requests.HTTPError(f"Retryable HTTP {resp.status_code}: {resp.text}", response=resp)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                last_exc = e
                if attempt >= cfg.max_retries:
                    raise
                sleep_for = min(2 ** attempt, cfg.retry_backoff_max_seconds)
                logger.warning(f"Foodics GET retry {attempt+1}/{cfg.max_retries} after {sleep_for}s: {e}")
                time.sleep(sleep_for)

        records = data.get("data", [])
        for r in records:
            yield r
        total += len(records)

        links = data.get("links") or {}
        next_url = links.get("next")
        if not next_url:
            break
        page += 1

    logger.info(f"✅ Pulled {total} records from Foodics endpoint '{endpoint}'.")

# --- append to the bottom of utils.py ---

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def ensure_custom_fields_for_foodics():
    """
    Idempotently ensure required custom fields exist on Customer & Address.
    Creates a 'Foodics' section on each doctype if missing.
    """
    # Build specs
    fields = {
        "Customer": [
            dict(fieldname="foodics_section", fieldtype="Section Break", label="Foodics", insert_after="__last"),
            dict(fieldname="foodics_customer_id", fieldtype="Data", label="Foodics Customer ID", unique=0, in_standard_filter=1),
            dict(fieldname="foodics_customer_data", fieldtype="Long Text", label="Foodics Raw JSON"),
            dict(fieldname="foodics_source", fieldtype="Data", label="Source", default="Foodics", read_only=1),
            dict(fieldname="foodics_last_sync_at", fieldtype="Datetime", label="Last Foodics Sync", read_only=1),
        ],
        "Address": [
            dict(fieldname="foodics_section", fieldtype="Section Break", label="Foodics", insert_after="__last"),
            dict(fieldname="foodics_customer_address_id", fieldtype="Data", label="Foodics Customer Address ID", in_standard_filter=1),
            dict(fieldname="foodics_delivery_zone", fieldtype="Data", label="Foodics Delivery Zone"),
            dict(fieldname="foodics_address_data", fieldtype="Long Text", label="Foodics Raw JSON"),
            dict(fieldname="foodics_source", fieldtype="Data", label="Source", default="Foodics", read_only=1),
            dict(fieldname="foodics_last_sync_at", fieldtype="Datetime", label="Last Foodics Sync", read_only=1),
        ],
    }

    payload = {}
    for doctype, flds in fields.items():
        payload[doctype] = []
        meta = frappe.get_meta(doctype)
        # we try to place under a 'Foodics' section; if not present we'll create the break first
        for f in flds:
            if meta.has_field(f["fieldname"]):
                continue
            # make sure we always put new fields toward the end to avoid disturbing layouts
            spec = {
                "fieldname": f["fieldname"],
                "fieldtype": f["fieldtype"],
                "label": f.get("label"),
                "insert_after": f.get("insert_after") or "last_name" if doctype == "Customer" else "county",
                "read_only": f.get("read_only") or 0,
                "default": f.get("default"),
                "in_standard_filter": f.get("in_standard_filter") or 0,
                "unique": f.get("unique") or 0,
            }
            payload[doctype].append(spec)

    if any(payload.values()):
        create_custom_fields(payload, ignore_validate=True)
        # Reload doctypes so new fields are available immediately
        frappe.clear_cache()
        try:
            frappe.reload_doc("contacts", "doctype", "address")
        except Exception:
            pass
        try:
            frappe.reload_doc("selling", "doctype", "customer")
        except Exception:
            pass

        frappe.clear_cache()


def assert_settings_for_parties(cfg: FoodicsConfig):
    """
    Validate settings required for Customer/Address sync.
    - customer_naming_series presence (we don't try to validate series table; let ERPNext throw if unknown)
    - default_customer_group exists
    - default_territory exists
    """
    s = frappe.get_single("Foodics Settings")
    if not getattr(s, "customer_naming_series", None):
        frappe.throw("Foodics Settings: `customer_naming_series` is required for Customer creation.")

    grp = getattr(s, "default_customer_group", None)
    if not grp or not frappe.db.exists("Customer Group", grp):
        frappe.throw(f"Foodics Settings: default_customer_group '{grp}' is missing; please create it.")

    terr = getattr(s, "default_territory", None)
    if not terr or not frappe.db.exists("Territory", terr):
        frappe.throw(f"Foodics Settings: default_territory '{terr}' is missing; please create it.")
