import json
from typing import Optional

import frappe

from .utils import (
    FoodicsConfig,
    load_foodics_settings,
    make_foodics_session,
    paginated_get,
)


def _resolve_item_group_by_foodics_category_id(foodics_category_id: Optional[str]) -> str:
    if not foodics_category_id:
        return "All Item Groups"
    ig = frappe.db.get_value(
        "Item Group",
        filters={"foodics_category_id": foodics_category_id},
        fieldname="name",
    )
    return ig or "All Item Groups"


def _get_item_by_foodics_id(fid: str):
    return frappe.db.get_value(
        "Item",
        filters={"foodics_product_id": fid},
        fieldname=["name", "item_code"],
        as_dict=True,
    )


def _get_item_by_item_code(item_code: str):
    return frappe.db.get_value(
        "Item",
        filters={"item_code": item_code},
        fieldname=["name"],
        as_dict=True,
    )


def _upsert_item_price(cfg: FoodicsConfig, item_code: str, rate: float):
    existing_name = frappe.db.get_value(
        "Item Price",
        filters={"item_code": item_code, "price_list": cfg.foodics_price_list},
        fieldname="name",
    )
    doc = {
        "doctype": "Item Price",
        "price_list": cfg.foodics_price_list,
        "item_code": item_code,
        "currency": cfg.foodics_currency,
        "price_list_rate": float(rate or 0.0),
    }
    if existing_name:
        ip = frappe.get_doc("Item Price", existing_name)
        ip.update(doc)
        ip.save(ignore_permissions=True)
        frappe.logger().info(f"↻ Updated Item Price: {item_code} @ {rate}")
    else:
        frappe.get_doc(doc).insert(ignore_permissions=True)
        frappe.logger().info(f"＋ Created Item Price: {item_code} @ {rate}")

def _replace_item_price(cfg: FoodicsConfig, item_code: str, gross_price: float):
    # Calculate net (VAT exclusive)
    vat_rate = getattr(cfg, "vat_rate", 0.15) or 0.0
    net_price = round(float(gross_price or 0.0) / (1 + vat_rate), 2)

    # Delete existing Item Prices for this item+pricelist
    names = frappe.get_all(
        "Item Price",
        filters={"item_code": item_code, "price_list": cfg.foodics_price_list},
        pluck="name",
    )
    for n in names:
        frappe.delete_doc("Item Price", n, ignore_permissions=True)

    # Create fresh Item Price
    doc = frappe.get_doc({
        "doctype": "Item Price",
        "price_list": cfg.foodics_price_list,
        "item_code": item_code,
        "currency": cfg.foodics_currency,
        "price_list_rate": net_price,
    })
    doc.insert(ignore_permissions=True)

    frappe.logger().info(f"↻ Replaced Item Price: {item_code} gross={gross_price} → net={net_price}")


def _disabled_flag(cfg: FoodicsConfig, product: dict) -> int:
    # If respect_foodics_deleted=1, disable when deleted_at present OR is_active == False
    if not cfg.respect_foodics_deleted:
        return 0
    if product.get("deleted_at"):
        return 1
    if product.get("is_active") is False:
        return 1
    return 0


def _upsert_item(cfg: FoodicsConfig, product: dict):
    fid = product.get("id")
    if not fid:
        return

    sku = (product.get("sku") or "").strip() or fid
    name = product.get("name") or sku
    maintain_stock = 1 if product.get("is_stock_product") else 0
    cost = float(product.get("cost") or 0.0)
    price = float(product.get("price") or 0.0)
    category = product.get("category") or {}
    item_group = _resolve_item_group_by_foodics_category_id(category.get("id"))
    disabled = _disabled_flag(cfg, product)

    payload = {
        "doctype": "Item",
        "item_code": sku,
        "item_name": name,
        "item_group": item_group,
        "stock_uom": cfg.default_uom,
        "is_stock_item": maintain_stock,
        "disabled": disabled,
        "valuation_rate": cost,
        "standard_rate": cost,
        # custom fields
        "foodics_product_id": fid,
        "foodics_product_data": json.dumps(product, ensure_ascii=False),
    }

    existing = _get_item_by_foodics_id(fid)
    if existing:
        item = frappe.get_doc("Item", existing["name"])
        item.update(payload)
        item.save(ignore_permissions=True)
        frappe.logger().info(f"↻ Updated Item: {sku} (Foodics {fid})")
        item_code = item.item_code
    else:
        by_code = _get_item_by_item_code(sku)
        if by_code:
            item = frappe.get_doc("Item", by_code["name"])
            item.update(payload)
            item.save(ignore_permissions=True)
            frappe.logger().info(f"↻ Updated Item (by item_code): {sku}")
            item_code = item.item_code
        else:
            item = frappe.get_doc(payload).insert(ignore_permissions=True)
            frappe.logger().info(f"＋ Created Item: {sku}")
            item_code = item.item_code

    #_upsert_item_price(cfg, item_code, price)
    _replace_item_price(cfg, item_code, price)


@frappe.whitelist()
def sync_foodics_products():
    """
    Pull Foodics products (with category) and upsert ERPNext Items & Item Prices.

    Run via:
      bench --site <yoursite> execute foodics_migration.api.sync_foodics_products
    """
    frappe.only_for(("System Manager", "Administrator"))

    cfg = load_foodics_settings()
    logger = frappe.logger()
    logger.setLevel(cfg.log_level)

    session = make_foodics_session(cfg)

    count = 0
    try:
        for prod in paginated_get(
            cfg,
            session,
            endpoint="products",
            params={"include": "category"},
        ):
            try:
                _upsert_item(cfg, prod)
                count += 1
                # commit batching
                if count % cfg.commit_every_n_records == 0:
                    frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.log_error(
                    title="Foodics Product Sync Error",
                    message=frappe.get_traceback(),
                )
                # continue with next record
        # final commit if remainder
        frappe.db.commit()
        logger.info(f"✅ Finished Foodics products sync. Processed: {count}")
    except Exception:
        frappe.db.rollback()
        raise

# --- append to api.py ---

from frappe.utils import now_datetime

from .utils import (
    FoodicsConfig,
    load_foodics_settings,
    make_foodics_session,
    paginated_get,
    ensure_custom_fields_for_foodics,
    assert_settings_for_parties,
)

# ---------------------------
# Helpers (lookups & builders)
# ---------------------------

def _get_customer_by_foodics_id(fid: str):
    return frappe.db.get_value(
        "Customer",
        filters={"foodics_customer_id": fid},
        fieldname=["name"],
        as_dict=True,
    )

def _get_address_by_foodics_id(aid: str):
    return frappe.db.get_value(
        "Address",
        filters={"foodics_customer_address_id": aid},
        fieldname=["name"],
        as_dict=True,
    )

def _safe_str(*vals) -> str:
    for v in vals:
        if v:
            v = str(v).strip()
            if v:
                return v
    return ""

def _extract_customer_name(c: dict) -> str:
    # Prefer explicit name; otherwise fallback to id
    return _safe_str(c.get("name"), c.get("first_name"), c.get("last_name"), c.get("id"))

def _extract_address_title(addr: dict, cust_name: str) -> str:
    return _safe_str(addr.get("name"), addr.get("label"), addr.get("nickname"), f"{cust_name} Address")

def _extract_address_line1(addr: dict) -> str:
    # NO PII policy: we still need a line to save Address. Prefer generic text if specifics are missing.
    return _safe_str(
        addr.get("address"),
        addr.get("line1"),
        addr.get("street"),
        addr.get("description"),
        "Address from Foodics",
    )

def _extract_city_country(addr: dict, default_city: str, default_country: str):
    city = _safe_str(addr.get("city"), default_city)
    country = _safe_str(addr.get("country"), default_country)
    return city, country

def _extract_delivery_zone(addr: dict) -> str:
    dz = addr.get("delivery_zone") or {}
    if isinstance(dz, dict):
        return _safe_str(dz.get("name"), dz.get("id"))
    return _safe_str(dz)

# ---------------------------
# Upsert functions
# ---------------------------

def _upsert_customer(cfg: FoodicsConfig, s, cust: dict) -> str:
    """
    Returns ERPNext Customer name.
    - 1:1 strict mapping
    - Always enabled
    - No PII (skip phone/email)
    """
    fid = cust.get("id")
    if not fid:
        return ""

    now = now_datetime()
    doc_fields = {
        "doctype": "Customer",
        "customer_name": _extract_customer_name(cust),
        "customer_group": s.default_customer_group,
        "territory": s.default_territory,
        "naming_series": s.customer_naming_series,
        "disabled": 0,
        # custom fields
        "foodics_customer_id": fid,
        "foodics_customer_data": frappe.as_json(cust, ensure_ascii=False),
        "foodics_source": "Foodics",
        "foodics_last_sync_at": now,
    }

    existing = _get_customer_by_foodics_id(fid)
    if existing:
        doc = frappe.get_doc("Customer", existing["name"])
        doc.update(doc_fields)
        doc.save(ignore_permissions=True)
        frappe.logger().info(f"↻ Updated Customer: {doc.name} (Foodics {fid})")
        return doc.name
    else:
        doc = frappe.get_doc(doc_fields).insert(ignore_permissions=True)
        frappe.logger().info(f"＋ Created Customer: {doc.name} (Foodics {fid})")
        return doc.name

def _upsert_address(cfg: FoodicsConfig, s, addr: dict, customer_name: str) -> str:
    """
    1:1 strict mapping for Address.
    - No phone, no coordinates
    - Keep a raw JSON snapshot
    - Link to Customer via Address Link child table
    """
    aid = addr.get("id")
    if not aid:
        return ""

    city, country = _extract_city_country(addr, s.address_city_default or "Al Riyadh", s.address_country_default or "Saudi Arabia")
    title = _extract_address_title(addr, customer_name)
    line1 = _extract_address_line1(addr)
    dz = _extract_delivery_zone(addr)
    now = now_datetime()

    base_fields = {
        "doctype": "Address",
        "address_title": title,
        "address_line1": line1,
        "city": city,
        "country": country,
        "address_type": "Billing",  # neutral; orders may add Shipping later
        # custom
        "foodics_customer_address_id": aid,
        "foodics_delivery_zone": dz,
        "foodics_address_data": frappe.as_json(addr, ensure_ascii=False),
        "foodics_source": "Foodics",
        "foodics_last_sync_at": now,
        # link to Customer
        "links": [
            {
                "link_doctype": "Customer",
                "link_name": customer_name,
            }
        ],
    }

    existing = _get_address_by_foodics_id(aid)
    if existing:
        adoc = frappe.get_doc("Address", existing["name"])
        adoc.update(base_fields)
        # ensure link present (avoid duplicates)
        if not any(l.link_doctype == "Customer" and l.link_name == customer_name for l in adoc.links or []):
            adoc.append("links", {"link_doctype": "Customer", "link_name": customer_name})
        adoc.save(ignore_permissions=True)
        frappe.logger().info(f"↻ Updated Address: {adoc.name} (Foodics {aid})")
        return adoc.name
    else:
        adoc = frappe.get_doc(base_fields).insert(ignore_permissions=True)
        frappe.logger().info(f"＋ Created Address: {adoc.name} (Foodics {aid})")
        return adoc.name

# ---------------------------
# Public sync entry points
# ---------------------------

@frappe.whitelist()
def sync_foodics_customers():
    """
    Sync Foodics Customers (1:1, no PII).
    Run via:
      bench --site <yoursite> execute foodics_migration.api.sync_foodics_customers
    """
    frappe.only_for(("System Manager", "Administrator"))

    # Ensure custom fields exist before we do anything else
    ensure_custom_fields_for_foodics()

    cfg = load_foodics_settings()
    logger = frappe.logger()
    logger.setLevel(cfg.log_level)

    # Validate party-related settings
    assert_settings_for_parties(cfg)
    s = frappe.get_single("Foodics Settings")

    session = make_foodics_session(cfg)
    count = 0

    try:
        for cust in paginated_get(cfg, session, endpoint="customers", params=None):
            try:
                _upsert_customer(cfg, s, cust)
                count += 1
                if count % cfg.commit_every_n_records == 0:
                    frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.log_error(title="Foodics Customer Sync Error", message=frappe.get_traceback())
        frappe.db.commit()
        logger.info(f"✅ Finished Foodics customer sync. Processed: {count}")
    except Exception:
        frappe.db.rollback()
        raise


@frappe.whitelist()
def sync_foodics_addresses():
    """
    Sync Foodics Customer Addresses (1:1, minimal, no PII).
    Expects customers to be synced first so we can link.
    Run via:
      bench --site <yoursite> execute foodics_migration.api.sync_foodics_addresses
    """
    frappe.only_for(("System Manager", "Administrator"))

    # Ensure custom fields exist before we do anything else
    ensure_custom_fields_for_foodics()

    cfg = load_foodics_settings()
    logger = frappe.logger()
    logger.setLevel(cfg.log_level)

    assert_settings_for_parties(cfg)
    s = frappe.get_single("Foodics Settings")

    session = make_foodics_session(cfg)
    count = 0

    # Foodics v5 commonly exposes customer addresses at 'customer_addresses'
    # If your tenant uses a different path, just change `endpoint` below.
    try:
        for addr in paginated_get(cfg, session, endpoint="customer_addresses", params=None):
            try:
                cust_id = _safe_str(addr.get("customer_id"))
                if not cust_id:
                    raise Exception("Address missing customer_id")
                cust = _get_customer_by_foodics_id(cust_id)
                if not cust:
                    # if Customer missing, we create it on the fly with minimal info
                    # or skip — here we choose to create minimally to preserve referential integrity
                    customer_name = _upsert_customer(cfg, s, {"id": cust_id, "name": cust_id})
                else:
                    customer_name = cust["name"]

                _upsert_address(cfg, s, addr, customer_name)
                count += 1
                if count % cfg.commit_every_n_records == 0:
                    frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.log_error(title="Foodics Address Sync Error", message=frappe.get_traceback())
        frappe.db.commit()
        logger.info(f"✅ Finished Foodics address sync. Processed: {count}")
    except Exception:
        frappe.db.rollback()
        raise


# foodics_migration/api.py

import json
import time
from urllib.parse import urlencode, urljoin

import frappe
import requests


# -----------------------------
# Settings & HTTP session
# -----------------------------

def _get_foodics_settings():
    cfg = frappe.get_single("Foodics Settings")
    missing = []
    for k in ("foodics_base_url", "foodics_token", "foodics_per_page"):
        if not getattr(cfg, k, None):
            missing.append(k)
    if missing:
        frappe.throw(f"Foodics Settings missing: {', '.join(missing)}")

    base = cfg.foodics_base_url.strip()
    # Per your decision: do NOT auto-add scheme; fail if missing
    if not (base.startswith("http://") or base.startswith("https://")):
        frappe.throw("Foodics base URL must include scheme (http/https). Example: https://api.foodics.com/v5")

    # ensure trailing slash handling works with urljoin
    if not base.endswith("/"):
        base = base + "/"

    per_page = int(cfg.foodics_per_page or 100)
    if per_page <= 0:
        per_page = 100

    return frappe._dict(
        base_url=base,
        token=cfg.foodics_token.strip(),
        per_page=per_page,
    )


def _build_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    })
    return s


# -----------------------------
# Schema guards (parent & child)
# -----------------------------

def _ensure_order_parent_schema(parent_dt="Foodics Order"):
    """Make sure the parent has needed fields (we won’t create the doctype; just ensure custom fields)."""
    parent_meta = frappe.get_meta(parent_dt)

    def ensure_field(fieldname, label, fieldtype, options=None):
        if not any(df.fieldname == fieldname for df in parent_meta.fields):
            print(f"⚠ Parent {parent_dt} missing `{fieldname}` → creating it.")
            frappe.get_doc({
                "doctype": "Custom Field",
                "dt": parent_dt,
                "fieldname": fieldname,
                "label": label,
                "fieldtype": fieldtype,
                "options": options,
                "insert_after": parent_meta.fields[-1].fieldname if parent_meta.fields else "naming_series",
            }).insert(ignore_permissions=True)
            frappe.clear_cache(doctype=parent_dt)

    # Minimal set used below
    ensure_field("foodics_order_id", "Foodics Order ID", "Data")
    ensure_field("number", "Number", "Int")
    ensure_field("check_number", "Check Number", "Int")
    ensure_field("status", "Status", "Int")
    ensure_field("delivery_status", "Delivery Status", "Int")
    ensure_field("guests", "Guests", "Int")
    ensure_field("business_date", "Business Date", "Date")
    ensure_field("opened_at", "Opened At", "Datetime")
    ensure_field("closed_at", "Closed At", "Datetime")
    ensure_field("subtotal_price_gross", "Subtotal Price (Gross)", "Currency")
    ensure_field("total_price_gross", "Total Price (Gross)", "Currency")
    ensure_field("total_price_net", "Total Price (Net)", "Currency")
    ensure_field("discount_amount", "Discount Amount", "Currency")
    ensure_field("rounding_amount", "Rounding Amount", "Currency")
    ensure_field("branch_name", "Branch Name", "Data")
    ensure_field("device_name", "Device Name", "Data")
    ensure_field("creator_name", "Creator Name", "Data")
    ensure_field("closer_name", "Closer Name", "Data")
    ensure_field("foodics_order_raw", "Foodics Order Raw", "Long Text")


def _ensure_order_items_schema(parent_dt="Foodics Order", child_dt="Foodics Order Item", child_fieldname="items"):
    # Ensure parent has a child table named `items`
    parent_meta = frappe.get_meta(parent_dt)
    if not any(df.fieldtype == "Table" and df.fieldname == child_fieldname for df in parent_meta.fields):
        print(f"⚠ Parent {parent_dt} missing child table field `{child_fieldname}` → creating it.")
        frappe.get_doc({
            "doctype": "Custom Field",
            "dt": parent_dt,
            "fieldname": child_fieldname,
            "label": "Items",
            "fieldtype": "Table",
            "options": child_dt,
            "insert_after": parent_meta.fields[-1].fieldname if parent_meta.fields else "naming_series",
        }).insert(ignore_permissions=True)
        frappe.clear_cache(doctype=parent_dt)

    # Ensure child fields
    child_meta = frappe.get_meta(child_dt)

    def ensure_child_field(fieldname, label, fieldtype, options=None):
        if not any(df.fieldname == fieldname for df in child_meta.fields):
            print(f"⚠ Child {child_dt} missing `{fieldname}` → creating it.")
            frappe.get_doc({
                "doctype": "Custom Field",
                "dt": child_dt,
                "fieldname": fieldname,
                "label": label,
                "fieldtype": fieldtype,
                "options": options,
                "insert_after": child_meta.fields[-1].fieldname if child_meta.fields else "idx",
            }).insert(ignore_permissions=True)
            frappe.clear_cache(doctype=child_dt)

    ensure_child_field("foodics_product_id", "Foodics Product ID", "Data")
    ensure_child_field("product_sku", "SKU", "Data")
    ensure_child_field("product_name", "Product Name", "Data")
    ensure_child_field("category_id", "Category ID", "Data")
    ensure_child_field("category_name", "Category Name", "Data")
    ensure_child_field("qty", "Qty", "Float")
    ensure_child_field("unit_price_gross", "Unit Price (Gross)", "Currency")
    ensure_child_field("total_price_gross", "Total Price (Gross)", "Currency")
    ensure_child_field("unit_price_net", "Unit Price (Net)", "Currency")
    ensure_child_field("total_price_net", "Total Price (Net)", "Currency")
    ensure_child_field("line_status", "Line Status", "Int")
    ensure_child_field("kitchen_notes", "Kitchen Notes", "Small Text")
    ensure_child_field("foodics_item_raw", "Foodics Item Raw", "Long Text")


# -----------------------------
# Mappers
# -----------------------------

def _map_parent_fields(doc, order_obj, raw_field="foodics_order_raw"):
    # Basic fields
    doc.foodics_order_id = order_obj.get("id")
    doc.number = order_obj.get("number")
    doc.check_number = order_obj.get("check_number")
    doc.status = order_obj.get("status")
    doc.delivery_status = order_obj.get("delivery_status")
    doc.guests = order_obj.get("guests")

    doc.business_date = order_obj.get("business_date")
    doc.opened_at = order_obj.get("opened_at")
    doc.closed_at = order_obj.get("closed_at")

    # Prices
    doc.subtotal_price_gross = order_obj.get("subtotal_price") or 0
    doc.total_price_gross = order_obj.get("total_price") or 0

    # total_price_net: sum of lines net if present; otherwise 0
    total_net = 0.0
    for line in (order_obj.get("products") or []):
        total_net += float(line.get("tax_exclusive_total_price") or 0)
    doc.total_price_net = total_net

    doc.discount_amount = order_obj.get("discount_amount") or 0
    doc.rounding_amount = order_obj.get("rounding_amount") or 0

    # Lightweight branch/device/creator/closer names (for filterability)
    branch = order_obj.get("branch") or {}
    doc.branch_name = branch.get("name")

    device = order_obj.get("device") or {}
    doc.device_name = device.get("name")

    creator = order_obj.get("creator") or {}
    doc.creator_name = creator.get("name")

    closer = order_obj.get("closer") or {}
    doc.closer_name = closer.get("name")

    # Raw JSON
    try:
        setattr(doc, raw_field, json.dumps(order_obj, ensure_ascii=False))
    except Exception:
        setattr(doc, raw_field, frappe.as_json(order_obj))


def _map_child_items(doc, order_products, child_fieldname="items"):
    if not order_products:
        print("   • No products on this order; items cleared.")
        doc.set(child_fieldname, [])
        return

    doc.set(child_fieldname, [])
    added = 0
    for line in order_products:
        p = (line or {}).get("product") or {}
        cat = p.get("category") or {}

        ch = doc.append(child_fieldname, {})
        ch.foodics_product_id = p.get("id")
        ch.product_sku = p.get("sku")
        ch.product_name = p.get("name")
        ch.category_id = cat.get("id")
        ch.category_name = cat.get("name")

        ch.qty = line.get("quantity") or 0
        ch.unit_price_gross = line.get("unit_price") or 0
        ch.total_price_gross = line.get("total_price") or 0
        ch.unit_price_net = line.get("tax_exclusive_unit_price") or 0
        ch.total_price_net = line.get("tax_exclusive_total_price") or 0

        ch.line_status = line.get("status") or 0
        ch.kitchen_notes = line.get("kitchen_notes")

        try:
            ch.foodics_item_raw = json.dumps(line, ensure_ascii=False)
        except Exception:
            ch.foodics_item_raw = frappe.as_json(line)

        added += 1

    print(f"   • Added {added} item rows.")


# -----------------------------
# Pagination fetch
# -----------------------------

INCLUDE_QUERY = (
    "products,products.product,products.product.category,"
    "branch,device,creator,closer"
)

def _initial_orders_url(base_url, per_page):
    endpoint = urljoin(base_url, "orders")
    query = {
        "include": INCLUDE_QUERY,
        "sort": "id",
        "per_page": per_page,
        "page": 1,
    }
    return f"{endpoint}?{urlencode(query)}"


def _fetch_page(session: requests.Session, url: str, attempt=1):
    resp = session.get(url, timeout=60)
    if resp.status_code == 429:
        # small backoff, then let caller decide to retry by calling again
        wait = min(2 ** attempt, 30)
        print(f"   • 429 rate limited. Sleeping {wait}s then retrying…")
        time.sleep(wait)
        raise requests.HTTPError("429", response=resp)
    resp.raise_for_status()
    return resp.json()


# -----------------------------
# Upsert one order
# -----------------------------

def _upsert_order(order_obj, child_fieldname="items"):
    order_id = order_obj.get("id")
    if not order_id:
        print("   • Skipping an order without id.")
        return

    # find existing by foodics_order_id
    existing_name = frappe.db.get_value(
        "Foodics Order",
        {"foodics_order_id": order_id},
        "name",
    )

    if existing_name:
        doc = frappe.get_doc("Foodics Order", existing_name)
        _map_parent_fields(doc, order_obj, raw_field="foodics_order_raw")
        _map_child_items(doc, order_obj.get("products") or [], child_fieldname=child_fieldname)
        doc.save(ignore_permissions=True)
        print(f"   • Updated order {order_id} → {doc.name}")
    else:
        doc = frappe.new_doc("Foodics Order")
        _map_parent_fields(doc, order_obj, raw_field="foodics_order_raw")
        _map_child_items(doc, order_obj.get("products") or [], child_fieldname=child_fieldname)
        doc.insert(ignore_permissions=True)
        print(f"   • Inserted order {order_id} → {doc.name}")


# -----------------------------
# PUBLIC: sync all orders
# -----------------------------

@frappe.whitelist()
def sync_foodics_orders(commit_every: int | None = None):
    """
    bench --site <yoursite> execute foodics_migration.api.sync_foodics_orders
    bench --site <yoursite> execute foodics_migration.api.sync_foodics_orders --kwargs "{'commit_every': 5}"
    """
    cfg = _get_foodics_settings()
    session = _build_session(cfg.token)

    # Schema guards
    _ensure_order_parent_schema(parent_dt="Foodics Order")
    _ensure_order_items_schema(parent_dt="Foodics Order", child_dt="Foodics Order Item", child_fieldname="items")

    commit_every = int(commit_every or 20)
    page_url = _initial_orders_url(cfg.base_url, cfg.per_page)

    total_inserted = 0
    total_updated = 0
    total_processed = 0
    batch_count = 0

    print("=== Foodics Orders Sync ===")
    print(f"Base URL  : {cfg.base_url}")
    print(f"Per Page  : {cfg.per_page}")
    print(f"Commit N  : {commit_every}")
    print(f"Includes  : {INCLUDE_QUERY}")
    print("---------------------------")

    while page_url:
        print(f"\n→ GET {page_url}")
        attempt = 1
        while True:
            try:
                data = _fetch_page(session, page_url, attempt=attempt)
                break
            except requests.HTTPError as e:
                if str(e) == "429" and attempt <= 5:
                    attempt += 1
                    continue
                # other HTTP errors or too many retries
                msg = f"HTTP error while fetching: {page_url} -> {getattr(e.response, 'text', e)}"
                print(msg)
                frappe.throw(f"Fetch failed: {msg}")

        orders = data.get("data") or []
        links = data.get("links") or {}

        print(f"• Received {len(orders)} orders")

        for order in orders:
            before_exists = frappe.db.exists("Foodics Order", {"foodics_order_id": order.get("id")})
            _upsert_order(order, child_fieldname="items")

            total_processed += 1
            batch_count += 1
            if before_exists:
                total_updated += 1
            else:
                total_inserted += 1

            # per-order commit, if you want literally each order:
            # frappe.db.commit()

            # commit every N
            if batch_count >= commit_every:
                frappe.db.commit()
                print(f"✓ Committed batch of {batch_count}. Totals → processed:{total_processed} inserted:{total_inserted} updated:{total_updated}")
                batch_count = 0

        # next page via links.next
        page_url = links.get("next")

    # final commit if some remain
    if batch_count > 0:
        frappe.db.commit()
        print(f"✓ Final commit of {batch_count} pending orders.")

    print("\n=== Done ===")
    print(f"Processed: {total_processed} | Inserted: {total_inserted} | Updated: {total_updated}")

DEFAULT_PER_PAGE = 100

def _cfg():
	doc = frappe.get_single("Foodics Settings")
	if not doc.foodics_base_url:
		frappe.throw("Foodics Settings: `foodics_base_url` is required.")
	if not doc.foodics_token:
		frappe.throw("Foodics Settings: `foodics_token` is required.")
	return frappe._dict(
		base_url=doc.foodics_base_url.strip(),
		token=doc.foodics_token.strip(),
		per_page=int(doc.foodics_per_page or DEFAULT_PER_PAGE),
		foodics_price_list=getattr(doc, "foodics_price_list", None),
		foodics_currency=getattr(doc, "foodics_currency", None),
		vat_rate=getattr(doc, "vat_rate", 0.15),
	)
 
 # ---------- COMBOS (pull + normalize + upsert as Item; price saved AS-IS) ----------

COMBOS_INCLUDE = (
	"category,tags,timed_events,branches,sizes,items.options.products,"
	"items.options.products.category,items.options.branches,items.options.price_tags,groups"
)

def _resolve_pricing_defaults(cfg):
	price_list = None
	currency = None
	try:
		price_list = getattr(cfg, "foodics_price_list", None) or frappe.db.get_single_value("Selling Settings", "selling_price_list")
	except Exception:
		pass
	try:
		currency = getattr(cfg, "foodics_currency", None) or (frappe.db.get_default("currency") or "SAR")
	except Exception:
		currency = "SAR"
	if not price_list:
		price_list = "Standard Selling"
	return frappe._dict(price_list=price_list, currency=currency)

def _iter_foodics_combos(cfg, s):
	"""
	Iterator over Foodics combos with a robust include list; we rebuild page URLs so
	'include=' is present on every page (some backends drop params on links.next).
	"""
	base = _safe_base_url(cfg.base_url)
	page = 1
	per_page = cfg.per_page

	def page_url(p):
		return f"{base}/combos?include={COMBOS_INCLUDE}&per_page={per_page}&page={p}"

	def index_included(included):
		idx = {}
		for inc in included or []:
			itype = (inc.get("type") or "").strip()
			iid = inc.get("id")
			attrs = inc.get("attributes") or {}
			if not itype or not iid:
				continue
			obj = {"id": iid, "type": itype, "attributes": attrs}
			if "name" in attrs:
				obj["name"] = attrs.get("name")
			idx.setdefault(itype, {})[iid] = obj
		return idx

	def normalize_combo(c, idx):
		attrs = c.get("attributes") or {}
		out = dict(attrs)
		out["id"] = c.get("id") or attrs.get("id") or attrs.get("uuid")

		# category
		cat_rel = ((c.get("relationships") or {}).get("category") or {}).get("data") or {}
		cat_id = cat_rel.get("id")
		if cat_id and "categories" in idx and cat_id in idx["categories"]:
			out["category"] = {"id": cat_id, "name": idx["categories"][cat_id].get("name")}

		# groups (IDs only)
		grp_rel = ((c.get("relationships") or {}).get("groups") or {}).get("data") or []
		out["groups"] = [{"id": g.get("id")} for g in grp_rel if isinstance(g, dict)]

		# sizes
		sz_rel = ((c.get("relationships") or {}).get("sizes") or {}).get("data") or []
		out["sizes"] = []
		for r in sz_rel:
			sid = r.get("id")
			if sid and "sizes" in idx and sid in idx["sizes"]:
				out["sizes"].append(idx["sizes"][sid])

		# branches
		br_rel = ((c.get("relationships") or {}).get("branches") or {}).get("data") or []
		out["branches"] = []
		for r in br_rel:
			bid = r.get("id")
			if bid and "branches" in idx and bid in idx["branches"]:
				out["branches"].append(idx["branches"][bid])

		# timed events
		te_rel = ((c.get("relationships") or {}).get("timed_events") or {}).get("data") or []
		out["timed_events"] = []
		for r in te_rel:
			tid = r.get("id")
			if tid and "timed_events" in idx and tid in idx["timed_events"]:
				out["timed_events"].append(idx["timed_events"][tid])

		# tags
		tg_rel = ((c.get("relationships") or {}).get("tags") or {}).get("data") or []
		out["tags"] = []
		for r in tg_rel:
			tid = r.get("id")
			if tid and "tags" in idx and tid in idx["tags"]:
				out["tags"].append(idx["tags"][tid])

		# items/options/products: preserve embedded payload if present
		out["items"] = attrs.get("items") or []

		# top-level combo price (saved AS-IS later)
		out["price"] = attrs.get("price")

		return out

	print(f"[info] Fetching Foodics combos per_page={per_page}")
	while True:
		print(f"[info] === Combos Page {page} ===")
		data = _get_with_backoff(s, page_url(page))
		included = data.get("included") or []
		idx = index_included(included)
		rows = data.get("data") or []
		print(f"[debug] fetched {len(rows)} combos")

		if not rows:
			break

		for c in rows:
			nc = normalize_combo(c, idx)
			cname = (nc.get("category") or {}).get("name")
			print(f"[combo] {nc.get('id')} -> category={cname} price={nc.get('price')}")
			yield nc

		has_next = bool((data.get("links") or {}).get("next"))
		if not has_next:
			break
		page += 1

def _replace_item_price_as_is(cfg, item_code, price):
	"""
	Hard-replace the Item Price with the given value (AS-IS; WITHOUT tax math).
	Uses Foodics Settings price list / currency if present, else sensible fallbacks.
	"""
	if price is None:
		return
	defs = _resolve_pricing_defaults(cfg)

	# delete existing Item Price rows for this price_list
	names = frappe.get_all(
		"Item Price",
		filters={"item_code": item_code, "price_list": defs.price_list},
		pluck="name",
	)
	for n in names:
		frappe.delete_doc("Item Price", n, ignore_permissions=True, force=1)

	# create fresh Item Price
	doc = frappe.get_doc({
		"doctype": "Item Price",
		"item_code": item_code,
		"price_list": defs.price_list,
		"currency": defs.currency,
		"price_list_rate": float(price),
	})
	doc.insert(ignore_permissions=True)
	print(f"    · replaced Item Price {defs.price_list} = {price} {defs.currency}")

def _upsert_combo_as_item(cfg, combo):
	"""
	Create/Update ERPNext Item from a Foodics Combo.
	- Match on custom field 'foodics_combo_id' if present; else on item_code == combo.id.
	- Set Item Group from combo.category.name (auto-create when missing).
	- Set Item Name from combo.name (if provided).
	- Replace Item Price AS-IS from combo.price (no VAT math).
	- Store raw combo JSON in 'foodics_combo_data' if that custom field exists.
	"""
	cid = combo.get("id")
	if not cid:
		return

	# locate existing Item
	item_name = None
	if frappe.get_meta("Item").has_field("foodics_combo_id"):
		item_name = frappe.db.get_value("Item", {"foodics_combo_id": cid}, "name")
	if not item_name:
		item_name = frappe.db.get_value("Item", {"item_code": cid}, "name")

	if item_name:
		item_doc = frappe.get_doc("Item", item_name)
	else:
		item_doc = frappe.get_doc({
			"doctype": "Item",
			"item_code": cid,
			"item_name": combo.get("name") or f"Combo {cid}",
			"item_group": "All Item Groups",  # temp; corrected below
			"is_stock_item": 0,
			"include_item_in_manufacturing": 0,
			"maintain_stock": 0,
		})
		item_doc.insert(ignore_permissions=True)
		print(f"[create] Item for Combo {cid} -> {item_doc.name}")

	# refresh name
	if combo.get("name") and item_doc.item_name != combo.get("name"):
		item_doc.item_name = combo.get("name")

	# set Item Group from combo.category
	cat_name = (combo.get("category") or {}).get("name")
	ig = _ensure_item_group(cat_name or "Unassigned (Foodics)", parent="All Item Groups")
	if item_doc.item_group != ig:
		item_doc.item_group = ig
		print(f"    · set item_group = {ig}")

	# persist Foodics combo id if custom field exists
	if frappe.get_meta("Item").has_field("foodics_combo_id") and not item_doc.get("foodics_combo_id"):
		item_doc.set("foodics_combo_id", cid)

	# raw payload for traceability
	if frappe.get_meta("Item").has_field("foodics_combo_data"):
		item_doc.set("foodics_combo_data", json.dumps(combo, ensure_ascii=False))

	item_doc.save(ignore_permissions=True)

	# Replace Item Price with combo.price AS-IS (WITHOUT tax)
	_replace_item_price_as_is(cfg, item_doc.name, combo.get("price"))

@frappe.whitelist()
def sync_foodics_combos():
	"""
	Fetch Foodics combos (with deep includes) and upsert each as an ERPNext Item:
	- Item Group from combo category
	- Price set exactly as provided (no VAT math)
	- Raw combo JSON stored when corresponding custom field exists
	"""
	cfg = _cfg()
	s = _new_session(cfg.token)

	processed = 0
	upserted = 0

	try:
		for combo in _iter_foodics_combos(cfg, s):
			_upsert_combo_as_item(cfg, combo)
			processed += 1
			upserted += 1

		frappe.db.commit()
		print(f"[info] Done. combos_processed={processed}, upserted={upserted}")

	except Exception as e:
		frappe.db.rollback()
		print(f"[error] Combo sync aborted: {e}")
		raise
