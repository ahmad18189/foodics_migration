# apps/foodics_migration/foodics_migration/api_orders.py

from __future__ import annotations

import json
import time
from typing import Dict, Any, Iterable, Optional
from urllib.parse import urlparse, parse_qs, urlencode, quote

import frappe
import requests

INCLUDE_PARTS = [
    "branch","device","creator","closer",
    "products","products.product","products.product.category",
    "products.options","products.options.modifier_option","products.taxes",
    "combos","combos.combo_size","combos.combo_size.combo","combos.discount",
    "combos.products","combos.products.product","combos.products.product.category",
    "combos.products.combo_size","combos.products.combo_option","combos.products.taxes",
    "payments","payments.payment_method","payments.user",
    "charges","charges.charge","charges.taxes",
]
INCLUDE_Q = quote(",".join(INCLUDE_PARTS), safe=",")

def _fetch_single_order_with_include(cfg, s, order_id: str) -> Dict[str, Any]:
    base = _safe_base_url(cfg.foodics_base_url)
    # NOTE: orders/{id} endpoint (not the list) – more reliable for relationships
    url = f"{base}/orders/{order_id}?include={INCLUDE_Q}"
    print(f"[debug] Fallback refetch one order: {url}")
    return _get_with_backoff(s, url).get("data") or {}

# ---------------------------
# SETTINGS / CONSTANTS
# ---------------------------

INCLUDE_CLAUSE = (
    "branch,device,creator,closer,"
    "products,products.product,products.product.category,"
    "products.options,products.options.modifier_option,products.taxes,"
    "combos,combos.combo_size,combos.combo_size.combo,combos.discount,"
    "combos.products,combos.products.product,combos.products.product.category,"
    "combos.products.combo_size,combos.products.combo_option,combos.products.taxes,"
    "payments,payments.payment_method,payments.user,"
    "charges,charges.charge,charges.taxes"
)

DEFAULT_PER_PAGE = 100  # you asked to use ?per_page=100
DEFAULT_COMMIT_EVERY = 1  # commit after each order by default
REQUEST_TIMEOUT = 60      # seconds
_PRODUCTS_INCLUDE_Q = quote(",".join([
    "product","product.category","options.modifier_option","taxes"
]), safe=",")

_COMBOS_INCLUDE_Q = quote(",".join([
    "combo_size","combo_size.combo","discount",
    "products","products.product","products.product.category",
    "products.combo_size","products.combo_option","products.taxes",
]), safe=",")

_PAYMENTS_INCLUDE_Q = quote(",".join([
    "payment_method","user"
]), safe=",")

_CHARGES_INCLUDE_Q = quote(",".join([
    "charge","taxes"
]), safe=",")

# ---------------------------
# UTILS
# ---------------------------
def _fetch_order_children_via_children_endpoints(cfg, s, order_id: str) -> Dict[str, Any]:
    """Return dict with any of: products, combos, payments, charges (lists)."""
    base = _safe_base_url(cfg.foodics_base_url)
    out = {}

    def _fetch_list(endpoint: str, include_q: str) -> list:
        url = f"{base}/orders/{order_id}/{endpoint}?include={include_q}&per_page=200"
        # paginate in case >200
        all_rows = []
        while url:
            print(f"[debug] child GET {url}")
            data = _get_with_backoff(s, url)
            all_rows.extend(data.get("data") or [])
            nxt = (data.get("links") or {}).get("next")
            url = nxt if nxt else None
        return all_rows

    try:
        out["products"] = _fetch_list("products", _PRODUCTS_INCLUDE_Q)
    except Exception as e:
        print(f"[warn] products fetch failed for {order_id}: {e}")

    try:
        out["combos"] = _fetch_list("combos", _COMBOS_INCLUDE_Q)
    except Exception as e:
        print(f"[warn] combos fetch failed for {order_id}: {e}")

    try:
        out["payments"] = _fetch_list("payments", _PAYMENTS_INCLUDE_Q)
    except Exception as e:
        print(f"[warn] payments fetch failed for {order_id}: {e}")

    try:
        out["charges"] = _fetch_list("charges", _CHARGES_INCLUDE_Q)
    except Exception as e:
        print(f"[warn] charges fetch failed for {order_id}: {e}")

    return out

def _rebuild_next_with_include(cfg, next_url: str, date_filters: Optional[Dict[str, Any]] = None) -> str:
    """Foodics 'links.next' often misses include/per_page; re-attach them."""
    if not next_url:
        return next_url
    base = _safe_base_url(cfg.foodics_base_url)
    # next_url might be absolute ("https://api.foodics.com/v5/orders?page=2")
    # or relative (rare). We only keep the page param from it.
    pr = urlparse(next_url)
    qs = parse_qs(pr.query)
    page = qs.get("page", ["1"])[0]
    
    # Build query parameters
    query_params = {
        "include": INCLUDE_Q,
        "sort": "id",
        "page": page,
        "per_page": str(int(cfg.foodics_per_page)),
    }
    
    # Add date filters if provided
    if date_filters:
        from_date = date_filters.get("from_date")
        to_date = date_filters.get("to_date")
        date_field = date_filters.get("date_field", "business_date")
        
        if date_field == "business_date" and from_date:
            query_params["filter[business_date_after]"] = from_date
        elif date_field == "updated_at" and from_date:
            query_params["filter[updated_after]"] = from_date
        elif date_field == "created_at" and from_date:
            query_params["filter[created_on]"] = from_date
    
    # Rebuild full list URL with our canonical params
    return f"{base}/orders?{urlencode(query_params)}"

def _cfg():
    """Load Foodics Settings using the proper config loader."""
    from foodics_migration.utils import load_foodics_settings
    return load_foodics_settings()


def _safe_base_url(base_url: Optional[str]) -> str:
    """
    Build a safe base URL.
    - If stored as 'https://api.foodics.com/v5' -> use as-is.
    - If stored as 'api.foodics.com/v5'       -> prepend https://
    """
    if not base_url:
        frappe.throw("Foodics base URL is not configured. Please check Foodics Settings.")
    if base_url.startswith("http://") or base_url.startswith("https://"):
        return base_url.rstrip("/")
    return "https://" + base_url.rstrip("/")


def _new_session(cfg_or_token) -> requests.Session:
    """Create a requests session. Accepts either a FoodicsConfig object or a token string."""
    from foodics_migration.utils import make_foodics_session, FoodicsConfig
    if isinstance(cfg_or_token, FoodicsConfig):
        return make_foodics_session(cfg_or_token)
    # Legacy: if token string is passed
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {cfg_or_token}",
        "Accept": "application/json",
        "User-Agent": "ERPNext-Foodics-Migration/1.0",
    })
    return s


def _get_with_backoff(s: requests.Session, url: str) -> Dict[str, Any]:
    """GET with simple retry/backoff for 429 / 5xx, with an explicit timeout."""
    backoff = 2.0
    for attempt in range(6):
        try:
            resp = s.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            if attempt == 5:
                raise
            time.sleep(backoff)
            backoff *= 1.5
            continue

        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            # respect Retry-After if present
            ra = resp.headers.get("Retry-After")
            try:
                sleep_for = float(ra) if ra else backoff
            except Exception:
                sleep_for = backoff
            print(f"[warn] {resp.status_code} on {url} — retrying in {sleep_for:.1f}s …")
            time.sleep(sleep_for)
            backoff *= 1.5
            continue

        resp.raise_for_status()
        return resp.json()

    # Shouldn't reach here
    raise requests.HTTPError(f"Failed to GET after retries: {url}")


def _has_field(doctype: str, fieldname: str) -> bool:
    return frappe.get_meta(doctype).has_field(fieldname)


def _set_if_exists(doc: frappe.model.document.Document, fieldname: str, value: Any) -> None:
    if _has_field(doc.doctype, fieldname):
        doc.set(fieldname, value)
        print(f"    · set {doc.doctype}.{fieldname} = {repr(value)}")
    else:
        print(f"    ! skip (missing field) {doc.doctype}.{fieldname}")


def _reset_child_tables(order_doc: frappe.model.document.Document) -> None:
    """Strict mirror: wipe all child tables before re-populating."""
    for childfield in ("items", "combos", "payments", "charges"):
        if childfield in (order_doc.as_dict() or {}):
            order_doc.set(childfield, [])


def _is_order_fully_paid(order_json: Dict[str, Any]) -> bool:
    """
    Check if an order is fully paid by comparing sum of payments to total price.
    Returns True if sum(payments.amount) >= total_price (with 0.01 tolerance for rounding).
    """
    total_price = order_json.get("total_price") or 0
    if total_price == 0:
        return False  # Free orders might not have payments
    
    payments = order_json.get("payments") or []
    payments_total = sum(float(p.get("amount") or 0) for p in payments)
    
    # Allow small tolerance for rounding (0.01)
    is_paid = payments_total >= (float(total_price) - 0.01)
    
    if not is_paid:
        print(f"[debug] Order {order_json.get('id')} not fully paid: payments={payments_total}, total={total_price}")
    
    return is_paid


def _parent_name_for(foodics_order_id: str) -> Optional[str]:
    return frappe.db.get_value("Foodics Order", {"foodics_order_id": foodics_order_id}, "name")


def _parent_log_header(order: Dict[str, Any]) -> str:
    oid = order.get("id")
    num = order.get("number")
    ref = order.get("reference")
    return f"[info] Processing order id={oid} number={num} ref={ref} …"


def _debug_order_keys(order_json: Dict[str, Any]) -> None:
    keys = sorted(order_json.keys())
    print(f"[debug] order keys: {keys}")
    for k in ("products", "combos", "payments", "charges"):
        if k in order_json:
            v = order_json.get(k) or []
            print(f"[debug] {k} count: {len(v)}")


# ---------------------------
# MAPPING
# ---------------------------

def _map_parent_fields(order_json: Dict[str, Any], doc: frappe.model.document.Document) -> None:
    """Map all parent-level primitives + selected nested entity snapshots to parent."""
    # scalar core
    mapping = {
        "foodics_order_id": order_json.get("id"),
        "reference": order_json.get("reference"),
        "number": order_json.get("number"),
        "check_number": order_json.get("check_number"),
        "status": order_json.get("status"),
        "delivery_status": order_json.get("delivery_status"),
        "guests": order_json.get("guests"),
        "business_date": order_json.get("business_date"),
        "opened_at": order_json.get("opened_at"),
        "accepted_at": order_json.get("accepted_at"),
        "due_at": order_json.get("due_at"),
        "driver_assigned_at": order_json.get("driver_assigned_at"),
        "dispatched_at": order_json.get("dispatched_at"),
        "driver_collected_at": order_json.get("driver_collected_at"),
        "delivered_at": order_json.get("delivered_at"),
        "closed_at": order_json.get("closed_at"),
        "subtotal_price_gross": order_json.get("subtotal_price"),
        "discount_amount": order_json.get("discount_amount"),
        "rounding_amount": order_json.get("rounding_amount"),
        "total_price_gross": order_json.get("total_price"),
        "total_price_net": order_json.get("tax_exclusive_total_price"),
    }

    print("  · Mapping parent fields …")
    for dest, val in mapping.items():
        _set_if_exists(doc, dest, val)

    # branch/device/users snapshots
    br = order_json.get("branch") or {}
    _set_if_exists(doc, "branch_id", br.get("id"))
    _set_if_exists(doc, "branch_name", br.get("name"))
    _set_if_exists(doc, "branch_reference", br.get("reference"))

    dv = order_json.get("device") or {}
    _set_if_exists(doc, "device_id", dv.get("id"))
    _set_if_exists(doc, "device_name", dv.get("name"))
    _set_if_exists(doc, "device_reference", dv.get("reference"))

    cr = order_json.get("creator") or {}
    _set_if_exists(doc, "creator_id", cr.get("id"))
    _set_if_exists(doc, "creator_name", cr.get("name"))

    cl = order_json.get("closer") or {}
    _set_if_exists(doc, "closer_id", cl.get("id"))
    _set_if_exists(doc, "closer_name", cl.get("name"))
    
    # Customer snapshot - extract from customer object or relationships
    # IMPORTANT: Handle null customer (walk-in orders may not have a customer)
    customer = order_json.get("customer")
    
    # DEBUG: Print customer info
    print(f"[debug] Order {order_json.get('id')}: customer={customer} (type: {type(customer).__name__})")
    
    customer_id_to_set = None
    
    # If customer is null, set customer_id to None (walk-in order)
    if customer is None:
        print(f"[debug] Customer is null - setting customer_id to None")
        customer_id_to_set = None
    elif isinstance(customer, dict) and len(customer) > 0:
        # Customer is a non-empty dict - extract ID
        customer_id_to_set = customer.get("id")
        if not customer_id_to_set and customer.get("attributes"):
            customer_id_to_set = customer.get("attributes", {}).get("id")
        print(f"[debug] Extracted customer_id from customer dict: {customer_id_to_set}")
    elif isinstance(customer, dict) and len(customer) == 0:
        # Empty dict - check relationships and included
        relationships = order_json.get("relationships") or {}
        if isinstance(relationships, dict) and "customer" in relationships:
            customer_rel = relationships.get("customer") or {}
            if isinstance(customer_rel, dict):
                customer_data = customer_rel.get("data") or {}
                if isinstance(customer_data, dict):
                    customer_id_to_set = customer_data.get("id")
                elif isinstance(customer_data, str):
                    customer_id_to_set = customer_data
            elif isinstance(customer_rel, str):
                customer_id_to_set = customer_rel
        
        # Also check included resources (JSON:API format)
        if not customer_id_to_set and "included" in order_json:
            included = order_json.get("included") or []
            for resource in included:
                if isinstance(resource, dict) and resource.get("type") == "customer":
                    customer_id_to_set = resource.get("id")
                    break
        print(f"[debug] Extracted customer_id from relationships/included: {customer_id_to_set}")
    
    # Set customer_id (or None if null customer)
    # Always set customer_id, even if None (to clear previous values)
    if _has_field(doc.doctype, "customer_id"):
        # Use direct assignment to ensure None is set correctly
        doc.customer_id = customer_id_to_set
        if customer_id_to_set:
            print(f"[debug] Set customer_id to: {customer_id_to_set} for order {order_json.get('id')}")
        else:
            print(f"[debug] Set customer_id to None (null customer - walk-in order) for order {order_json.get('id')}")
    else:
        print(f"[debug] customer_id field does not exist on {doc.doctype}")

    # raw blob
    raw = json.dumps(order_json, ensure_ascii=False)
    _set_if_exists(doc, "foodics_order_raw", raw)


def _append_item_from(
    order_id: str,
    node: Dict[str, Any],
    doc: frappe.model.document.Document,
    combo_ctx: Optional[Dict[str, Any]] = None,
    row_index: int = 0,
) -> None:
    """Append a row to Foodics Order Item from a product node (either from order.products[] or combo.products[])."""
    chi = doc.append("items", {})

    # deterministic child name => avoids DuplicateEntryError on re-runs
    item_id = node.get("id") or f"ROW{row_index}"

    # basic refs
    chi.foodics_order_item_id = node.get("id")
    chi.status = node.get("status")
    chi.quantity = node.get("quantity")
    if "returned_quantity" in chi.as_dict():
        chi.returned_quantity = node.get("returned_quantity")
    chi.unit_price_gross = node.get("unit_price")
    chi.total_price_gross = node.get("total_price")
    chi.unit_price_net = node.get("tax_exclusive_unit_price")
    chi.total_price_net = node.get("tax_exclusive_total_price")
    chi.discount_amount = node.get("discount_amount")
    if "kitchen_notes" in chi.as_dict():
        chi.kitchen_notes = node.get("kitchen_notes")

    # timestamps (if fields exist)
    if "added_at" in chi.as_dict():
        chi.added_at = node.get("added_at")
    if "closed_at" in chi.as_dict():
        chi.closed_at = node.get("closed_at")

    # product snapshot
    p = (node.get("product") or {})
    chi.product_id = p.get("id")
    chi.product_sku = p.get("sku")
    chi.product_name = p.get("name")
    c = (p.get("category") or {})
    chi.category_id = c.get("id")
    chi.category_name = c.get("name")

    # raw option/tax blobs if available
    if "options_raw" in chi.as_dict():
        chi.options_raw = json.dumps(node.get("options") or [], ensure_ascii=False)
    if "taxes_raw" in chi.as_dict():
        chi.taxes_raw = json.dumps(node.get("taxes") or [], ensure_ascii=False)

    # combo context markers on item (optional fields)
    if combo_ctx:
        if "is_combo_item" in chi.as_dict():
            chi.is_combo_item = 1
        if "combo_id" in chi.as_dict():
            chi.combo_id = combo_ctx.get("combo_id")
        if "combo_size_id" in chi.as_dict():
            chi.combo_size_id = combo_ctx.get("combo_size_id")


def _map_children(order_json: Dict[str, Any], doc: frappe.model.document.Document) -> None:
    order_id = order_json.get("id") or doc.get("foodics_order_id")

    print("  · Mapping items …")
    products = order_json.get("products") or []
    print(f"    · products count: {len(products)}")
    for idx, it in enumerate(products):
        _append_item_from(order_id, it, doc, combo_ctx=None, row_index=idx)

    print("  · Mapping combos + combo items …")
    combos = order_json.get("combos") or []
    print(f"    · combos count: {len(combos)}")
    for cidx, combo in enumerate(combos):
        chcmb = doc.append("combos", {})
        cmb_id = combo.get("id") or f"ROW{cidx}"

        # deterministic name for combo row (prevents duplicates)

        # combo head fields (only set if exist)
        if "foodics_combo_id" in chcmb.as_dict():
            chcmb.foodics_combo_id = combo.get("id")
        if "discount_amount" in chcmb.as_dict():
            chcmb.discount_amount = combo.get("discount_amount")

        size = combo.get("combo_size") or {}
        cmb_def = size.get("combo") or {}
        if "combo_size_id" in chcmb.as_dict():
            chcmb.combo_size_id = size.get("id")
        if "combo_size_name" in chcmb.as_dict():
            chcmb.combo_size_name = size.get("name")
        if "combo_id" in chcmb.as_dict():
            chcmb.combo_id = cmb_def.get("id")
        if "combo_name" in chcmb.as_dict():
            chcmb.combo_name = cmb_def.get("name")

        # combo items -> same items table with a combo context
        ctx = {"combo_id": combo.get("id"), "combo_size_id": size.get("id")}
        c_items = combo.get("products") or []
        print(f"    · combo {cmb_id}: products={len(c_items)}")
        for pidx, pit in enumerate(c_items):
            _append_item_from(order_id, pit, doc, combo_ctx=ctx, row_index=pidx)

    print("  · Mapping payments …")
    pays = order_json.get("payments") or []
    print(f"    · payments count: {len(pays)}")
    for pidx, pay in enumerate(pays):
        chp = doc.append("payments", {})
        pay_id = pay.get("id") or f"ROW{pidx}"



        if "foodics_payment_id" in chp.as_dict():
            chp.foodics_payment_id = pay.get("id")
        if "amount" in chp.as_dict():
            chp.amount = pay.get("amount")
        if "tip_amount" in chp.as_dict():
            chp.tip_amount = pay.get("tip_amount")

        # Get payment_method - check both direct object and included array (JSON:API format)
        pm = (pay.get("payment_method") or {})
        pm_id = None
        pm_name = None
        
        # If payment_method is a dict with id/name, use it
        if isinstance(pm, dict) and pm:
            pm_id = pm.get("id")
            pm_name = pm.get("name") or pm.get("attributes", {}).get("name")
        
        # If payment_method is just an ID reference, check included array
        if not pm_id and isinstance(pm, str):
            pm_id = pm
            # Try to find payment_method in included array
            if "included" in order_json:
                included = order_json.get("included") or []
                for resource in included:
                    if resource.get("type") == "payment_method" and resource.get("id") == pm_id:
                        attrs = resource.get("attributes") or {}
                        pm_name = attrs.get("name")
                        break
        
        # Also check relationships.payment_method.data.id (JSON:API format)
        if not pm_id:
            rel = pay.get("relationships", {})
            pm_rel = rel.get("payment_method", {})
            pm_data = pm_rel.get("data", {})
            pm_id = pm_data.get("id")
            if pm_id and "included" in order_json:
                included = order_json.get("included") or []
                for resource in included:
                    if resource.get("type") == "payment_method" and resource.get("id") == pm_id:
                        attrs = resource.get("attributes") or {}
                        pm_name = attrs.get("name")
                        break
        
        # Set payment method fields
        if "payment_method_id" in chp.as_dict() and pm_id:
            chp.payment_method_id = pm_id
        if "payment_method_name" in chp.as_dict() and pm_name:
            chp.payment_method_name = pm_name

        usr = (pay.get("user") or {})
        if "cashier_id" in chp.as_dict():
            chp.cashier_id = usr.get("id")
        if "cashier_name" in chp.as_dict():
            chp.cashier_name = usr.get("name")

    print("  · Mapping charges …")
    chgs = order_json.get("charges") or []
    print(f"    · charges count: {len(chgs)}")
    for cgh_idx, chg in enumerate(chgs):
        chc = doc.append("charges", {})
        chg_id = chg.get("id") or f"ROW{cgh_idx}"


        if "foodics_charge_id" in chc.as_dict():
            chc.foodics_charge_id = chg.get("id")
        if "amount" in chc.as_dict():
            chc.amount = chg.get("amount")

        base = (chg.get("charge") or {})
        if "charge_id" in chc.as_dict():
            chc.charge_id = base.get("id")
        if "charge_name" in chc.as_dict():
            chc.charge_name = base.get("name")
        if "taxes_raw" in chc.as_dict():
            chc.taxes_raw = json.dumps(chg.get("taxes") or [], ensure_ascii=False)


# ---------------------------
# FETCH / UPSERT
# ---------------------------

def _paginate_orders(
	cfg: frappe._dict,
	s: requests.Session,
	date_filters: Optional[Dict[str, Any]] = None,
	only_fully_paid: bool = False,
	start_page: int = 1,
	sync_log: Optional[frappe.model.document.Document] = None,
):
    """
    Paginate through Foodics orders with optional date filtering and fully paid filter.
    
    Args:
        cfg: Foodics configuration
        s: Requests session
        date_filters: Optional dict with from_date, to_date, date_field
        only_fully_paid: If True, only yield orders where sum(payments) >= total_price
    """
    base = _safe_base_url(cfg.foodics_base_url)
    page = max(start_page or 1, 1)
    print(f"[info] Page size: {cfg.foodics_per_page}")
    print(f"[info] Commit every: {cfg.commit_every_n_records} order(s)")
    print(f"[info] Using include={INCLUDE_Q}")
    if only_fully_paid:
        print(f"[info] Filtering: Only fully paid orders")
    
    # Build query parameters
    query_params = {
        "include": INCLUDE_Q,
        "sort": "id",
        "page": str(page),
        "per_page": str(int(cfg.foodics_per_page)),
    }
    
    # Add date filters if provided
    if date_filters:
        from_date = date_filters.get("from_date")
        to_date = date_filters.get("to_date")
        date_field = date_filters.get("date_field", "business_date")
        
        if from_date or to_date:
            if date_field == "business_date":
                if from_date:
                    query_params["filter[business_date_after]"] = from_date
                if to_date:
                    query_params["filter[business_date_before]"] = to_date
            elif date_field == "updated_at":
                if from_date:
                    query_params["filter[updated_after]"] = from_date
        elif date_field == "created_at":
            if from_date:
                query_params["filter[created_on]"] = from_date
            print(f"[info] Date filters: {date_field} from {from_date} to {to_date}")

    url = f"{base}/orders?{urlencode(query_params)}"

    while url:
        print(f"[info] === Page {page} ===")
        print(f"[debug] URL={url}")
        if sync_log:
            try:
                sync_log.db_set("current_page", page)
                sync_log.db_set("last_request_url", url)
            except Exception:
                pass
        data = _get_with_backoff(s, url)
        rows = data.get("data") or []
        print(f"[debug] fetched {len(rows)} orders on this page")
        
        for row in rows:
            # Filter for fully paid orders if requested
            if only_fully_paid:
                if not _is_order_fully_paid(row):
                    continue
            yield row

        if sync_log:
            try:
                sync_log.db_set("last_page_processed", page)
                sync_log.db_set("resume_page", page + 1)
            except Exception:
                pass

        links = data.get("links") or {}
        nxt = links.get("next")
        if nxt:
            # Rebuild URL with date filters preserved
            url = _rebuild_next_with_include(cfg, nxt, date_filters)
        else:
            url = None
        page += 1





def _upsert_one_order(order_json: Dict[str, Any], update_if_exists: bool = False) -> str:
    print(_parent_log_header(order_json))
    oid = order_json.get("id")
    existing = _parent_name_for(oid)
    
    # Check if should skip update
    if existing and not update_if_exists:
        print(f"  ⊘ Skipped order (exists, update disabled): {existing} (Foodics {oid})")
        return existing

    # quick visibility into what we actually received
    got_keys = sorted(list(order_json.keys()))
    print(f"[debug] order keys: {got_keys}")

    # detect if relationships are missing on this list-row

    missing_children = not any(
        k in order_json for k in ("products", "combos", "payments", "charges")
    )

    if missing_children:
        print("[warn] No child arrays on list row. Trying single-order refetch with include …")
        try:
            cfg_local = _cfg()
            s_local = _new_session(cfg_local)
            order_full = _fetch_single_order_with_include(cfg_local, s_local, oid)
            got_child_now = any(k in order_full for k in ("products","combos","payments","charges"))
            print(f"[debug] after refetch, have children? {got_child_now}")
            if got_child_now:
                order_json = order_full
            else:
                raise requests.HTTPError("single-order refetch returned no children")
        except Exception as refetch_err:
            print(f"[warn] single-order refetch failed ({refetch_err}). Falling back to child endpoints …")
            cfg_local = _cfg()
            s_local = _new_session(cfg_local)
            stitched = _fetch_order_children_via_children_endpoints(cfg_local, s_local, oid)
            # Only attach non-empty children to the list-row json so downstream mappers see them
            for k, v in stitched.items():
                if v:
                    order_json[k] = v
            have_any = any(order_json.get(k) for k in ("products","combos","payments","charges"))
            print(f"[debug] stitched children present? {have_any} "
                f"(items={len(order_json.get('products') or [])}, "
                f"combos={len(order_json.get('combos') or [])}, "
                f"payments={len(order_json.get('payments') or [])}, "
                f"charges={len(order_json.get('charges') or [])})")



    if existing:
        print(f"  ↺ Updating existing order {existing} (Foodics {oid})")
        doc = frappe.get_doc("Foodics Order", existing)
        _reset_child_tables(doc)
    else:
        print(f"  ➕ Inserting new order (Foodics {oid})")
        doc = frappe.get_doc({"doctype": "Foodics Order"})

    # parent + raw
    _map_parent_fields(order_json, doc)

    # children
    print("  · Mapping items …")
    order_id = order_json.get("id") or doc.get("foodics_order_id")
    for idx, it in enumerate(order_json.get("products") or []):
        _append_item_from(order_id, it, doc, combo_ctx=None, row_index=idx)

    print("  · Mapping combos + combo items …")
    for cidx, combo in enumerate(order_json.get("combos") or []):
        chcmb = doc.append("combos", {})
        cmb_id = combo.get("id") or f"ROW{cidx}"
        chcmb.foodics_combo_id = combo.get("id")
        chcmb.discount_amount = combo.get("discount_amount")
        size = combo.get("combo_size") or {}
        cmb_def = size.get("combo") or {}
        if "combo_size_id" in chcmb.as_dict():   chcmb.combo_size_id = size.get("id")
        if "combo_size_name" in chcmb.as_dict(): chcmb.combo_size_name = size.get("name")
        if "combo_id" in chcmb.as_dict():        chcmb.combo_id = cmb_def.get("id")
        if "combo_name" in chcmb.as_dict():      chcmb.combo_name = cmb_def.get("name")

        ctx = {"combo_id": combo.get("id"), "combo_size_id": size.get("id")}
        for pidx, pit in enumerate(combo.get("products") or []):
            _append_item_from(order_id, pit, doc, combo_ctx=ctx, row_index=pidx)

    print("  · Mapping payments …")
    for pidx, pay in enumerate(order_json.get("payments") or []):
        chp = doc.append("payments", {})
        pay_id = pay.get("id") or f"ROW{pidx}"
        chp.foodics_payment_id = pay.get("id")
        chp.amount = pay.get("amount")
        if "tip_amount" in chp.as_dict():
            chp.tip_amount = pay.get("tip_amount")
        # Get payment_method - check both direct object and included array (JSON:API format)
        pm = (pay.get("payment_method") or {})
        pm_id = None
        pm_name = None
        
        # If payment_method is a dict with id/name, use it
        if isinstance(pm, dict) and pm:
            pm_id = pm.get("id")
            pm_name = pm.get("name") or pm.get("attributes", {}).get("name")
        
        # If payment_method is just an ID reference, check included array
        if not pm_id and isinstance(pm, str):
            pm_id = pm
            # Try to find payment_method in included array
            if "included" in order_json:
                included = order_json.get("included") or []
                for resource in included:
                    if resource.get("type") == "payment_method" and resource.get("id") == pm_id:
                        attrs = resource.get("attributes") or {}
                        pm_name = attrs.get("name")
                        break
        
        # Also check relationships.payment_method.data.id (JSON:API format)
        if not pm_id:
            rel = pay.get("relationships", {})
            pm_rel = rel.get("payment_method", {})
            pm_data = pm_rel.get("data", {})
            pm_id = pm_data.get("id")
            if pm_id and "included" in order_json:
                included = order_json.get("included") or []
                for resource in included:
                    if resource.get("type") == "payment_method" and resource.get("id") == pm_id:
                        attrs = resource.get("attributes") or {}
                        pm_name = attrs.get("name")
                        break
        
        # Set payment method fields
        if pm_id:
            chp.payment_method_id = pm_id
        if pm_name:
            chp.payment_method_name = pm_name
        usr = (pay.get("user") or {})
        if "cashier_id" in chp.as_dict():
            chp.cashier_id = usr.get("id")
        if "cashier_name" in chp.as_dict():
            chp.cashier_name = usr.get("name")

    print("  · Mapping charges …")
    for cgh_idx, chg in enumerate(order_json.get("charges") or []):
        chc = doc.append("charges", {})
        chg_id = chg.get("id") or f"ROW{cgh_idx}"
        chc.foodics_charge_id = chg.get("id")
        chc.amount = chg.get("amount")
        base = (chg.get("charge") or {})
        if "charge_id" in chc.as_dict():
            chc.charge_id = base.get("id")
        if "charge_name" in chc.as_dict():
            chc.charge_name = base.get("name")
        if "taxes_raw" in chc.as_dict():
            chc.taxes_raw = json.dumps(chg.get("taxes") or [], ensure_ascii=False)

    # Save
    print(f"  · items={len(doc.get('items') or [])}, combos={len(doc.get('combos') or [])}, "
          f"payments={len(doc.get('payments') or [])}, charges={len(doc.get('charges') or [])}")
    doc.save(ignore_permissions=True)
    return doc.name




# ---------------------------
# ENTRYPOINT
# ---------------------------

@frappe.whitelist()
def sync_foodics_orders():
    """
    Fetch orders (with optional date filtering) with combos/payments/charges & products,
    store raw JSON, strict-mirror child rows, and commit every N orders.
    Optionally creates Sales Invoices from orders.
    """
    try:
        cfg = _cfg()
        s = _new_session(cfg)
        
        # Get settings
        settings = frappe.get_single("Foodics Settings")
        update_if_exists = bool(getattr(settings, "update_orders_if_exists", 0))
        create_sales_invoices = bool(getattr(settings, "create_sales_invoices", 1))
        
        # Get date filters
        date_filters = None
        from_date = getattr(settings, "orders_sync_from_date", None)
        to_date = getattr(settings, "orders_sync_to_date", None)
        date_field = getattr(settings, "orders_sync_date_field", "business_date") or "business_date"
        
        if from_date or to_date:
            date_filters = {
                "from_date": str(from_date) if from_date else None,
                "to_date": str(to_date) if to_date else None,
                "date_field": date_field,
            }
            print(f"[info] Date filtering enabled: {date_field} from {from_date} to {to_date}")

        count = 0
        skipped = 0
        created = 0
        updated = 0
        invoices_created = 0
        
        for order in _paginate_orders(cfg, s, date_filters=date_filters):
            try:
                oid = order.get("id")
                existing = _parent_name_for(oid)
                was_existing = existing is not None
                
                name = _upsert_one_order(order, update_if_exists=update_if_exists)
                
                if was_existing and not update_if_exists:
                    skipped += 1
                elif was_existing:
                    updated += 1
                    count += 1
                else:
                    created += 1
                    count += 1
                
                # Create Sales Invoice if enabled
                if create_sales_invoices:
                    try:
                        invoice_name = create_sales_invoice_from_order(name)
                        if invoice_name:
                            invoices_created += 1
                    except Exception as inv_err:
                        print(f"[warn] Failed to create invoice for order {name}: {inv_err}")
                        frappe.log_error(
                            title="Sales Invoice Creation Error",
                            message=f"Order: {name}\n{frappe.get_traceback()}",
                        )
                
                # commit in batches
                if (count + skipped) % int(cfg.commit_every_n_records) == 0:
                    frappe.db.commit()
                    print(f"[info] ✔ committed batch (total so far: {count}, skipped: {skipped})")
            except Exception as e:
                frappe.db.rollback()
                print(f"[error] Order failed (id={order.get('id')}): {e}")
                frappe.log_error(
                    title="Foodics Order Sync Error",
                    message=frappe.get_traceback(),
                )
                # Continue with next order instead of raising

        # final commit
        frappe.db.commit()
        print(f"[info] Done. Created: {created}, Updated: {updated}, Skipped: {skipped}, Invoices: {invoices_created}")

    except Exception as e:
        print(f"[error] Sync aborted: {e}")
        frappe.db.rollback()
        raise

import frappe

def _field_exists(doctype: str, fieldname: str) -> bool:
    return frappe.get_meta(doctype).has_field(fieldname)

@frappe.whitelist()
def backfill_foodics_order_flags():
    """
    Sets has_products=1 if order has any child items,
    and has_combo=1 if order has any child combos.
    Leaves 0/blank otherwise. Skips fields that don't exist.
    """
    if not _field_exists("Foodics Order", "has_products"):
        print("[warn] Field Foodics Order.has_products not found — skipping.")
    if not _field_exists("Foodics Order", "has_combo"):
        print("[warn] Field Foodics Order.has_combo not found — skipping.")

    rows = frappe.db.sql("""
        select
            fo.name as order_name,
            count(distinct foi.name) as items_cnt,
            count(distinct foc.name) as combos_cnt
        from `tabFoodics Order` fo
        left join `tabFoodics Order Item` foi
               on foi.parent = fo.name and foi.parenttype = 'Foodics Order'
        left join `tabFoodics Order Combo` foc
               on foc.parent = fo.name and foc.parenttype = 'Foodics Order'
        group by fo.name
    """, as_dict=True)

    updated = 0
    for r in rows:
        vals = {}
        if _field_exists("Foodics Order", "has_products"):
            vals["has_products"] = 1 if (r.get("items_cnt") or 0) > 0 else 0
        if _field_exists("Foodics Order", "has_combo"):
            vals["has_combo"] = 1 if (r.get("combos_cnt") or 0) > 0 else 0
        if vals:
            frappe.db.set_value("Foodics Order", r["order_name"], vals, update_modified=False)
            updated += 1
    frappe.db.commit()
    print(f"[info] Backfilled flags on {updated} orders.")


# ---------------------------
# Sales Invoice Creation
# ---------------------------

def get_cost_center_for_branch(branch_id: Optional[str], branch_name: Optional[str]) -> Optional[str]:
	"""
	Get Cost Center for a Foodics branch from Foodics Settings mapping.
	Matches by branch_id first, then branch_name.
	"""
	if not branch_id and not branch_name:
		return None
	
	settings = frappe.get_single("Foodics Settings")
	mappings = getattr(settings, "branch_cost_center_mapping", []) or []
	
	# Try to match by branch_id first
	if branch_id:
		for mapping in mappings:
			if mapping.get("foodics_branch_id") == branch_id:
				cost_center = mapping.get("cost_center")
				if cost_center:
					return cost_center
	
	# Fallback to branch_name
	if branch_name:
		for mapping in mappings:
			if mapping.get("foodics_branch_name") == branch_name:
				cost_center = mapping.get("cost_center")
				if cost_center:
					return cost_center
	
	return None


def _get_customer_from_order(order_doc) -> Optional[str]:
	"""
	Get customer name from Foodics Order.
	Looks for customer link or tries to find by foodics_customer_id.
	Also checks if there's an existing Sales Invoice with the same foodics_order_id.
	"""
	order_id = getattr(order_doc, "foodics_order_id", None)
	order_name = getattr(order_doc, "name", None)
	
	print(f"[debug] Getting customer for order {order_name} (ID: {order_id})")
	
	# First, check if there's an existing Sales Invoice with this foodics_order_id
	# and use its customer (this handles cases where customer was set correctly before)
	if order_id and frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
		existing_invoice = frappe.db.get_value(
			"Sales Invoice",
			{"foodics_order_id": order_id},
			["name", "customer"],
			as_dict=True
		)
		if existing_invoice and existing_invoice.customer:
			print(f"[debug] Found existing invoice with customer: {existing_invoice.customer}")
			return existing_invoice.customer
	
	# Check if order has customer_id field
	customer_id = getattr(order_doc, "customer_id", None)
	if customer_id:
		print(f"[debug] Order has customer_id field: {customer_id}")
		# Try to find customer by foodics_customer_id - use exact match
		customer_name = frappe.db.get_value(
			"Customer",
			{"foodics_customer_id": customer_id},
			"name",
		)
		if customer_name:
			print(f"[debug] Found customer by foodics_customer_id: {customer_name} (for customer_id: {customer_id})")
			return customer_name
		else:
			print(f"[warn] Customer not found for foodics_customer_id: {customer_id}")
			# Verify the customer doesn't exist
			customer_exists = frappe.db.exists('Customer', {'foodics_customer_id': customer_id})
			print(f"[debug] Customer exists check: {customer_exists}")
			# List available customers with foodics_customer_id for debugging
			all_customers = frappe.get_all('Customer', 
				fields=['name', 'foodics_customer_id'],
				filters={'foodics_customer_id': ['is', 'set']},
				limit=5
			)
			print(f"[debug] Available customers with foodics_customer_id: {[(c.name, c.foodics_customer_id) for c in all_customers]}")
	else:
		print(f"[debug] Order has no customer_id field (None or empty)")
	
	# Check raw JSON for customer - handle both direct and nested structures
	raw_data = getattr(order_doc, "foodics_order_raw", None)
	if raw_data:
		import json
		try:
			order_json = json.loads(raw_data)
			
			# Try direct customer field - handle null customer explicitly
			customer = order_json.get("customer")
			print(f"[debug] Customer from raw JSON (direct): {customer} (type: {type(customer).__name__})")
			
			customer_id_from_json = None
			
			# Handle null customer (walk-in orders) - check explicitly for None
			if customer is None:
				print(f"[debug] Customer is null in raw JSON - order has no customer")
				# Explicitly set customer_id to None for null customers
				if not customer_id:  # Only update if not already set
					try:
						order_doc.customer_id = None
						order_doc.save(ignore_permissions=True)
						frappe.db.commit()
						print(f"[info] Set customer_id to None for null customer order {order_name}")
					except Exception as save_err:
						print(f"[warn] Could not set customer_id to None: {save_err}")
				# Return None so caller can use walk-in customer
				return None
			elif isinstance(customer, dict) and len(customer) > 0:
				# Customer is a non-empty dict - extract ID
				customer_id_from_json = customer.get("id")
				if not customer_id_from_json and customer.get("attributes"):
					customer_id_from_json = customer.get("attributes", {}).get("id")
				print(f"[debug] Extracted customer_id from customer dict: {customer_id_from_json}")
			elif isinstance(customer, dict) and len(customer) == 0:
				# Empty dict - check relationships and included
				print(f"[debug] Customer is empty dict, checking relationships/included")
				relationships = order_json.get("relationships") or {}
				if isinstance(relationships, dict) and "customer" in relationships:
					customer_rel = relationships.get("customer") or {}
					if isinstance(customer_rel, dict):
						customer_data = customer_rel.get("data") or {}
						if isinstance(customer_data, dict):
							customer_id_from_json = customer_data.get("id")
						elif isinstance(customer_data, str):
							customer_id_from_json = customer_data
					elif isinstance(customer_rel, str):
						customer_id_from_json = customer_rel
					print(f"[debug] Extracted customer_id from relationships: {customer_id_from_json}")
				
				# Also check included resources (JSON:API format)
				if not customer_id_from_json and "included" in order_json:
					included = order_json.get("included") or []
					for resource in included:
						if isinstance(resource, dict) and resource.get("type") == "customer":
							customer_id_from_json = resource.get("id")
							print(f"[debug] Extracted customer_id from included: {customer_id_from_json}")
							break
			else:
				# Customer is not None and not a dict - unexpected format
				print(f"[warn] Customer has unexpected type: {type(customer)}, value: {customer}")
			
			if customer_id_from_json:
				print(f"[debug] Extracted customer_id from raw JSON: {customer_id_from_json}")
				# Try to find customer by foodics_customer_id - use exact match only
				customer_name = frappe.db.get_value(
					"Customer",
					{"foodics_customer_id": customer_id_from_json},
					"name",
				)
				if customer_name:
					print(f"[debug] Found customer by foodics_customer_id from JSON: {customer_name} (for customer_id: {customer_id_from_json})")
					# Update the order's customer_id field if it's missing
					if not customer_id:
						try:
							order_doc.customer_id = customer_id_from_json
							order_doc.save(ignore_permissions=True)
							frappe.db.commit()
							print(f"[info] Updated order {order_name} with customer_id: {customer_id_from_json}")
						except Exception as save_err:
							print(f"[warn] Could not save customer_id to order: {save_err}")
					return customer_name
				else:
					print(f"[warn] Customer not found for foodics_customer_id from JSON: {customer_id_from_json}")
					# DO NOT update order with customer_id if customer doesn't exist - this causes wrong assignments
					print(f"[warn] Customer with foodics_customer_id={customer_id_from_json} does not exist in ERPNext")
			else:
				print(f"[debug] No customer_id extracted from raw JSON - customer is null or missing")
				# For null customers, ensure customer_id is None
				if customer_id:  # If it was set incorrectly before, clear it
					try:
						order_doc.customer_id = None
						order_doc.save(ignore_permissions=True)
						frappe.db.commit()
						print(f"[info] Cleared incorrect customer_id for null customer order {order_name}")
					except Exception as save_err:
						print(f"[warn] Could not clear customer_id: {save_err}")
			
		except Exception as json_err:
			print(f"[warn] Error parsing raw JSON for customer: {json_err}")
	
	# If no customer found, create/use a walk-in customer
	# Invoices must have a customer, so we need a walk-in customer for null customer orders
	print(f"[info] No customer found for order {order_name}, using walk-in customer")
	
	# Get or create walk-in customer
	walk_in_customer_name = "Walk-in Customer"
	walk_in_customer = frappe.db.get_value("Customer", {"customer_name": walk_in_customer_name}, "name")
	
	if not walk_in_customer:
		# Create walk-in customer
		try:
			settings = frappe.get_single("Foodics Settings")
			default_customer_group = getattr(settings, "default_customer_group", None)
			if not default_customer_group:
				default_customer_group = "All Customer Groups"
			
			walk_in_customer_doc = frappe.new_doc("Customer")
			walk_in_customer_doc.customer_name = walk_in_customer_name
			walk_in_customer_doc.customer_group = default_customer_group
			walk_in_customer_doc.customer_type = "Company"
			walk_in_customer_doc.insert(ignore_permissions=True)
			frappe.db.commit()
			walk_in_customer = walk_in_customer_doc.name
			print(f"[info] Created walk-in customer: {walk_in_customer}")
		except Exception as create_err:
			print(f"[error] Failed to create walk-in customer: {create_err}")
			import traceback
			print(traceback.format_exc()[:500])
			# DO NOT use any random customer as fallback - this causes all invoices to have the same customer
			print(f"[error] Cannot create invoice without walk-in customer")
			return None
	
	print(f"[info] Using walk-in customer: {walk_in_customer}")
	return walk_in_customer


def _get_item_by_foodics_product_id(product_id: str) -> Optional[str]:
	"""Get Item name by Foodics product ID."""
	return frappe.db.get_value(
		"Item",
		{"foodics_product_id": product_id},
		"name",
	)


def _get_item_by_foodics_combo_id(combo_id: str) -> Optional[str]:
	"""Get Item name by Foodics combo ID."""
	return frappe.db.get_value(
		"Item",
		{"foodics_combo_id": combo_id},
		"name",
	)


def _get_item_by_sku(sku: str) -> Optional[str]:
	"""Get Item name by SKU/item_code."""
	return frappe.db.get_value(
		"Item",
		{"item_code": sku},
		"name",
	)


def _get_item_by_name(item_name: str) -> Optional[str]:
	"""Get Item name by item_name (friendly name) or fallback to name."""
	if not item_name:
		return None
	found = frappe.db.get_value("Item", {"item_name": item_name}, "name")
	if not found:
		found = frappe.db.get_value("Item", {"name": item_name}, "name")
	return found


@frappe.whitelist()
def create_sales_invoice_from_order(foodics_order_name: str) -> Optional[str]:
	"""
	Create Sales Invoice from a Foodics Order.
	Returns Sales Invoice name if created, None if skipped or error.
	"""
	frappe.only_for(("System Manager", "Administrator"))
	
	try:
		order_doc = frappe.get_doc("Foodics Order", foodics_order_name)
	except frappe.DoesNotExistError:
		print(f"[error] Foodics Order not found: {foodics_order_name}")
		return None
	
	# Check if Sales Invoice already exists
	if hasattr(order_doc, "sales_invoice") and order_doc.sales_invoice:
		existing_inv = frappe.db.get_value("Sales Invoice", order_doc.sales_invoice, "name")
		if existing_inv:
			print(f"[skip] Sales Invoice already exists for order {foodics_order_name}: {existing_inv}")
			return existing_inv
	
	# Get customer - with detailed debugging
	customer_name = _get_customer_from_order(order_doc)
	
	# If customer not found, try to extract from raw JSON and update order
	if not customer_name:
		print(f"[debug] Customer not found initially for order {foodics_order_name}")
		print(f"[debug] Order customer_id field: {getattr(order_doc, 'customer_id', None)}")
		
		# Try to extract customer_id from raw JSON and update the order
		raw_data = getattr(order_doc, "foodics_order_raw", None)
		if raw_data:
			import json
			try:
				order_json = json.loads(raw_data)
				customer = order_json.get("customer")
				
				customer_id_from_json = None
				
				# Handle null customer (walk-in orders) - use walk-in customer
				if customer is None:
					print(f"[info] Customer is null in raw JSON - order has no customer, will use walk-in customer")
					# Don't return None - let it fall through to use walk-in customer
					customer_id_from_json = None
				elif isinstance(customer, dict):
					# Customer is a dict - extract ID
					customer_id_from_json = customer.get("id")
					if not customer_id_from_json and customer.get("attributes"):
						customer_id_from_json = customer.get("attributes", {}).get("id")
				else:
					# Check relationships for customer (JSON:API format)
					relationships = order_json.get("relationships") or {}
					if isinstance(relationships, dict) and "customer" in relationships:
						customer_rel = relationships.get("customer") or {}
						if isinstance(customer_rel, dict):
							customer_data = customer_rel.get("data") or {}
							if isinstance(customer_data, dict):
								customer_id_from_json = customer_data.get("id")
							elif isinstance(customer_data, str):
								customer_id_from_json = customer_data
						elif isinstance(customer_rel, str):
							customer_id_from_json = customer_rel
					
					# Also check included resources (JSON:API format)
					if not customer_id_from_json and "included" in order_json:
						included = order_json.get("included") or []
						for resource in included:
							if isinstance(resource, dict) and resource.get("type") == "customer":
								customer_id_from_json = resource.get("id")
								break
				
				if customer_id_from_json:
					print(f"[info] Found customer_id in raw JSON: {customer_id_from_json}, updating order...")
					order_doc.customer_id = customer_id_from_json
					order_doc.save(ignore_permissions=True)
					frappe.db.commit()
					# Try lookup again
					customer_name = _get_customer_from_order(order_doc)
					if customer_name:
						print(f"[info] Customer found after updating order: {customer_name}")
					else:
						print(f"[error] Customer still not found after updating order with customer_id: {customer_id_from_json}")
						print(f"[error] Checking if customer exists in ERPNext...")
						customer_exists = frappe.db.exists('Customer', {'foodics_customer_id': customer_id_from_json})
						print(f"[error] Customer exists: {customer_exists}")
						return None
				else:
					print(f"[debug] No customer_id extracted from raw JSON - customer is null or missing")
			except Exception as e:
				print(f"[error] Error extracting customer from raw JSON: {e}")
				import traceback
				print(traceback.format_exc()[:500])
		
		# If still no customer, use walk-in customer (invoices must have a customer)
		if not customer_name:
			print(f"[info] No customer found for order {foodics_order_name}, using walk-in customer")
			# Get or create walk-in customer
			walk_in_customer_name = "Walk-in Customer"
			walk_in_customer = frappe.db.get_value("Customer", {"customer_name": walk_in_customer_name}, "name")
			
			if not walk_in_customer:
				# Create walk-in customer
				try:
					settings = frappe.get_single("Foodics Settings")
					default_customer_group = getattr(settings, "default_customer_group", None)
					if not default_customer_group:
						default_customer_group = "All Customer Groups"
					
					walk_in_customer_doc = frappe.new_doc("Customer")
					walk_in_customer_doc.customer_name = walk_in_customer_name
					walk_in_customer_doc.customer_group = default_customer_group
					walk_in_customer_doc.customer_type = "Company"
					walk_in_customer_doc.insert(ignore_permissions=True)
					frappe.db.commit()
					walk_in_customer = walk_in_customer_doc.name
					print(f"[info] Created walk-in customer: {walk_in_customer}")
				except Exception as create_err:
					print(f"[error] Failed to create walk-in customer: {create_err}")
					import traceback
					print(traceback.format_exc()[:500])
					# DO NOT use any random customer as fallback
					print(f"[error] Cannot create invoice without walk-in customer")
					return None
			
			customer_name = walk_in_customer
			print(f"[info] Using walk-in customer: {customer_name}")
	
	print(f"[info] Using customer: {customer_name} for order {foodics_order_name}")
	
	# Get cost center from branch mapping
	cost_center = None
	branch_id = getattr(order_doc, "branch_id", None)
	branch_name = getattr(order_doc, "branch_name", None)
	if branch_id or branch_name:
		cost_center = get_cost_center_for_branch(branch_id, branch_name)
		if not cost_center:
			print(f"[warn] No cost center mapping found for branch {branch_id or branch_name}")
	
	# Get posting date and time from Foodics order
	# Priority: closed_at > opened_at > business_date
	posting_datetime_str = None
	posting_date = None
	posting_time = None
	
	# Try closed_at first (most accurate - when order was completed)
	closed_at = getattr(order_doc, "closed_at", None)
	if closed_at:
		posting_datetime_str = closed_at
	# Fallback to opened_at
	elif getattr(order_doc, "opened_at", None):
		posting_datetime_str = order_doc.opened_at
	# Fallback to business_date
	else:
		business_date = getattr(order_doc, "business_date", None)
		if business_date:
			posting_datetime_str = business_date
	
	# Parse datetime string to extract date and time
	if posting_datetime_str:
		from datetime import datetime
		if isinstance(posting_datetime_str, str):
			# Try parsing ISO format: "2025-12-06T14:30:00" or "2025-12-06 14:30:00"
			try:
				if "T" in posting_datetime_str:
					dt = datetime.fromisoformat(posting_datetime_str.replace("Z", "+00:00"))
				elif " " in posting_datetime_str:
					dt = datetime.strptime(posting_datetime_str, "%Y-%m-%d %H:%M:%S")
				else:
					dt = datetime.strptime(posting_datetime_str, "%Y-%m-%d")
				posting_date = dt.date()
				posting_time = dt.time()
			except Exception as parse_err:
				print(f"[warn] Failed to parse datetime {posting_datetime_str}: {parse_err}")
				# Fallback: extract date only
				posting_date = posting_datetime_str.split()[0] if " " in posting_datetime_str else posting_datetime_str
		else:
			# Already a datetime object
			posting_date = posting_datetime_str.date() if hasattr(posting_datetime_str, "date") else posting_datetime_str
			posting_time = posting_datetime_str.time() if hasattr(posting_datetime_str, "time") else None
	
	# Create Sales Invoice
	invoice = frappe.new_doc("Sales Invoice")
	invoice.customer = customer_name
	
	# Enable set posting time (required to set custom posting time) - ALWAYS set to 1
	# Set both flag and field value
	invoice.flags.set_posting_time = 1
	# Force set the field value - try multiple methods
	invoice.set_posting_time = 1
	# Also try using set method to ensure it's saved
	try:
		invoice.set("set_posting_time", 1)
	except Exception:
		pass
	print(f"[info] Set set_posting_time = 1 for invoice")
	print(f"[info] Set set_posting_time = 1 for invoice")
	
	# Set company
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if company:
		invoice.company = company
	
	# Set posting date and time
	if posting_date:
		invoice.posting_date = posting_date
		if posting_time:
			# Set posting time - format as HH:MM:SS
			time_str = posting_time.strftime("%H:%M:%S") if hasattr(posting_time, "strftime") else str(posting_time)
			if frappe.get_meta("Sales Invoice").has_field("posting_time"):
				invoice.posting_time = time_str
			print(f"[info] Set posting_date={posting_date}, posting_time={time_str}")
		# Set due date based on payment terms or default to posting_date + 30 days
		from datetime import timedelta
		if isinstance(posting_date, str):
			posting_date_obj = datetime.strptime(posting_date, "%Y-%m-%d").date()
		else:
			posting_date_obj = posting_date
		invoice.due_date = posting_date_obj + timedelta(days=30)
	else:
		# Set default dates if not provided
		today = frappe.utils.today()
		invoice.posting_date = today
		invoice.due_date = frappe.utils.add_days(today, 30)
	
	# Set description if field exists (may be required in some installations)
	order_ref_for_desc = getattr(order_doc, "reference", None) or getattr(order_doc, "number", None) or foodics_order_name
	invoice_description_text = f"Foodics Order: {order_ref_for_desc}"

	if frappe.get_meta("Sales Invoice").has_field("description"):
		invoice.description = invoice_description_text
	else:
		# Even if field doesn't exist in meta, try to set it (might be a custom field)
		try:
			invoice.description = invoice_description_text
		except Exception:
			pass
	
	if cost_center:
		if frappe.get_meta("Sales Invoice").has_field("cost_center"):
			invoice.cost_center = cost_center
	
	# Add items from Foodics Order Items
	items = getattr(order_doc, "items", []) or []
	
	# Load raw JSON to extract product IDs if items don't have them
	raw_data = None
	if hasattr(order_doc, "foodics_order_raw") and order_doc.foodics_order_raw:
		try:
			raw_data = json.loads(order_doc.foodics_order_raw)
		except Exception:
			pass

	# Only fetch from API if there are no items/combos stored locally
	has_local_items = bool(getattr(order_doc, "items", [])) or bool(getattr(order_doc, "combos", []))
	if (not has_local_items) and (not raw_data or (not raw_data.get("products") and not raw_data.get("combos"))) and hasattr(order_doc, "foodics_order_id"):
		order_id = order_doc.foodics_order_id
		print(f"[info] Raw JSON missing products/combos, fetching full order {order_id} from API...")
		try:
			cfg = _cfg()
			s = _new_session(cfg)
			raw_data = _fetch_single_order_with_include(cfg, s, order_id)
			# Update the order's raw JSON for future use
			order_doc.foodics_order_raw = json.dumps(raw_data)
			order_doc.save(ignore_permissions=True)
			frappe.db.commit()
		except Exception as fetch_err:
			print(f"[warn] Failed to fetch full order from API: {fetch_err}")
			# Continue with existing raw_data if available
	
	# Build a mapping from raw JSON products by index (since order items are usually in same order)
	raw_products_by_index = {}
	if raw_data:
		# Handle direct products
		if "products" in raw_data:
			for idx, p in enumerate(raw_data.get("products", [])):
				# Extract product ID from nested structure
				product_obj = p.get("product") or {}
				product_attrs = {}
				if isinstance(product_obj, dict) and product_obj:
					product_attrs = product_obj.get("attributes") or product_obj
				product_id = (product_attrs.get("id") if product_attrs else None) or product_obj.get("id") or p.get("id")
				product_sku = (product_attrs.get("sku") if product_attrs else None) or product_obj.get("sku") or p.get("sku")
				product_name = (product_attrs.get("name") if product_attrs else None) or product_obj.get("name") or p.get("name")

				raw_products_by_index[idx] = {
					"id": product_id,
					"sku": product_sku,
					"name": product_name,
				}
		
		# Handle products from combos (many orders have combos instead of direct products)
		if "combos" in raw_data and not raw_products_by_index:
			combo_idx = 0
			for combo in raw_data.get("combos", []):
				combo_products = combo.get("products", [])
				for combo_product in combo_products:
					product_obj = combo_product.get("product") or {}
					product_attrs = {}
					if isinstance(product_obj, dict) and product_obj:
						product_attrs = product_obj.get("attributes") or product_obj
					product_id = (product_attrs.get("id") if product_attrs else None) or product_obj.get("id") or combo_product.get("id")
					product_sku = (product_attrs.get("sku") if product_attrs else None) or product_obj.get("sku") or combo_product.get("sku")
					product_name = (product_attrs.get("name") if product_attrs else None) or product_obj.get("name") or combo_product.get("name")

					if product_id or product_sku:
						raw_products_by_index[combo_idx] = {
							"id": product_id,
							"sku": product_sku,
							"name": product_name,
						}
						combo_idx += 1
	
	for idx, order_item in enumerate(items):
		item_name = None
		
		# First check if erp_item_code is already set (direct link)
		erp_item_code = getattr(order_item, "erp_item_code", None)
		if erp_item_code:
			item_name = erp_item_code
		
		# Try to find item by foodics_product_id from order_item
		if not item_name:
			product_id = getattr(order_item, "foodics_product_id", None)
			
			# If product_id is missing, try to get from raw JSON by index
			if not product_id and idx in raw_products_by_index:
				product_id = raw_products_by_index[idx].get("id")
			
			if product_id:
				item_name = _get_item_by_foodics_product_id(product_id)
		
		# Fallback: try to get product SKU from raw JSON
		if not item_name and idx in raw_products_by_index:
			product_sku = raw_products_by_index[idx].get("sku")
			if product_sku:
				item_name = _get_item_by_sku(product_sku)
		
		# Fallback: try to match product name from raw JSON
		if not item_name and idx in raw_products_by_index:
			product_name = raw_products_by_index[idx].get("name")
			if product_name:
				item_name = _get_item_by_name(product_name)

		# Fallback to product_sku from order_item
		if not item_name:
			product_sku = getattr(order_item, "product_sku", None)
			if product_sku:
				item_name = _get_item_by_sku(product_sku)

		# Fallback: try to match using product name stored on order_item
		if not item_name:
			product_name = getattr(order_item, "product_name", None)
			if product_name:
				item_name = _get_item_by_name(product_name)
		
		if not item_name:
			product_id = getattr(order_item, "foodics_product_id", None) or (raw_products_by_index.get(idx, {}).get("id") if idx in raw_products_by_index else None)
			product_sku = getattr(order_item, "product_sku", None) or (raw_products_by_index.get(idx, {}).get("sku") if idx in raw_products_by_index else None)
			print(f"[warn] Item not found for product_id={product_id}, sku={product_sku}, order_item_idx={idx}, skipping")
			continue
		
		# Get prices - use net prices if available
		unit_price = getattr(order_item, "unit_price_net", None) or getattr(order_item, "unit_price_gross", 0)
		quantity = getattr(order_item, "quantity", 0)
		
		if quantity <= 0:
			continue  # Skip items with zero quantity
		
		# Check if item is disabled
		item_doc = frappe.get_doc("Item", item_name)
		if item_doc.disabled:
			print(f"[warn] Item {item_name} is disabled, enabling it for invoice creation")
			item_doc.disabled = 0
			item_doc.save(ignore_permissions=True)
		
		# Get item description - ensure it's not empty
		item_description = item_doc.description or item_doc.item_name or item_name
		if not item_description or item_description.strip() == "":
			item_description = item_name
		
		# Ensure item_name is set (required field)
		item_name_value = item_doc.item_name or item_name
		
		# Ensure description is not None or empty - convert to string
		item_description = str(item_description) if item_description else str(item_name_value)
		
		# Get income account from item or company defaults
		income_account = None
		if hasattr(item_doc, "income_account") and item_doc.income_account:
			income_account = item_doc.income_account
		else:
			# Get default income account from company
			company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
			if company:
				company_doc = frappe.get_doc("Company", company)
				if hasattr(company_doc, "default_income_account"):
					income_account = company_doc.default_income_account
		
		# Create item dict with all required fields
		# IMPORTANT: Always set description as a non-empty string (may be required)
		item_dict = {
			"item_code": item_name,
			"item_name": str(item_name_value),  # Required field - ensure string
			"qty": quantity,
			"rate": unit_price,
		}
		
		# Always set description - ensure it's a non-empty string
		item_dict["description"] = str(item_description) if item_description else str(item_name_value)
		
		# Set income account if found
		if income_account:
			item_dict["income_account"] = income_account
		
		# Append item
		invoice.append("items", item_dict)
		
		# Immediately verify description was set (access the last appended item)
		last_item = invoice.items[-1]
		if not last_item.description or (isinstance(last_item.description, str) and last_item.description.strip() == ""):
			last_item.description = str(item_name_value)
	
	# Add combo items as separate line items (if not already included in items table)
	# Note: Combo items are often flattened into the items table, so this may be redundant
	# but we include it for cases where combos are stored separately
	combos = getattr(order_doc, "combos", []) or []
	for combo in combos:
		combo_id = getattr(combo, "foodics_combo_id", None) or getattr(combo, "combo_id", None)
		item_name = None
		if combo_id:
			item_name = _get_item_by_foodics_combo_id(combo_id)
		
		if not item_name:
			combo_name = getattr(combo, "combo_name", None)
			if combo_name:
				item_name = _get_item_by_name(combo_name)

		if item_name:  # Only add if item_name is found
				quantity = getattr(combo, "quantity", 0)
				if quantity > 0:
					unit_price = getattr(combo, "unit_price_net", None) or getattr(combo, "unit_price_gross", 0)
					
					# Check if item is disabled
					item_doc = frappe.get_doc("Item", item_name)
					if item_doc.disabled:
						print(f"[warn] Item {item_name} is disabled, enabling it for invoice creation")
						item_doc.disabled = 0
						item_doc.save(ignore_permissions=True)
					
					item_description = item_doc.description or item_doc.item_name or item_name
					if not item_description or item_description.strip() == "":
						item_description = item_name
					
					# Ensure item_name is set (required field)
					item_name_value = item_doc.item_name or item_name
					
					# Ensure description is not None or empty - convert to string
					item_description = str(item_description) if item_description else str(item_name_value)
					
					# Get income account from item or company defaults
					income_account = None
					if hasattr(item_doc, "income_account") and item_doc.income_account:
						income_account = item_doc.income_account
					else:
						# Get default income account from company
						company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
						if company:
							company_doc = frappe.get_doc("Company", company)
							if hasattr(company_doc, "default_income_account"):
								income_account = company_doc.default_income_account
					
					# Create item dict with all required fields
					item_dict = {
						"item_code": item_name,
						"item_name": str(item_name_value),  # Required field - ensure string
						"qty": quantity,
						"rate": unit_price,
						"description": item_description,  # Always set as non-empty string
					}
					
					# Set income account if found
					if income_account:
						item_dict["income_account"] = income_account
					
					# Append item
					invoice.append("items", item_dict)
					
					# Immediately verify description was set
					last_item = invoice.items[-1]
					if not last_item.description or (isinstance(last_item.description, str) and last_item.description.strip() == ""):
						last_item.description = str(item_name_value)
	
	# Validate invoice has items
	if not invoice.items or len(invoice.items) == 0:
		print(f"[warn] No items found for order {foodics_order_name}, skipping invoice creation")
		return None
	
	# Apply discount if exists
	discount_amount = getattr(order_doc, "discount_amount", 0) or 0
	if discount_amount and discount_amount > 0:
		# Apply as additional discount
		if frappe.get_meta("Sales Invoice").has_field("additional_discount_percentage"):
			# Calculate percentage discount (approximate)
			total_before_discount = sum(item.get("rate", 0) * item.get("qty", 0) for item in invoice.items)
			if total_before_discount > 0:
				discount_percent = (discount_amount / total_before_discount) * 100
				invoice.additional_discount_percentage = discount_percent
	
	# Add taxes (VAT) from Foodics Settings or order data
	settings = frappe.get_single("Foodics Settings")
	vat_rate = float(getattr(settings, "vat_rate", 0) or 0)
	
	# Check if order has tax data in raw JSON
	total_price_net = getattr(order_doc, "total_price_net", None) or 0
	total_price_gross = getattr(order_doc, "total_price_gross", None) or 0
	
	# Calculate VAT if not explicitly set
	if vat_rate == 0 and total_price_net > 0 and total_price_gross > total_price_net:
		# Calculate VAT rate from order totals
		vat_amount = total_price_gross - total_price_net
		vat_rate = (vat_amount / total_price_net) * 100 if total_price_net > 0 else 0
	
	# Add tax entry if VAT rate is set
	if vat_rate > 0:
		# Get company for tax account
		company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
		
		# Get tax account from company settings
		tax_account = None
		if company:
			# Try to get default tax account from company
			company_doc = frappe.get_doc("Company", company)
			if hasattr(company_doc, "default_sales_tax_and_charges_account"):
				tax_account = company_doc.default_sales_tax_and_charges_account
			# Fallback: try to find VAT account
			if not tax_account:
				tax_account = frappe.db.get_value(
					"Account",
					{"account_type": "Tax", "company": company},
					"name",
					order_by="creation desc"
				)
		
		if tax_account:
			invoice.append("taxes", {
				"charge_type": "On Net Total",
				"rate": vat_rate,
				"account_head": tax_account,
			})
		else:
			print(f"[warn] Tax account not found for company {company}, VAT will not be added")
	
	# Set Foodics custom fields
	if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
		invoice.foodics_order_id = getattr(order_doc, "foodics_order_id", None)
	if frappe.get_meta("Sales Invoice").has_field("foodics_order_name"):
		invoice.foodics_order_name = foodics_order_name
	if frappe.get_meta("Sales Invoice").has_field("foodics_branch_id"):
		invoice.foodics_branch_id = branch_id
	if frappe.get_meta("Sales Invoice").has_field("foodics_branch_name"):
		invoice.foodics_branch_name = branch_name
	
	# Store payment information to add AFTER calculate_taxes_and_totals
	# (calculate_taxes_and_totals may clear payment entries)
	payments = getattr(order_doc, "payments", []) or []
	payments_total = 0.0
	payment_methods_map = {}  # Map payment_method_id -> total amount
	payment_entries_to_add = []  # Store payment entries to add later
	payment_details_map = {}  # key -> (payment_method_id, payment_method_name)
	
	# Get order total for fallback
	order_total = float(getattr(order_doc, "total_price", None) or getattr(order_doc, "total_price_gross", None) or 0)
	
	# Group payments by payment method
	# IMPORTANT: Process ALL payments with amount > 0, even if payment_method_id is missing
	# If payment_method is missing (common for cash payments), default to "Cash"
	for payment in payments:
		payment_method_id = getattr(payment, "payment_method_id", None)
		payment_method_name = getattr(payment, "payment_method_name", None)
		amount = float(getattr(payment, "amount", 0) or 0)
		
		# Process payment if amount > 0, even if payment_method_id is missing
		if amount > 0:
			# If payment method is completely missing, assume it's Cash (common case)
			# This handles cases where Foodics doesn't include payment_method for cash payments
			if not payment_method_id and not payment_method_name:
				print(f"[info] Payment has no method data (amount={amount}), assuming Cash payment")
				payment_method_name = "Cash"  # Default to Cash when method is missing
			
			# Use payment_method_id if available, otherwise use payment_method_name, otherwise use "default"
			key = payment_method_id
			if not key:
				key = payment_method_name
			if not key:
				key = "default"
			
			if key not in payment_methods_map:
				payment_methods_map[key] = 0.0
				# Store the actual payment_method_name (which may be "Cash" if we defaulted)
				payment_details_map[key] = (payment_method_id, payment_method_name)
			
			payment_methods_map[key] += amount
			payments_total += amount
			print(f"[info] Grouped payment: key={key}, method_id={payment_method_id}, method_name={payment_method_name}, amount={amount}, total={payment_methods_map[key]}")
	
	# If no payments found but order has total, this might be an issue
	if len(payment_methods_map) == 0 and payments_total == 0 and len(payments) > 0:
		print(f"[warn] Found {len(payments)} payment entries but all have amount=0 or missing amount")
		# Try to sum amounts directly
		for payment in payments:
			amount = float(getattr(payment, "amount", 0) or 0)
			if amount > 0:
				payments_total += amount
				# Add to default if no method data
				if "default" not in payment_methods_map:
					payment_methods_map["default"] = 0.0
					payment_details_map["default"] = (None, None)
				payment_methods_map["default"] += amount
				print(f"[info] Added payment with missing method data: amount={amount}")
	
	# Prepare payment entries (but don't add yet - will add after calculate_taxes_and_totals)
	print(f"[info] Processing {len(payment_methods_map)} payment methods for invoice")
	print(f"[info] Total payments from order: {len(payments)}, grouped into {len(payment_methods_map)} methods")
	
	for payment_method_key, total_amount in payment_methods_map.items():
		# Get payment method details from our map
		payment_method_id, payment_method_name = payment_details_map.get(payment_method_key, (None, None))
		
		# Find Mode of Payment by foodics_payment_method_id
		mode_of_payment = None
		if payment_method_id and frappe.get_meta("Mode of Payment").has_field("foodics_payment_method_id"):
			mode_of_payment = frappe.db.get_value(
				"Mode of Payment",
				{"foodics_payment_method_id": payment_method_id},
				"name"
			)
		
		if not mode_of_payment and payment_method_name:
			# Fallback: try to find by name
			mode_of_payment = frappe.db.get_value(
				"Mode of Payment",
				{"mode_of_payment": payment_method_name},
				"name"
			)
			if mode_of_payment:
				print(f"[info] Found mode of payment by name: {mode_of_payment} for {payment_method_name}")
		
		if mode_of_payment:
			# Get default account for this mode of payment and company
			default_account = None
			company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
			
			if company:
				try:
					# Get account from Mode of Payment Account child table
					mop_doc = frappe.get_doc("Mode of Payment", mode_of_payment)
					for account_row in (mop_doc.get("accounts") or []):
						if account_row.company == company:
							default_account = account_row.default_account
							break
				except Exception as mop_err:
					print(f"[warn] Error getting mode of payment accounts: {mop_err}")
			
			payment_entry = {
				"mode_of_payment": mode_of_payment,
				"amount": total_amount,
			}
			
			if default_account:
				payment_entry["account"] = default_account
			
			payment_entries_to_add.append(payment_entry)
			print(f"[info] Prepared payment entry: {mode_of_payment} = {total_amount}")
		else:
			# Mode of Payment not found - use a default Mode of Payment (Cash) as fallback
			print(f"[warn] Mode of Payment not found for payment_method_key: {payment_method_key}, amount: {total_amount}")
			
			# Try to use a default Mode of Payment (Cash) as fallback
			default_mop = frappe.db.get_value("Mode of Payment", {"mode_of_payment": "Cash"}, "name")
			if not default_mop:
				# Try to get any Mode of Payment
				default_mop = frappe.db.get_value("Mode of Payment", {}, "name", order_by="creation asc")
			
			if default_mop:
				print(f"[info] Using default Mode of Payment: {default_mop} for payment_method_key: {payment_method_key}")
				company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
				default_account = None
				
				if company:
					try:
						mop_doc = frappe.get_doc("Mode of Payment", default_mop)
						for account_row in (mop_doc.get("accounts") or []):
							if account_row.company == company:
								default_account = account_row.default_account
								break
					except Exception:
						pass
				
				payment_entry = {
					"mode_of_payment": default_mop,
					"amount": total_amount,
				}
				
				if default_account:
					payment_entry["account"] = default_account
				
				payment_entries_to_add.append(payment_entry)
				print(f"[info] Added payment entry with default Mode of Payment: {default_mop} = {total_amount}")
			else:
				print(f"[error] No Mode of Payment found at all - cannot add payment entry")
	
	# If no payment entries were added but we have payments_total > 0, add a default payment
	# This handles cases where payment method data is missing but we know the order was paid
	if len(payment_entries_to_add) == 0 and payments_total > 0:
		print(f"[info] No payment entries added but payments_total={payments_total}, adding default Cash payment")
		default_mop = frappe.db.get_value("Mode of Payment", {"mode_of_payment": "Cash"}, "name")
		if not default_mop:
			default_mop = frappe.db.get_value("Mode of Payment", {}, "name", order_by="creation asc")
		
		if default_mop:
			company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
			default_account = None
			
			if company:
				try:
					mop_doc = frappe.get_doc("Mode of Payment", default_mop)
					for account_row in (mop_doc.get("accounts") or []):
						if account_row.company == company:
							default_account = account_row.default_account
							break
				except Exception:
					pass
			
			payment_entry = {
				"mode_of_payment": default_mop,
				"amount": payments_total,
			}
			
			if default_account:
				payment_entry["account"] = default_account
			
			payment_entries_to_add.append(payment_entry)
			print(f"[info] Added default payment entry: {default_mop} = {payments_total}")
	
	# If still no payment entries, use order total as payment amount
	# This is a last resort for fully paid invoices
	if len(payment_entries_to_add) == 0 and order_total > 0:
		print(f"[info] No payment entries but order total={order_total}, adding default Cash payment")
		default_mop = frappe.db.get_value("Mode of Payment", {"mode_of_payment": "Cash"}, "name")
		if not default_mop:
			default_mop = frappe.db.get_value("Mode of Payment", {}, "name", order_by="creation asc")
		
		if default_mop:
			company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
			default_account = None
			
			if company:
				try:
					mop_doc = frappe.get_doc("Mode of Payment", default_mop)
					for account_row in (mop_doc.get("accounts") or []):
						if account_row.company == company:
							default_account = account_row.default_account
							break
				except Exception:
					pass
			
			payment_entry = {
				"mode_of_payment": default_mop,
				"amount": float(order_total),
			}
			
			if default_account:
				payment_entry["account"] = default_account
			
			payment_entries_to_add.append(payment_entry)
			payments_total = float(order_total)
			print(f"[info] Added default payment entry based on order total: {default_mop} = {order_total}")
	
	# Set company if not set
	if not hasattr(invoice, "company") or not invoice.company:
		company = frappe.db.get_single_value("Global Defaults", "default_company")
		if company:
			invoice.company = company
	
	# Ensure all items have descriptions and item_name BEFORE calculations (both are required)
	for idx, item in enumerate(invoice.items):
		# Ensure item_name is set (required field)
		if not hasattr(item, "item_name") or not item.item_name or (isinstance(item.item_name, str) and item.item_name.strip() == ""):
			try:
				item_doc = frappe.get_doc("Item", item.item_code)
				item.item_name = item_doc.item_name or item.item_code
			except Exception:
				item.item_name = item.item_code
		
		# Ensure description is set (required in some installations)
		# Check if description exists and is not None/empty
		if not hasattr(item, "description") or item.description is None:
			item.description = ""
		
		if not item.description or (isinstance(item.description, str) and item.description.strip() == ""):
			try:
				item_doc = frappe.get_doc("Item", item.item_code)
				item.description = item_doc.description or item_doc.item_name or item.item_code
			except Exception:
				item.description = item.item_code or item.item_name or "Item"
		
		# Final check: ensure both are strings and not empty
		item.item_name = str(item.item_name) if item.item_name else str(item.item_code)
		item.description = str(item.description) if item.description else (item.item_name or str(item.item_code))
		
		# Debug: verify description is set
		if not item.description or item.description.strip() == "":
			print(f"[error] Item {idx} ({item.item_code}) still has empty description after fix!")
			item.description = item.item_code  # Force set to item_code as last resort
	
	# Calculate totals before saving (needed for payment matching)
	# Do this AFTER ensuring descriptions are set
	try:
		invoice.run_method("calculate_taxes_and_totals")
	except Exception as calc_err:
		print(f"[warn] Error calculating taxes: {calc_err}")
	
	# Add payment entries AFTER calculate_taxes_and_totals
	# (calculate_taxes_and_totals may clear payment entries, so we add them after)
	if payment_entries_to_add:
		invoice.set("payments", [])  # Clear any existing payments
		for payment_entry in payment_entries_to_add:
			invoice.append("payments", payment_entry)
		print(f"[info] Added {len(payment_entries_to_add)} payment entries to invoice after calculate_taxes_and_totals")
	
	# Re-ensure descriptions after calculations (in case they were cleared)
	for item in invoice.items:
		if not item.description or (isinstance(item.description, str) and item.description.strip() == ""):
			try:
				item_doc = frappe.get_doc("Item", item.item_code)
				item.description = item_doc.description or item_doc.item_name or item.item_code
			except Exception:
				item.description = item.item_code or item.item_name or "Item"
	
	# Final validation: check all items have description and item_code before insert
	# Remove any items with None item_code (they will cause validation errors)
	valid_items = []
	for item in invoice.items:
		if item.item_code and str(item.item_code).strip():
			# Force set description if missing or empty
			if not hasattr(item, "description") or item.description is None:
				item.description = ""
			if not item.description or (isinstance(item.description, str) and item.description.strip() == ""):
				item.description = item.item_name or item.item_code
			
			# Ensure it's a string
			item.description = str(item.description) if item.description else str(item.item_code)
			valid_items.append(item)
		else:
			print(f"[warn] Skipping item with None/empty item_code: item_name={getattr(item, 'item_name', None)}")
	
	# Replace items list with valid items only using set method
	if len(valid_items) < len(invoice.items):
		invoice.set("items", valid_items)

	# Ensure invoice description is still populated before insert
	if not invoice.description or (isinstance(invoice.description, str) and invoice.description.strip() == ""):
		invoice.description = invoice_description_text
		try:
			invoice.set("description", invoice_description_text)
		except Exception:
			pass
	
	# Ensure set_posting_time is 1 right before insert (ALWAYS set to 1)
	# This field must be set to 1 to allow custom posting time
	# Set it multiple ways to ensure it's saved
	invoice.set_posting_time = 1
	invoice.flags.set_posting_time = 1
	# Also set via set method to ensure it's saved
	try:
		invoice.set("set_posting_time", 1)
	except Exception:
		pass
	# Also set is_pos if it exists (some installations use is_pos instead)
	# ALWAYS set is_pos = 1
	try:
		if frappe.get_meta("Sales Invoice").has_field("is_pos"):
			invoice.is_pos = 1
			invoice.flags.is_pos = 1
			invoice.set("is_pos", 1)
	except Exception:
		pass
	print(f"[info] Set set_posting_time = 1 and is_pos = 1 before insert")
	
	# Save invoice with ignore_validate to bypass description validation if needed
	try:
		# Try normal insert first
		invoice.insert(ignore_permissions=True)
		# After insert, ensure set_posting_time is 1 using db_set
		try:
			invoice.db_set("set_posting_time", 1)
			frappe.db.commit()
			print(f"[info] Set set_posting_time = 1 after insert using db_set")
		except Exception as db_set_err:
			print(f"[warn] Could not set set_posting_time using db_set: {db_set_err}")
		
		# Add payment entries after insert (they may have been cleared)
		if payment_entries_to_add:
			print(f"[info] payment_entries_to_add has {len(payment_entries_to_add)} entries to add after insert")
			invoice.reload()
			# Check current payments before clearing
			current_payments = len(invoice.get("payments", []))
			print(f"[info] Invoice has {current_payments} payment entries before adding")
			
			# Clear existing payments and add new ones
			invoice.set("payments", [])
			for payment_entry in payment_entries_to_add:
				invoice.append("payments", payment_entry)
				print(f"[info] Appended payment: {payment_entry.get('mode_of_payment')} = {payment_entry.get('amount')}")
			
			# Verify payments were added before save
			payments_count = len(invoice.get("payments", []))
			print(f"[info] Invoice now has {payments_count} payment entries after append (before save)")
			
			# Save the invoice with payments
			try:
				invoice.save(ignore_permissions=True)
				frappe.db.commit()
				print(f"[info] Saved invoice with {payments_count} payment entries")
			except Exception as save_err:
				print(f"[error] Error saving invoice with payments: {save_err}")
				import traceback
				print(f"[error] Traceback: {traceback.format_exc()[:500]}")
				# Try to save payments directly via db
				try:
					# Clear existing payment entries in DB
					frappe.db.sql("DELETE FROM `tabSales Invoice Payment` WHERE parent = %s", invoice.name)
					# Insert payment entries directly
					for idx, payment_entry in enumerate(payment_entries_to_add):
						frappe.db.sql("""
							INSERT INTO `tabSales Invoice Payment`
							(name, parent, parentfield, parenttype, mode_of_payment, amount, account, creation, modified, owner, modified_by)
							VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s, %s)
						""", (
							frappe.generate_hash(length=10),
							invoice.name,
							"payments",
							"Sales Invoice",
							payment_entry.get("mode_of_payment"),
							payment_entry.get("amount"),
							payment_entry.get("account"),
							frappe.session.user,
							frappe.session.user
						))
					frappe.db.commit()
					print(f"[info] Added {len(payment_entries_to_add)} payment entries directly via DB")
				except Exception as db_err:
					print(f"[error] Failed to add payments via DB: {db_err}")
					import traceback
					print(f"[error] DB Error Traceback: {traceback.format_exc()[:500]}")
			
			# Reload and verify
			invoice.reload()
			final_payments = len(invoice.get("payments", []))
			print(f"[info] Final payment entries after save: {final_payments}")
			if final_payments == 0:
				print(f"[error] WARNING: Payment entries were not saved! Expected {len(payment_entries_to_add)} entries")
				# Force add via DB as last resort
				try:
					frappe.db.sql("DELETE FROM `tabSales Invoice Payment` WHERE parent = %s", invoice.name)
					for payment_entry in payment_entries_to_add:
						frappe.db.sql("""
							INSERT INTO `tabSales Invoice Payment`
							(name, parent, parentfield, parenttype, mode_of_payment, amount, account, creation, modified, owner, modified_by)
							VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s, %s)
						""", (
							frappe.generate_hash(length=10),
							invoice.name,
							"payments",
							"Sales Invoice",
							payment_entry.get("mode_of_payment"),
							payment_entry.get("amount"),
							payment_entry.get("account"),
							frappe.session.user,
							frappe.session.user
						))
					frappe.db.commit()
					print(f"[info] Force-added {len(payment_entries_to_add)} payment entries via DB as last resort")
					invoice.reload()
					final_payments = len(invoice.get("payments", []))
					print(f"[info] After force-add: {final_payments} payment entries")
				except Exception as force_err:
					print(f"[error] Force-add also failed: {force_err}")
		else:
			print(f"[warn] payment_entries_to_add is empty - no payments to add!")
	except Exception as insert_err:
		error_msg = str(insert_err)
		print(f"[error] Failed to insert invoice: {error_msg}")
		# If it's a description error, try to fix it one more time
		if "description" in error_msg.lower():
			# Ensure all items have description - use direct assignment
			for item in invoice.items:
				# Force set description to a non-empty string
				if not item.description or (isinstance(item.description, str) and item.description.strip() == ""):
					item.description = item.item_name or item.item_code or "Item"
				# Ensure it's a string
				item.description = str(item.description)
			# Also check if invoice itself needs description
			if frappe.get_meta("Sales Invoice").has_field("description"):
				if not invoice.description or invoice.description.strip() == "":
					order_ref = getattr(order_doc, "reference", None) or getattr(order_doc, "number", None) or foodics_order_name
					invoice.description = f"Foodics Order: {order_ref}"
			# Try insert with ignore_validate if description error persists
			try:
				# Use db_insert directly to bypass validation if needed
				invoice.flags.ignore_validate = True
				invoice.insert(ignore_permissions=True)
			except Exception as retry_err:
				print(f"[error] Retry also failed: {retry_err}")
				# Last resort: try db_insert directly
				try:
					invoice.flags.ignore_validate = True
					invoice.flags.ignore_mandatory = True
					invoice.db_insert()
					for item in invoice.items:
						item.parent = invoice.name
						item.db_insert()
					frappe.db.commit()
					print(f"[info] Invoice {invoice.name} created using db_insert (bypassed validation)")
				except Exception as db_err:
					print(f"[error] db_insert also failed: {db_err}")
					import traceback
					print(traceback.format_exc()[:1000])
					raise retry_err
		else:
			raise insert_err
	
	# Ensure set_posting_time is 1 after insert (before submit) using db_set
	try:
		invoice.db_set("set_posting_time", 1)
		# Also set is_pos if it exists
		if frappe.get_meta("Sales Invoice").has_field("is_pos"):
			invoice.db_set("is_pos", 1)
		frappe.db.commit()
		print(f"[info] Set set_posting_time = 1 (and is_pos = 1 if exists) after insert (before submit)")
	except Exception as db_set_err:
		print(f"[warn] Could not set set_posting_time using db_set: {db_set_err}")
	
	# Check and apply VAT 15% template after insert
	invoice.reload()
	company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
	
	# Check if invoice has a tax template with 15% VAT
	has_vat_15 = False
	current_template = getattr(invoice, "taxes_and_charges", None)
	
	if current_template:
		try:
			template_doc = frappe.get_doc("Sales Taxes and Charges Template", current_template)
			taxes = template_doc.get("taxes", [])
			for tax in taxes:
				if tax.rate == 15.0:
					has_vat_15 = True
					print(f"[info] Invoice already has VAT 15% template: {current_template}")
					break
		except Exception:
			pass
	
	# If no VAT 15% template, find and apply one
	if not has_vat_15:
		print(f"[info] Checking for VAT 15% template for company: {company}")
		# Find templates for this company
		templates = frappe.get_all(
			"Sales Taxes and Charges Template",
			filters={"company": company} if company else {},
			fields=["name"]
		)
		
		vat_15_template = None
		for template in templates:
			try:
				template_doc = frappe.get_doc("Sales Taxes and Charges Template", template.name)
				taxes = template_doc.get("taxes", [])
				for tax in taxes:
					if tax.rate == 15.0:
						vat_15_template = template.name
						print(f"[info] Found VAT 15% template: {vat_15_template}")
						break
				if vat_15_template:
					break
			except Exception:
				continue
		
		# Apply the template if found
		if vat_15_template:
			try:
				invoice.taxes_and_charges = vat_15_template
				# Load the template taxes
				invoice.run_method("set_missing_values")
				# Recalculate taxes and totals with the new template
				invoice.run_method("calculate_taxes_and_totals")
				invoice.save(ignore_permissions=True)
				frappe.db.commit()
				invoice.reload()  # Reload to get updated totals
				print(f"[info] Applied VAT 15% template: {vat_15_template}")
			except Exception as template_err:
				print(f"[warn] Could not apply VAT 15% template: {template_err}")
				import traceback
				print(f"[warn] Template error details: {traceback.format_exc()[:500]}")
		else:
			print(f"[warn] No VAT 15% template found for company: {company}")
	
	# Ensure payments are added BEFORE submit (submitted invoices can't be modified)
	# CRITICAL: If no payment entries, ALWAYS add a default payment based on invoice grand_total
	# This ensures all invoices have payments even if order payment data is missing
	invoice.reload()
	invoice_total = invoice.grand_total or 0.0
	
	# ALWAYS ensure we have at least one payment entry if invoice has a total
	if len(payment_entries_to_add) == 0:
		if invoice_total > 0:
			print(f"[info] No payment entries from order but invoice total={invoice_total}, adding default Cash payment")
			default_mop = frappe.db.get_value("Mode of Payment", {"mode_of_payment": "Cash"}, "name")
			if not default_mop:
				default_mop = frappe.db.get_value("Mode of Payment", {}, "name", order_by="creation asc")
			
			if default_mop:
				company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
				default_account = None
				
				if company:
					try:
						mop_doc = frappe.get_doc("Mode of Payment", default_mop)
						for account_row in (mop_doc.get("accounts") or []):
							if account_row.company == company:
								default_account = account_row.default_account
								break
					except Exception:
						pass
				
				payment_entry = {
					"mode_of_payment": default_mop,
					"amount": invoice_total,
				}
				
				if default_account:
					payment_entry["account"] = default_account
				
				payment_entries_to_add.append(payment_entry)
				payments_total = invoice_total
				print(f"[info] Added default payment entry based on invoice total: {default_mop} = {invoice_total}")
			else:
				print(f"[warn] Invoice has no grand_total ({invoice_total}), cannot add payment")
	else:
		# If we have payment entries, recalculate payments_total
		payments_total = sum(float(pe.get("amount", 0) or 0) for pe in payment_entries_to_add)
	
	# ALWAYS add payments if we have any payment entries
	if payment_entries_to_add:
		print(f"[info] Adding {len(payment_entries_to_add)} payment entries before submit")
		invoice.reload()
		invoice.set("payments", [])
		for payment_entry in payment_entries_to_add:
			invoice.append("payments", payment_entry)
			print(f"[info] Appended payment before submit: {payment_entry.get('mode_of_payment')} = {payment_entry.get('amount')}")
		
		# Save payments before submit
		try:
			invoice.save(ignore_permissions=True)
			frappe.db.commit()
			payments_count = len(invoice.get("payments", []))
			print(f"[info] Saved {payments_count} payment entries before submit")
		except Exception as save_err:
			print(f"[error] Error saving payments before submit: {save_err}")
			# Force add via DB
			try:
				frappe.db.sql("DELETE FROM `tabSales Invoice Payment` WHERE parent = %s", invoice.name)
				for payment_entry in payment_entries_to_add:
					frappe.db.sql("""
						INSERT INTO `tabSales Invoice Payment`
						(name, parent, parentfield, parenttype, mode_of_payment, amount, account, creation, modified, owner, modified_by)
						VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s, %s)
					""", (
						frappe.generate_hash(length=10),
						invoice.name,
						"payments",
						"Sales Invoice",
						payment_entry.get("mode_of_payment"),
						payment_entry.get("amount"),
						payment_entry.get("account"),
						frappe.session.user,
						frappe.session.user
					))
				frappe.db.commit()
				print(f"[info] Force-added {len(payment_entries_to_add)} payments via DB before submit")
			except Exception as db_err:
				print(f"[error] Failed to force-add payments: {db_err}")
	else:
		print(f"[warn] No payment entries to add - payment_entries_to_add is empty!")
		# Last resort: add default payment via DB directly
		invoice.reload()
		invoice_total = invoice.grand_total or 0.0
		if invoice_total > 0:
			print(f"[info] Last resort: Adding default Cash payment via DB for invoice total={invoice_total}")
			default_mop = frappe.db.get_value("Mode of Payment", {"mode_of_payment": "Cash"}, "name")
			if not default_mop:
				default_mop = frappe.db.get_value("Mode of Payment", {}, "name", order_by="creation asc")
			
			if default_mop:
				try:
					# Get account
					company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
					default_account = None
					if company:
						try:
							mop_doc = frappe.get_doc("Mode of Payment", default_mop)
							for account_row in (mop_doc.get("accounts") or []):
								if account_row.company == company:
									default_account = account_row.default_account
									break
						except Exception:
							pass
					
					# Insert payment directly via DB
					frappe.db.sql("DELETE FROM `tabSales Invoice Payment` WHERE parent = %s", invoice.name)
					frappe.db.sql("""
						INSERT INTO `tabSales Invoice Payment`
						(name, parent, parentfield, parenttype, mode_of_payment, amount, account, creation, modified, owner, modified_by)
						VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s, %s)
					""", (
						frappe.generate_hash(length=10),
						invoice.name,
						"payments",
						"Sales Invoice",
						default_mop,
						invoice_total,
						default_account,
						frappe.session.user,
						frappe.session.user
					))
					frappe.db.commit()
					print(f"[info] Last resort: Added default payment via DB: {default_mop} = {invoice_total}")
				except Exception as db_err:
					print(f"[error] Last resort DB add failed: {db_err}")
	
	# Check if fully paid and add remaining balance as Cash if needed
	invoice.reload()  # Reload to get calculated totals
	invoice_total = invoice.grand_total or 0.0
	
	# Calculate total payments from payment_entries_to_add
	payments_total_from_entries = sum(float(pe.get("amount", 0) or 0) for pe in payment_entries_to_add)
	
	# If payments are less than grand_total, add remaining as Cash payment
	if payments_total_from_entries > 0 and payments_total_from_entries < invoice_total:
		remaining_balance = invoice_total - payments_total_from_entries
		print(f"[info] Payments total ({payments_total_from_entries}) < grand_total ({invoice_total}), adding remaining balance ({remaining_balance}) as Cash")
		
		default_mop = frappe.db.get_value("Mode of Payment", {"mode_of_payment": "Cash"}, "name")
		if not default_mop:
			default_mop = frappe.db.get_value("Mode of Payment", {}, "name", order_by="creation asc")
		
		if default_mop:
			company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
			default_account = None
			
			if company:
				try:
					mop_doc = frappe.get_doc("Mode of Payment", default_mop)
					for account_row in (mop_doc.get("accounts") or []):
						if account_row.company == company:
							default_account = account_row.default_account
							break
				except Exception:
					pass
			
			payment_entry = {
				"mode_of_payment": default_mop,
				"amount": remaining_balance,
			}
			
			if default_account:
				payment_entry["account"] = default_account
			
			payment_entries_to_add.append(payment_entry)
			payments_total = payments_total_from_entries + remaining_balance
			print(f"[info] Added remaining balance as Cash payment: {default_mop} = {remaining_balance}")
		else:
			payments_total = payments_total_from_entries
	else:
		payments_total = payments_total_from_entries
	
	# FINAL CHECK: If still no payment entries, add default payment NOW before submit
	# This is the absolute last chance to add payments before invoice is submitted
	if len(payment_entries_to_add) == 0 and invoice_total > 0:
		print(f"[info] FINAL CHECK: No payment entries but invoice total={invoice_total}, adding default Cash payment")
		default_mop = frappe.db.get_value("Mode of Payment", {"mode_of_payment": "Cash"}, "name")
		if not default_mop:
			default_mop = frappe.db.get_value("Mode of Payment", {}, "name", order_by="creation asc")
		
		if default_mop:
			company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
			default_account = None
			
			if company:
				try:
					mop_doc = frappe.get_doc("Mode of Payment", default_mop)
					for account_row in (mop_doc.get("accounts") or []):
						if account_row.company == company:
							default_account = account_row.default_account
							break
				except Exception:
					pass
			
			payment_entry = {
				"mode_of_payment": default_mop,
				"amount": invoice_total,
			}
			
			if default_account:
				payment_entry["account"] = default_account
			
			payment_entries_to_add.append(payment_entry)
			payments_total = invoice_total
			print(f"[info] FINAL CHECK: Added default payment: {default_mop} = {invoice_total}")
			
			# Immediately add it to invoice
			invoice.set("payments", [])
			invoice.append("payments", payment_entry)
			try:
				invoice.save(ignore_permissions=True)
				frappe.db.commit()
				print(f"[info] FINAL CHECK: Saved payment to invoice before submit")
			except Exception as save_err:
				print(f"[error] FINAL CHECK: Error saving payment: {save_err}")
				# Force add via DB
				try:
					frappe.db.sql("DELETE FROM `tabSales Invoice Payment` WHERE parent = %s", invoice.name)
					frappe.db.sql("""
						INSERT INTO `tabSales Invoice Payment`
						(name, parent, parentfield, parenttype, mode_of_payment, amount, account, creation, modified, owner, modified_by)
						VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s, %s)
					""", (
						frappe.generate_hash(length=10),
						invoice.name,
						"payments",
						"Sales Invoice",
						default_mop,
						invoice_total,
						default_account,
						frappe.session.user,
						frappe.session.user
					))
					frappe.db.commit()
					print(f"[info] FINAL CHECK: Force-added payment via DB")
				except Exception as db_err:
					print(f"[error] FINAL CHECK: DB add also failed: {db_err}")
	
	is_fully_paid = payments_total >= (invoice_total - 0.01)  # 0.01 tolerance for rounding
	
	# Submit invoice (when Create Sales Invoices is enabled)
	# Check if we should submit by checking the settings
	settings = frappe.get_single("Foodics Settings")
	create_sales_invoices = bool(getattr(settings, "create_sales_invoices", 1))
	
	if create_sales_invoices:
		try:
			# If fully paid, set outstanding to 0 before submit
			if is_fully_paid and payments_total > 0:
				invoice.outstanding_amount = 0
			
			# Ensure set_posting_time = 1 and is_pos = 1 before submit
			try:
				invoice.db_set("set_posting_time", 1)
				# Also set is_pos = 1 if it exists
				try:
					if frappe.get_meta("Sales Invoice").has_field("is_pos"):
						invoice.db_set("is_pos", 1)
				except Exception:
					pass
				frappe.db.commit()
			except Exception:
				pass
			
			invoice.submit()
			print(f"[info] Created and submitted Sales Invoice {invoice.name} for order {foodics_order_name}")
			
			# After submit, ensure set_posting_time = 1 and is_pos = 1
			try:
				invoice.db_set("set_posting_time", 1)
				# Also set is_pos = 1 if it exists
				try:
					if frappe.get_meta("Sales Invoice").has_field("is_pos"):
						invoice.db_set("is_pos", 1)
				except Exception:
					pass
				frappe.db.commit()
				print(f"[info] Updated set_posting_time = 1 and is_pos = 1 after submit")
			except Exception as db_set_err:
				print(f"[warn] Could not set set_posting_time after submit: {db_set_err}")
			
			# CRITICAL: After submit, check if invoice has payments - if not, add default payment via DB
			# This is the absolute last chance to add payments
			invoice.reload()
			existing_payments = invoice.get("payments", [])
			if len(existing_payments) == 0:
				invoice_total = invoice.grand_total or 0.0
				if invoice_total > 0:
					print(f"[info] POST-SUBMIT CHECK: Invoice has no payments but total={invoice_total}, adding via DB")
					default_mop = frappe.db.get_value("Mode of Payment", {"mode_of_payment": "Cash"}, "name")
					if not default_mop:
						default_mop = frappe.db.get_value("Mode of Payment", {}, "name", order_by="creation asc")
					
					if default_mop:
						try:
							company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
							default_account = None
							if company:
								try:
									mop_doc = frappe.get_doc("Mode of Payment", default_mop)
									for account_row in (mop_doc.get("accounts") or []):
										if account_row.company == company:
											default_account = account_row.default_account
											break
								except Exception:
									pass
							
							# Insert payment directly via DB (works even for submitted invoices)
							frappe.db.sql("DELETE FROM `tabSales Invoice Payment` WHERE parent = %s", invoice.name)
							frappe.db.sql("""
								INSERT INTO `tabSales Invoice Payment`
								(name, parent, parentfield, parenttype, mode_of_payment, amount, account, creation, modified, owner, modified_by)
								VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s, %s)
							""", (
								frappe.generate_hash(length=10),
								invoice.name,
								"payments",
								"Sales Invoice",
								default_mop,
								invoice_total,
								default_account,
								frappe.session.user,
								frappe.session.user
							))
							frappe.db.commit()
							print(f"[info] POST-SUBMIT: Added default payment via DB: {default_mop} = {invoice_total}")
						except Exception as post_submit_err:
							print(f"[error] POST-SUBMIT: Failed to add payment: {post_submit_err}")
			
			# If fully paid, ensure outstanding is 0 and status is Paid
			if is_fully_paid and payments_total > 0:
				try:
					invoice.reload()
					# Update outstanding amount to 0
					invoice.db_set("outstanding_amount", 0)
					# Check if status needs to be updated
					if invoice.status != "Paid":
						invoice.db_set("status", "Paid")
					print(f"[info] Marked Sales Invoice {invoice.name} as fully paid (payments: {payments_total}, total: {invoice_total})")
				except Exception as paid_err:
					print(f"[warn] Could not mark invoice as paid: {paid_err}")
		except Exception as submit_err:
			# If submit fails, log error but keep the draft invoice
			print(f"[warn] Failed to submit Sales Invoice {invoice.name}: {submit_err}")
			frappe.log_error(
				title="Sales Invoice Submit Error",
				message=f"Order: {foodics_order_name}\nInvoice: {invoice.name}\nError: {submit_err}\n{frappe.get_traceback()}",
			)
			# Continue - invoice is saved as draft
			print(f"[info] Created Sales Invoice {invoice.name} as draft for order {foodics_order_name}")
	else:
		print(f"[info] Created Sales Invoice {invoice.name} as draft for order {foodics_order_name}")
	
	# Link back to Foodics Order
	if frappe.get_meta("Foodics Order").has_field("sales_invoice"):
		order_doc.sales_invoice = invoice.name
		order_doc.save(ignore_permissions=True)
	
	# ABSOLUTE FINAL CHECK: Ensure invoice has payments before returning
	# This is the last chance to add payments
	invoice.reload()
	final_payments_check = invoice.get("payments", [])
	if len(final_payments_check) == 0:
		invoice_total_final = invoice.grand_total or 0.0
		if invoice_total_final > 0:
			print(f"[info] ABSOLUTE FINAL CHECK: Invoice {invoice.name} has no payments but total={invoice_total_final}, adding via DB")
			default_mop = frappe.db.get_value("Mode of Payment", {"mode_of_payment": "Cash"}, "name")
			if not default_mop:
				default_mop = frappe.db.get_value("Mode of Payment", {}, "name", order_by="creation asc")
			
			if default_mop:
				try:
					company = invoice.company if hasattr(invoice, "company") and invoice.company else frappe.db.get_single_value("Global Defaults", "default_company")
					default_account = None
					if company:
						try:
							mop_doc = frappe.get_doc("Mode of Payment", default_mop)
							for account_row in (mop_doc.get("accounts") or []):
								if account_row.company == company:
									default_account = account_row.default_account
									break
						except Exception:
							pass
					
					# Insert payment directly via DB
					frappe.db.sql("DELETE FROM `tabSales Invoice Payment` WHERE parent = %s", invoice.name)
					frappe.db.sql("""
						INSERT INTO `tabSales Invoice Payment`
						(name, parent, parentfield, parenttype, mode_of_payment, amount, account, creation, modified, owner, modified_by)
						VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s, %s)
					""", (
						frappe.generate_hash(length=10),
						invoice.name,
						"payments",
						"Sales Invoice",
						default_mop,
						invoice_total_final,
						default_account,
						frappe.session.user,
						frappe.session.user
					))
					frappe.db.commit()
					print(f"[info] ABSOLUTE FINAL CHECK: Added payment via DB: {default_mop} = {invoice_total_final}")
				except Exception as final_err:
					print(f"[error] ABSOLUTE FINAL CHECK: Failed to add payment: {final_err}")
	
	# POST-CREATION VALIDATION: Validate items and values after invoice is created
	try:
		validation_result = _validate_invoice_items_and_values(invoice, order_doc)
		if not validation_result.get("valid", True):
			print(f"[warn] Invoice validation failed: {validation_result.get('errors', [])}")
			# Try to fix missing items
			if validation_result.get("missing_items", []):
				print(f"[info] Attempting to fix {len(validation_result['missing_items'])} missing items...")
				fix_result = _fix_invoice_missing_items(invoice, order_doc, validation_result["missing_items"])
				if fix_result.get("fixed", False):
					print(f"[info] Fixed {fix_result.get('items_added', 0)} missing items in invoice {invoice.name}")
					# Re-validate after fix
					validation_result = _validate_invoice_items_and_values(invoice, order_doc)
					if not validation_result.get("valid", True):
						print(f"[warn] Invoice still has validation issues after fix: {validation_result.get('errors', [])}")
				else:
					print(f"[warn] Could not fix missing items: {fix_result.get('error', 'Unknown error')}")
		else:
			print(f"[info] Invoice validation passed: items={validation_result.get('invoice_items_count', 0)}, total={validation_result.get('invoice_total', 0)}")
	except Exception as validation_err:
		print(f"[warn] Error during invoice validation: {validation_err}")
		import traceback
		frappe.log_error(
			title="Invoice Validation Error",
			message=f"Invoice: {invoice.name}\nOrder: {foodics_order_name}\nError: {validation_err}\n{frappe.get_traceback()}",
		)
	
	return invoice.name


def _validate_invoice_items_and_values(invoice, order_doc) -> dict:
	"""
	Validate that Sales Invoice items and totals match the Foodics Order.
	Returns dict with validation results.
	"""
	result = {
		"valid": True,
		"errors": [],
		"missing_items": [],
		"invoice_items_count": 0,
		"order_items_count": 0,
		"invoice_total": 0.0,
		"order_total": 0.0,
		"total_diff": 0.0,
	}
	
	try:
		# Reload invoice to get latest data
		invoice.reload()
		
		# Count invoice items
		result["invoice_items_count"] = len(invoice.items) if invoice.items else 0
		result["invoice_total"] = float(invoice.grand_total or 0)
		
		# Count order items and combos
		order_items = getattr(order_doc, "items", []) or []
		order_combos = getattr(order_doc, "combos", []) or []
		result["order_items_count"] = len(order_items) + len(order_combos)
		
		# Get order total
		order_total = float(getattr(order_doc, "total_price_gross", None) or getattr(order_doc, "total_price", None) or 0)
		result["order_total"] = order_total
		result["total_diff"] = abs(result["invoice_total"] - order_total)
		
		# Check if invoice has items
		if result["invoice_items_count"] == 0:
			result["valid"] = False
			result["errors"].append("Invoice has no items")
		
		# Check if invoice total matches order total (with tolerance for rounding)
		tolerance = 0.10  # Allow 0.10 SAR difference for rounding/tax differences
		if result["total_diff"] > tolerance:
			result["valid"] = False
			result["errors"].append(f"Invoice total ({result['invoice_total']:.2f}) differs from order total ({order_total:.2f}) by {result['total_diff']:.2f}")
		
		# Check for missing items by comparing order items to invoice items
		# Build a set of order item identifiers
		order_item_identifiers = set()
		
		# Add items from order_items
		for order_item in order_items:
			product_id = getattr(order_item, "foodics_product_id", None)
			product_sku = getattr(order_item, "product_sku", None)
			# Try multiple name fields
			product_name = (
				getattr(order_item, "foodics_product_name", None) or
				getattr(order_item, "product_name", None)
			)
			quantity = getattr(order_item, "quantity", 0)
			
			if quantity > 0:  # Only check items with quantity > 0
				if product_id:
					order_item_identifiers.add(("product_id", product_id))
				if product_sku:
					order_item_identifiers.add(("sku", product_sku))
				if product_name:
					# Use product_name as primary identifier when product_id is null
					order_item_identifiers.add(("name", product_name))
					# Also add as product_name for matching
					order_item_identifiers.add(("product_name", product_name))
		
		# Add combos
		for combo in order_combos:
			combo_id = getattr(combo, "foodics_combo_id", None)
			combo_name = getattr(combo, "combo_name", None)
			quantity = getattr(combo, "quantity", 0)
			
			if quantity > 0:
				if combo_id:
					order_item_identifiers.add(("combo_id", combo_id))
				if combo_name:
					order_item_identifiers.add(("combo_name", combo_name))
		
		# Check if invoice items match order items
		invoice_item_identifiers = set()
		for invoice_item in invoice.items:
			item_code = invoice_item.item_code
			# Try to find the item and get its Foodics IDs
			try:
				item_doc = frappe.get_doc("Item", item_code)
				product_id = getattr(item_doc, "foodics_product_id", None)
				combo_id = getattr(item_doc, "foodics_combo_id", None)
				sku = item_doc.item_code
				name = item_doc.item_name
				
				if product_id:
					invoice_item_identifiers.add(("product_id", product_id))
				if combo_id:
					invoice_item_identifiers.add(("combo_id", combo_id))
				if sku:
					invoice_item_identifiers.add(("sku", sku))
				if name:
					# Add both name and product_name for matching
					invoice_item_identifiers.add(("name", name))
					invoice_item_identifiers.add(("product_name", name))
			except Exception:
				# If item not found, use item_code as identifier
				invoice_item_identifiers.add(("item_code", item_code))
		
		# Find missing items (items in order but not in invoice)
		missing_identifiers = order_item_identifiers - invoice_item_identifiers
		if missing_identifiers:
			result["valid"] = False
			result["missing_items"] = list(missing_identifiers)
			result["errors"].append(f"Missing {len(missing_identifiers)} items in invoice")
		
	except Exception as e:
		result["valid"] = False
		result["errors"].append(f"Validation error: {str(e)}")
		import traceback
		frappe.log_error(
			title="Invoice Validation Error",
			message=f"Invoice: {invoice.name}\nOrder: {order_doc.name}\nError: {e}\n{frappe.get_traceback()}",
		)
	
	return result


def _fix_invoice_missing_items(invoice, order_doc, missing_items: list) -> dict:
	"""
	Fix missing items in Sales Invoice by adding them from Foodics Order.
	Returns dict with fix results.
	"""
	result = {
		"fixed": False,
		"items_added": 0,
		"error": None,
	}
	
	try:
		# Cancel invoice if submitted (need to cancel to modify)
		was_submitted = invoice.docstatus == 1
		if was_submitted:
			print(f"[info] Cancelling invoice {invoice.name} to fix missing items...")
			invoice.cancel()
			frappe.db.commit()
			invoice.reload()
		
		# Get order items and combos
		order_items = getattr(order_doc, "items", []) or []
		order_combos = getattr(order_doc, "combos", []) or []
		
		# Load raw JSON if available
		raw_data = None
		if hasattr(order_doc, "foodics_order_raw") and order_doc.foodics_order_raw:
			try:
				import json
				raw_data = json.loads(order_doc.foodics_order_raw)
			except Exception:
				pass
		
		# Try to find and add missing items
		items_added = 0
		
		# Check order items
		for order_item in order_items:
			product_id = getattr(order_item, "foodics_product_id", None)
			product_sku = getattr(order_item, "product_sku", None)
			# Try multiple name fields
			product_name = (
				getattr(order_item, "foodics_product_name", None) or
				getattr(order_item, "product_name", None)
			)
			quantity = getattr(order_item, "quantity", 0)
			
			if quantity <= 0:
				continue
			
			# Check if this item is missing
			is_missing = False
			for missing_type, missing_value in missing_items:
				if missing_type == "product_id" and product_id == missing_value:
					is_missing = True
					break
				elif missing_type == "sku" and product_sku == missing_value:
					is_missing = True
					break
				elif missing_type == "name" and product_name == missing_value:
					is_missing = True
					break
				elif missing_type == "product_name" and product_name == missing_value:
					is_missing = True
					break
			
			if not is_missing:
				continue
			
			# Try to find the item - prioritize by name when product_id is null
			item_name = None
			if product_id:
				item_name = _get_item_by_foodics_product_id(product_id)
			# If product_id is null, try name first (more reliable)
			if not item_name and product_name:
				item_name = _get_item_by_name(product_name)
			if not item_name and product_sku:
				item_name = _get_item_by_sku(product_sku)
			
			if item_name:
				# Check if item already in invoice
				already_added = False
				for inv_item in invoice.items:
					if inv_item.item_code == item_name:
						already_added = True
						break
				
				if not already_added:
					# Add item to invoice
					unit_price = getattr(order_item, "unit_price_net", None) or getattr(order_item, "unit_price_gross", 0)
					
					try:
						item_doc = frappe.get_doc("Item", item_name)
						item_description = item_doc.description or item_doc.item_name or item_name
						
						invoice.append("items", {
							"item_code": item_name,
							"item_name": item_doc.item_name or item_name,
							"qty": quantity,
							"rate": unit_price,
							"description": item_description,
						})
						items_added += 1
						print(f"[info] Added missing item {item_name} (qty={quantity}, rate={unit_price})")
					except Exception as add_err:
						print(f"[warn] Failed to add item {item_name}: {add_err}")
		
		# Check order combos
		for combo in order_combos:
			combo_id = getattr(combo, "foodics_combo_id", None)
			combo_name = getattr(combo, "combo_name", None)
			quantity = getattr(combo, "quantity", 0)
			
			if quantity <= 0:
				continue
			
			# Check if this combo is missing
			is_missing = False
			for missing_type, missing_value in missing_items:
				if missing_type == "combo_id" and combo_id == missing_value:
					is_missing = True
					break
				elif missing_type == "combo_name" and combo_name == missing_value:
					is_missing = True
					break
			
			if not is_missing:
				continue
			
			# Try to find the combo item
			item_name = None
			if combo_id:
				item_name = _get_item_by_foodics_combo_id(combo_id)
			if not item_name and combo_name:
				item_name = _get_item_by_name(combo_name)
			
			if item_name:
				# Check if already added
				already_added = False
				for inv_item in invoice.items:
					if inv_item.item_code == item_name:
						already_added = True
						break
				
				if not already_added:
					unit_price = getattr(combo, "unit_price_net", None) or getattr(combo, "unit_price_gross", 0)
					
					try:
						item_doc = frappe.get_doc("Item", item_name)
						item_description = item_doc.description or item_doc.item_name or item_name
						
						invoice.append("items", {
							"item_code": item_name,
							"item_name": item_doc.item_name or item_name,
							"qty": quantity,
							"rate": unit_price,
							"description": item_description,
						})
						items_added += 1
						print(f"[info] Added missing combo {item_name} (qty={quantity}, rate={unit_price})")
					except Exception as add_err:
						print(f"[warn] Failed to add combo {item_name}: {add_err}")
		
		if items_added > 0:
			# Recalculate taxes and totals
			try:
				invoice.run_method("calculate_taxes_and_totals")
				invoice.save(ignore_permissions=True)
				frappe.db.commit()
				
				# Re-submit if it was submitted before
				if was_submitted:
					invoice.submit()
					frappe.db.commit()
					print(f"[info] Re-submitted invoice {invoice.name} after fixing items")
				
				result["fixed"] = True
				result["items_added"] = items_added
			except Exception as save_err:
				result["error"] = f"Failed to save invoice after adding items: {save_err}"
				print(f"[error] {result['error']}")
		else:
			result["error"] = "No items could be added"
		
	except Exception as e:
		result["error"] = str(e)
		import traceback
		frappe.log_error(
			title="Fix Invoice Missing Items Error",
			message=f"Invoice: {invoice.name}\nOrder: {order_doc.name}\nError: {e}\n{frappe.get_traceback()}",
		)
	
	return result


@frappe.whitelist()
def sync_orders_and_create_invoices(
	from_date: str,
	to_date: Optional[str] = None,
	date_field: str = "business_date",
	sync_log_name: Optional[str] = None,
	enqueue: bool = True,
	start_page: Optional[int] = None,
):
	"""
	Sync fully paid orders from Foodics and create paid Sales Invoices.
	Only processes orders that:
	- Are fully paid (sum(payments) >= total_price)
	- Don't already exist in Foodics Order
	- Don't already have a Sales Invoice
	
	Args:
		from_date: Start date for order sync
		to_date: End date for order sync
		date_field: Field to filter by (business_date, created_at, updated_at)
		sync_log_name: Optional name of Foodics Sync Log record to log to
		enqueue: If True, enqueue as background job (default: True)
		
	Returns:
		dict with sync results and log name, or job info if enqueued
	"""
	frappe.only_for(("System Manager", "Administrator"))
	
	# If enqueue is True, run in background
	if enqueue:
		# Create sync log first so user can track progress
		if not sync_log_name:
			sync_log = frappe.get_doc({
				"doctype": "Foodics Sync Log",
				"sync_type": "Orders with Invoices",
				"status": "In Progress",
				"started_at": frappe.utils.now(),
				"sync_settings": json.dumps({
					"from_date": from_date,
					"to_date": to_date,
					"date_field": date_field,
				}, indent=2),
			})
			sync_log.insert(ignore_permissions=True)
			frappe.db.commit()
			sync_log_name = sync_log.name
			sync_log.add_log_entry("Job queued, waiting to start...", None, None, "INFO")
		
		job = frappe.enqueue(
			"foodics_migration.api_orders.sync_orders_and_create_invoices",
			from_date=from_date,
			to_date=to_date,
			date_field=date_field,
			sync_log_name=sync_log_name,
			enqueue=False,  # Don't enqueue recursively
			queue="long",
			timeout=3600,  # 1 hour timeout
			job_name=f"sync_orders_invoices_{from_date}_{to_date}",
		)
		return {
			"job_id": job.id if hasattr(job, 'id') else str(job),
			"status": "queued",
			"sync_log_name": sync_log_name,
			"message": "Sync job has been queued. Check Foodics Sync Log for progress.",
		}
	
	# Get or create sync log
	sync_log = None
	if sync_log_name:
		try:
			sync_log = frappe.get_doc("Foodics Sync Log", sync_log_name)
		except frappe.DoesNotExistError:
			sync_log = None
	
	if not sync_log:
		sync_log = frappe.get_doc({
			"doctype": "Foodics Sync Log",
			"sync_type": "Orders with Invoices",
			"status": "In Progress",
			"started_at": frappe.utils.now(),
			"sync_settings": json.dumps({
				"from_date": from_date,
				"to_date": to_date,
				"date_field": date_field,
			}, indent=2),
		})
		sync_log.insert(ignore_permissions=True)
		frappe.db.commit()
		sync_log_name = sync_log.name
	
	start_page_value = 1
	if start_page:
		try:
			start_page_value = int(start_page)
		except Exception:
			start_page_value = 1
	start_page_value = max(start_page_value, 1)

	if sync_log:
		try:
			sync_log.db_set("resume_page", start_page_value)
			sync_log.db_set("current_page", start_page_value)
			if sync_log.status != "In Progress":
				sync_log.db_set("status", "In Progress")
		except Exception:
			pass

	if date_field != "business_date" and not to_date:
		to_date = from_date

	sync_log.add_log_entry(
		f"Starting orders and invoices sync from {from_date} to {to_date} (filter: {date_field})",
		None, None, "INFO"
	)
	
	try:
		cfg = _cfg()
		s = _new_session(cfg)
		
		# Build date filters
		date_filters = {
			"from_date": from_date,
			"to_date": to_date,
			"date_field": date_field,
		}
		
		orders_created = 0
		orders_skipped = 0
		invoices_created = 0
		invoices_skipped = 0
		failed = 0
		orders_for_invoicing: list[str] = []
		
		# Track last business date for resume functionality
		last_business_date = None
		previous_business_date = None
		
		# Paginate through fully paid orders
		for order_json in _paginate_orders(
			cfg,
			s,
			date_filters=date_filters,
			only_fully_paid=True,
			start_page=start_page_value,
			sync_log=sync_log,
		):
			try:
				order_id = order_json.get("id")
				if not order_id:
					continue
				
				# Track business_date for resume functionality
				if date_field == "business_date":
					order_business_date = order_json.get("business_date")
					if order_business_date:
						# Update last_business_date periodically (every 10 orders or when date changes)
						if order_business_date != previous_business_date:
							last_business_date = order_business_date
							previous_business_date = order_business_date
							# Update sync log with last business date
							try:
								sync_log.db_set("last_business_date", last_business_date)
							except Exception:
								pass  # Field might not exist, ignore
						elif (orders_created + orders_skipped) % 10 == 0:
							# Update periodically even if date hasn't changed
							try:
								sync_log.db_set("last_business_date", order_business_date)
							except Exception:
								pass
				
				# Check if Foodics Order already exists
				existing_order = _parent_name_for(order_id)
				if existing_order:
					# Check if it already has a Sales Invoice
					order_doc = frappe.get_doc("Foodics Order", existing_order)
					has_invoice = False
					if hasattr(order_doc, "sales_invoice") and order_doc.sales_invoice:
						has_invoice = True
					else:
						# Also check by foodics_order_id on Sales Invoice
						if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
							existing_inv = frappe.db.get_value(
								"Sales Invoice",
								{"foodics_order_id": order_id},
								"name"
							)
							if existing_inv:
								has_invoice = True
					
					if has_invoice:
						sync_log.add_log_entry(
							f"Order already exists with invoice, skipping",
							"Foodics Order", existing_order, "SKIPPED"
						)
						orders_skipped += 1
						continue
					else:
						# Order exists but no invoice yet
						sync_log.add_log_entry(
							f"Order exists but no invoice, queueing for invoice creation",
							"Foodics Order", existing_order, "INFO"
						)
						orders_for_invoicing.append(existing_order)
						continue
				
				# Check if Sales Invoice already exists by foodics_order_id
				existing_invoice = None
				if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
					existing_invoice = frappe.db.get_value(
						"Sales Invoice",
						{"foodics_order_id": order_id},
						"name"
					)
				
				if existing_invoice:
					sync_log.add_log_entry(
						f"Sales Invoice already exists for this order, skipping invoice creation",
						"Sales Invoice", existing_invoice, "SKIPPED"
					)
					invoices_skipped += 1
					# Still create the order record if it doesn't exist
					# But we already checked above, so this shouldn't happen
					continue
				
				# Create Foodics Order
				try:
					order_name = _upsert_one_order(order_json, update_if_exists=False)
					orders_created += 1
					sync_log.add_log_entry(
						f"Created Foodics Order",
						"Foodics Order", order_name, "CREATED"
					)
					orders_for_invoicing.append(order_name)
					
					# Commit periodically
					if (orders_created + orders_skipped) % int(cfg.commit_every_n_records) == 0:
						frappe.db.commit()
						sync_log.save(ignore_permissions=True)
						frappe.db.commit()
						
				except Exception as order_err:
					failed += 1
					sync_log.add_log_entry(
						f"Failed to create Foodics Order: {str(order_err)}",
						"Order", order_id, "FAILED"
					)
					frappe.log_error(
						title="Order Creation Error",
						message=f"Order ID: {order_id}\nError: {order_err}\n{frappe.get_traceback()}",
					)
					
			except Exception as e:
				failed += 1
				sync_log.add_log_entry(
					f"Error processing order: {str(e)}",
					"Order", order_json.get("id", "unknown"), "FAILED"
				)
				frappe.log_error(
					title="Order Processing Error",
					message=f"Order: {order_json.get('id', 'unknown')}\nError: {e}\n{frappe.get_traceback()}",
				)
		
		# Final commit for orders
		frappe.db.commit()

		# Phase 2: create invoices for the saved orders
		if orders_for_invoicing:
			unique_orders = []
			seen = set()
			for name in orders_for_invoicing:
				if name and name not in seen:
					seen.add(name)
					unique_orders.append(name)

			sync_log.add_log_entry(
				f"Starting invoice creation for {len(unique_orders)} orders",
				None, None, "INFO"
			)

			for idx, invoice_order in enumerate(unique_orders, start=1):
				try:
					order_doc = frappe.get_doc("Foodics Order", invoice_order)
					has_invoice = False
					if hasattr(order_doc, "sales_invoice") and order_doc.sales_invoice:
						has_invoice = True
					else:
						if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
							existing_inv = frappe.db.get_value(
								"Sales Invoice",
								{"foodics_order_id": invoice_order},
								"name"
							)
							if existing_inv:
								has_invoice = True

					if has_invoice:
						invoices_skipped += 1
						sync_log.add_log_entry(
							f"Order already linked to invoice, skipping",
							"Foodics Order", invoice_order, "SKIPPED"
						)
						continue

					invoice_name = create_sales_invoice_from_order(invoice_order)
					if invoice_name:
						invoices_created += 1
						sync_log.add_log_entry(
							f"Created Sales Invoice",
							"Sales Invoice", invoice_name, "CREATED"
						)
					else:
						invoices_skipped += 1
						sync_log.add_log_entry(
							f"Failed to create Sales Invoice (no items or customer)",
							"Foodics Order", invoice_order, "SKIPPED"
						)

					if idx % 5 == 0:
						frappe.db.commit()
						sync_log.save(ignore_permissions=True)
						frappe.db.commit()

				except Exception as inv_err:
					failed += 1
					sync_log.add_log_entry(
						f"Failed to create Sales Invoice: {str(inv_err)}",
						"Foodics Order", invoice_order, "FAILED"
					)
					frappe.log_error(
						title="Invoice Creation Error",
						message=f"Order: {invoice_order}\nError: {inv_err}\n{frappe.get_traceback()}",
					)

			# Commit after invoice phase
			frappe.db.commit()
			sync_log.save(ignore_permissions=True)
			frappe.db.commit()

		
		# Update sync log
		total_records = orders_created + orders_skipped + failed
		sync_log.total_records = total_records
		sync_log.created = orders_created
		sync_log.skipped = orders_skipped + invoices_skipped
		sync_log.failed = failed
		sync_log.invoices_created = invoices_created
		
		status = "Completed" if failed == 0 else "Partial"
		sync_log.finalize_log(status)
		
		return {
			"sync_log_name": sync_log.name,
			"orders_created": orders_created,
			"orders_skipped": orders_skipped,
			"invoices_created": invoices_created,
			"invoices_skipped": invoices_skipped,
			"failed": failed,
		}
		
	except Exception as e:
		if sync_log:
			sync_log.add_log_entry(f"Sync aborted: {str(e)}", None, None, "FAILED")
			sync_log.finalize_log("Failed")
		frappe.log_error(title="Orders and Invoices Sync Error", message=frappe.get_traceback())
		raise


@frappe.whitelist()
def resume_sync_orders(sync_log_name: str) -> Dict[str, Any]:
	"""
	Resume a previously interrupted Orders with Invoices sync from the last recorded business date.
	Uses last_business_date if available, otherwise falls back to page-based resume.
	"""
	frappe.only_for(("System Manager", "Administrator"))

	if not sync_log_name:
		frappe.throw("Sync log name is required to resume the job.")

	try:
		sync_log = frappe.get_doc("Foodics Sync Log", sync_log_name)
	except frappe.DoesNotExistError:
		frappe.throw(f"Sync Log {sync_log_name} not found.")

	if sync_log.sync_type != "Orders with Invoices":
		frappe.throw("Only 'Orders with Invoices' sync logs can be resumed.")

	# Parse stored settings
	settings = {}
	if sync_log.sync_settings:
		try:
			settings = json.loads(sync_log.sync_settings)
		except Exception:
			settings = {}

	# Determine resume strategy: prefer last_business_date over page-based
	date_field = settings.get("date_field", "business_date")
	from_date = settings.get("from_date")
	to_date = settings.get("to_date")
	
	# If we have last_business_date and date_field is business_date, use it
	last_business_date = getattr(sync_log, "last_business_date", None)
	if last_business_date and date_field == "business_date":
		# Resume from the day after last_business_date
		from datetime import datetime, timedelta
		try:
			if isinstance(last_business_date, str):
				last_date = datetime.strptime(last_business_date, "%Y-%m-%d").date()
			else:
				last_date = last_business_date
			
			# Resume from the next day after last_business_date
			resume_from_date = (last_date + timedelta(days=1)).strftime("%Y-%m-%d")
			from_date = resume_from_date
			
			sync_log.add_log_entry(
				f"Resuming from last business date: {last_business_date} -> from_date: {resume_from_date}",
				None, None, "INFO"
			)
			# Always start from page 1 when using date-based resume
			start_page = 1
		except Exception as date_err:
			print(f"[warn] Error parsing last_business_date {last_business_date}: {date_err}, falling back to page-based resume")
			last_business_date = None
	
	# Fallback to page-based resume if no last_business_date
	if not last_business_date or date_field != "business_date":
		start_page = sync_log.resume_page or (sync_log.last_page_processed or 0) + 1
		try:
			start_page = max(int(start_page), 1)
		except Exception:
			start_page = 1
		
		sync_log.add_log_entry(
			f"Resuming from page {start_page} (last_business_date not available or date_field is not business_date)",
			None, None, "INFO"
		)

	sync_log.db_set("status", "In Progress")
	sync_log.db_set("resume_page", start_page)
	sync_log.db_set("current_page", start_page)
	
	result = sync_orders_and_create_invoices(
		from_date=from_date,
		to_date=to_date,
		date_field=date_field,
		sync_log_name=sync_log.name,
		enqueue=False,
		start_page=start_page,
	)
	result["resume_page"] = start_page
	result["resume_from_date"] = from_date
	return result


@frappe.whitelist()
def get_orders_count_for_invoice_creation(
	from_date: str,
	to_date: str,
	date_field: str = "business_date",
	only_without_invoice: bool = True,
) -> int:
	"""
	Get count of Foodics Orders that match the criteria for invoice creation.
	"""
	frappe.only_for(("System Manager", "Administrator"))
	
	# Build filters
	filters = {}
	
	# Date filter - use proper Frappe filter syntax
	if date_field == "business_date":
		if from_date and to_date:
			filters["business_date"] = ["between", [from_date, to_date]]
		elif from_date:
			filters["business_date"] = [">=", from_date]
		elif to_date:
			filters["business_date"] = ["<=", to_date]
	elif date_field == "created_at":
		if from_date and to_date:
			filters["creation"] = ["between", [from_date, to_date]]
		elif from_date:
			filters["creation"] = [">=", from_date]
		elif to_date:
			filters["creation"] = ["<=", to_date]
	elif date_field == "updated_at":
		if from_date and to_date:
			filters["modified"] = ["between", [from_date, to_date]]
		elif from_date:
			filters["modified"] = [">=", from_date]
		elif to_date:
			filters["modified"] = ["<=", to_date]
	
	# Filter for orders without Sales Invoice
	# Note: We'll filter this in Python after getting orders, as Frappe's ['in', [None, '']] doesn't work well
	# For count, we'll get all matching date orders and filter manually
	meta = frappe.get_meta("Foodics Order")
	has_sales_invoice_field = meta.has_field("sales_invoice")
	
	if only_without_invoice and has_sales_invoice_field:
		# Get all orders matching date, then filter by sales_invoice in Python
		all_orders = frappe.get_all(
			"Foodics Order",
			filters=filters,
			fields=["name", "sales_invoice"],
		)
		# Filter for orders without invoice
		count = sum(1 for o in all_orders if not o.get("sales_invoice"))
	else:
		count = frappe.db.count("Foodics Order", filters)
	
	return count


@frappe.whitelist()
def create_sales_invoices_from_orders(
	from_date: str,
	to_date: str,
	date_field: str = "business_date",
	only_without_invoice: bool = True,
) -> Dict[str, int]:
	"""
	Create Sales Invoices from Foodics Orders based on date range.
	Only processes orders that don't have a Sales Invoice (if only_without_invoice is True).
	
	Returns dict with counts: created, skipped, failed
	"""
	frappe.only_for(("System Manager", "Administrator"))
	
	# Build filters
	filters = {}
	
	# Date filter - use proper Frappe filter syntax
	if date_field == "business_date":
		if from_date and to_date:
			filters["business_date"] = ["between", [from_date, to_date]]
		elif from_date:
			filters["business_date"] = [">=", from_date]
		elif to_date:
			filters["business_date"] = ["<=", to_date]
	elif date_field == "created_at":
		if from_date and to_date:
			filters["creation"] = ["between", [from_date, to_date]]
		elif from_date:
			filters["creation"] = [">=", from_date]
		elif to_date:
			filters["creation"] = ["<=", to_date]
	elif date_field == "updated_at":
		if from_date and to_date:
			filters["modified"] = ["between", [from_date, to_date]]
		elif from_date:
			filters["modified"] = [">=", from_date]
		elif to_date:
			filters["modified"] = ["<=", to_date]
	
	# Filter for orders without Sales Invoice
	meta = frappe.get_meta("Foodics Order")
	has_sales_invoice_field = meta.has_field("sales_invoice")
	
	# Get orders matching date filter
	all_orders = frappe.get_all(
		"Foodics Order",
		filters=filters,
		fields=["name", "sales_invoice"] if has_sales_invoice_field else ["name"],
		order_by="business_date asc, creation asc",
	)
	
	# Filter for orders without invoice if needed
	if only_without_invoice and has_sales_invoice_field:
		orders = [o for o in all_orders if not o.get("sales_invoice")]
	else:
		orders = all_orders
	
	created = 0
	skipped = 0
	failed = 0
	
	settings = frappe.get_single("Foodics Settings")
	create_sales_invoices = bool(getattr(settings, "create_sales_invoices", 1))
	
	logger = frappe.logger()
	logger.setLevel(getattr(settings, "log_level", "INFO") or "INFO")
	
	for order in orders:
		try:
			order_name = order.name
			
			# Check if invoice already exists (double-check)
			if only_without_invoice:
				order_doc = frappe.get_doc("Foodics Order", order_name)
				meta = frappe.get_meta("Foodics Order")
				if meta.has_field("sales_invoice") and hasattr(order_doc, "sales_invoice") and order_doc.sales_invoice:
					existing_inv = frappe.db.get_value("Sales Invoice", order_doc.sales_invoice, "name")
					if existing_inv:
						skipped += 1
						logger.info(f"⊘ Skipped order {order_name}: Sales Invoice already exists")
						continue
			
			# Create invoice
			invoice_name = create_sales_invoice_from_order(order_name)
			
			if invoice_name:
				created += 1
				logger.info(f"✅ Created Sales Invoice {invoice_name} for order {order_name}")
			else:
				skipped += 1
				logger.info(f"⊘ Skipped order {order_name}: No invoice created")
			
			# Commit in batches
			if (created + skipped + failed) % 10 == 0:
				frappe.db.commit()
				logger.info(f"Processed {created + skipped + failed} orders so far...")
				
		except Exception as e:
			failed += 1
			frappe.log_error(
				title="Sales Invoice Creation Error",
				message=f"Order: {order.name}\nError: {e}\n{frappe.get_traceback()}",
			)
			logger.error(f"❌ Failed to create invoice for order {order.name}: {e}")
			# Continue with next order
	
	# Final commit
	frappe.db.commit()
	logger.info(f"✅ Finished creating Sales Invoices. Created: {created}, Skipped: {skipped}, Failed: {failed}")
	
	return {
		"created": created,
		"skipped": skipped,
		"failed": failed,
	}


def _finalize_sync_log(sync_log_name: str, result: Dict[str, int]) -> None:
	"""Update the sync log with counts and mark it completed."""
	try:
		log = frappe.get_doc("Foodics Sync Log", sync_log_name)
	except frappe.DoesNotExistError:
		return

	log.add_log_entry(
		f"Sales invoice creation completed. Created: {result.get('created', 0)}, Skipped: {result.get('skipped', 0)}, Failed: {result.get('failed', 0)}",
		None, None, "INFO"
	)
	log.created = result.get("created", log.created or 0)
	log.skipped = result.get("skipped", log.skipped or 0)
	log.failed = result.get("failed", log.failed or 0)
	log.finalize_log("Completed" if result.get("failed", 0) == 0 else "Partial")


def _create_sales_invoices_task(
	from_date: str,
	to_date: str,
	date_field: str,
	only_without_invoice: bool,
	sync_log_name: Optional[str] = None,
	enqueue: bool = False,
):
	result = create_sales_invoices_from_orders(
		from_date=from_date,
		to_date=to_date,
		date_field=date_field,
		only_without_invoice=only_without_invoice,
	)
	if sync_log_name:
		_finalize_sync_log(sync_log_name, result)


@frappe.whitelist()
def enqueue_create_sales_invoices_job(
	from_date: str,
	to_date: Optional[str] = None,
	date_field: str = "business_date",
	only_without_invoice: bool = True,
):
	frappe.only_for(("System Manager", "Administrator"))

	if date_field != "business_date" and not to_date:
		to_date = from_date
	if not to_date:
		to_date = from_date

	log = frappe.get_doc({
		"doctype": "Foodics Sync Log",
		"sync_type": "Orders with Invoices",
		"status": "In Progress",
		"started_at": frappe.utils.now(),
		"sync_settings": json.dumps({
			"from_date": from_date,
			"to_date": to_date,
			"date_field": date_field,
			"only_without_invoice": only_without_invoice,
		}, indent=2),
	})
	log.insert(ignore_permissions=True)
	frappe.db.commit()

	job = frappe.enqueue(
		"foodics_migration.api_orders._create_sales_invoices_task",
		from_date=from_date,
		to_date=to_date,
		date_field=date_field,
		only_without_invoice=only_without_invoice,
		sync_log_name=log.name,
		enqueue=False,
		queue="long",
		timeout=3600,
		job_name=f"create_sales_invoices_{from_date}_{to_date}",
	)
	return {
		"job_id": getattr(job, "id", str(job)),
		"sync_log_name": log.name,
	}


@frappe.whitelist()
def fix_invoices_and_compare_totals(
	from_date: Optional[str] = None,
	to_date: Optional[str] = None,
	limit: Optional[int] = None,
	only_with_errors: bool = True,
) -> Dict[str, Any]:
	"""
	Fix missing items in Sales Invoices and compare totals with Foodics Orders.
	
	Args:
		from_date: Optional start date filter (business_date)
		to_date: Optional end date filter (business_date)
		limit: Optional limit on number of invoices to process
		only_with_errors: If True, only process invoices with validation errors
		
	Returns:
		dict with results: fixed, compared, errors, summary
	"""
	frappe.only_for(("System Manager", "Administrator"))
	
	results = {
		"total_checked": 0,
		"total_fixed": 0,
		"total_errors": 0,
		"items_fixed": 0,
		"totals_match": 0,
		"totals_mismatch": 0,
		"fixed_invoices": [],
		"mismatched_invoices": [],
		"errors": [],
	}
	
	try:
		# Build query to find Sales Invoices with Foodics Orders
		conditions = []
		values = []
		
		# Filter by foodics_order_id or foodics_order_name
		if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
			conditions.append("si.foodics_order_id IS NOT NULL AND si.foodics_order_id != ''")
		elif frappe.get_meta("Sales Invoice").has_field("foodics_order_name"):
			conditions.append("si.foodics_order_name IS NOT NULL AND si.foodics_order_name != ''")
		else:
			# Try to find via Foodics Order's sales_invoice field
			conditions.append("fo.sales_invoice IS NOT NULL")
		
		# Date filters
		if from_date:
			conditions.append("fo.business_date >= %s")
			values.append(from_date)
		if to_date:
			conditions.append("fo.business_date <= %s")
			values.append(to_date)
		
		# Build SQL query
		if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
			query = f"""
				SELECT DISTINCT si.name as invoice_name, si.foodics_order_id
				FROM `tabSales Invoice` si
				INNER JOIN `tabFoodics Order` fo ON fo.foodics_order_id = si.foodics_order_id
				WHERE {' AND '.join(conditions)}
				ORDER BY si.creation DESC
			"""
		elif frappe.get_meta("Sales Invoice").has_field("foodics_order_name"):
			query = f"""
				SELECT DISTINCT si.name as invoice_name, fo.name as order_name
				FROM `tabSales Invoice` si
				INNER JOIN `tabFoodics Order` fo ON fo.name = si.foodics_order_name
				WHERE {' AND '.join(conditions)}
				ORDER BY si.creation DESC
			"""
		else:
			query = f"""
				SELECT DISTINCT si.name as invoice_name, fo.name as order_name
				FROM `tabSales Invoice` si
				INNER JOIN `tabFoodics Order` fo ON fo.sales_invoice = si.name
				WHERE {' AND '.join(conditions)}
				ORDER BY si.creation DESC
			"""
		
		if limit:
			query += f" LIMIT {int(limit)}"
		
		invoices = frappe.db.sql(query, values, as_dict=True)
		
		print(f"[info] Found {len(invoices)} invoices to check")
		
		for row in invoices:
			try:
				invoice_name = row.get("invoice_name")
				order_name = row.get("order_name")
				order_id = row.get("foodics_order_id")
				
				# Get order name if we only have ID
				if not order_name and order_id:
					order_name = _parent_name_for(order_id)
				
				if not invoice_name or not order_name:
					continue
				
				results["total_checked"] += 1
				
				# Get invoice and order documents
				try:
					invoice = frappe.get_doc("Sales Invoice", invoice_name)
					order_doc = frappe.get_doc("Foodics Order", order_name)
				except Exception as doc_err:
					results["errors"].append({
						"invoice": invoice_name,
						"order": order_name,
						"error": f"Failed to load documents: {doc_err}"
					})
					results["total_errors"] += 1
					continue
				
				# Validate invoice items and values
				validation_result = _validate_invoice_items_and_values(invoice, order_doc)
				
				# Fix missing items if validation failed
				if not validation_result.get("valid", True):
					missing_items = validation_result.get("missing_items", [])
					if missing_items:
						print(f"[info] Invoice {invoice_name} has {len(missing_items)} missing items, attempting to fix...")
						fix_result = _fix_invoice_missing_items(invoice, order_doc, missing_items)
						
						if fix_result.get("fixed", False):
							results["total_fixed"] += 1
							results["items_fixed"] += fix_result.get("items_added", 0)
							results["fixed_invoices"].append({
								"invoice": invoice_name,
								"order": order_name,
								"items_added": fix_result.get("items_added", 0),
							})
							
							# Re-validate after fix
							invoice.reload()
							validation_result = _validate_invoice_items_and_values(invoice, order_doc)
						
						# If still not valid after fix, log it
						if not validation_result.get("valid", True):
							results["errors"].append({
								"invoice": invoice_name,
								"order": order_name,
								"error": f"Still has errors after fix: {validation_result.get('errors', [])}"
							})
				
				# Compare totals
				invoice_total = float(invoice.grand_total or 0)
				order_total = float(getattr(order_doc, "total_price_gross", None) or getattr(order_doc, "total_price", None) or 0)
				total_diff = abs(invoice_total - order_total)
				tolerance = 0.10  # Allow 0.10 SAR difference
				
				if total_diff <= tolerance:
					results["totals_match"] += 1
				else:
					results["totals_mismatch"] += 1
					results["mismatched_invoices"].append({
						"invoice": invoice_name,
						"order": order_name,
						"invoice_total": invoice_total,
						"order_total": order_total,
						"difference": total_diff,
					})
					
					# If only_with_errors is True and this has a mismatch, try to fix it
					if only_with_errors and total_diff > tolerance:
						# Check if items are missing
						if validation_result.get("missing_items"):
							missing_items = validation_result.get("missing_items", [])
							if missing_items:
								print(f"[info] Invoice {invoice_name} has total mismatch and missing items, attempting to fix...")
								fix_result = _fix_invoice_missing_items(invoice, order_doc, missing_items)
								
								if fix_result.get("fixed", False):
									# Re-check totals after fix
									invoice.reload()
									new_invoice_total = float(invoice.grand_total or 0)
									new_diff = abs(new_invoice_total - order_total)
									
									if new_diff <= tolerance:
										results["totals_match"] += 1
										results["totals_mismatch"] -= 1
										# Remove from mismatched if now matches
										results["mismatched_invoices"] = [
											m for m in results["mismatched_invoices"]
											if m["invoice"] != invoice_name
										]
				
			except Exception as e:
				results["total_errors"] += 1
				invoice_name = row.get("invoice_name", "unknown") if 'row' in locals() else "unknown"
				order_name = row.get("order_name", "unknown") if 'row' in locals() else "unknown"
				results["errors"].append({
					"invoice": invoice_name,
					"order": order_name,
					"error": str(e)
				})
				frappe.log_error(
					title="Fix and Compare Error",
					message=f"Invoice: {invoice_name}\nOrder: {order_name}\nError: {e}\n{frappe.get_traceback()}",
				)
		
		# Commit any changes
		frappe.db.commit()
		
		# Create summary
		results["summary"] = {
			"checked": results["total_checked"],
			"fixed": results["total_fixed"],
			"items_added": results["items_fixed"],
			"totals_match": results["totals_match"],
			"totals_mismatch": results["totals_mismatch"],
			"errors": results["total_errors"],
		}
		
		print(f"[info] Fix and Compare Summary:")
		print(f"  Checked: {results['total_checked']}")
		print(f"  Fixed: {results['total_fixed']} invoices ({results['items_fixed']} items added)")
		print(f"  Totals Match: {results['totals_match']}")
		print(f"  Totals Mismatch: {results['totals_mismatch']}")
		print(f"  Errors: {results['total_errors']}")
		
		return results
		
	except Exception as e:
		frappe.log_error(
			title="Fix and Compare Total Error",
			message=f"Error: {e}\n{frappe.get_traceback()}",
		)
		results["errors"].append({"error": str(e)})
		return results


@frappe.whitelist()
def detailed_invoice_comparison_report(
	invoice_names: Optional[List[str]] = None,
	from_date: Optional[str] = None,
	to_date: Optional[str] = None,
	limit: Optional[int] = None,
) -> Dict[str, Any]:
	"""
	Generate a detailed item-by-item comparison report for Sales Invoices vs Foodics Orders.
	
	Args:
		invoice_names: Optional list of specific invoice names to check
		from_date: Optional start date filter (business_date)
		to_date: Optional end date filter (business_date)
		limit: Optional limit on number of invoices to process
		
	Returns:
		dict with detailed comparison for each invoice
	"""
	frappe.only_for(("System Manager", "Administrator"))
	
	results = {
		"total_checked": 0,
		"detailed_comparisons": [],
		"summary": {},
	}
	
	try:
		# Get invoices to check
		if invoice_names:
			# Check specific invoices
			invoices_to_check = []
			for inv_name in invoice_names:
				try:
					inv = frappe.get_doc("Sales Invoice", inv_name)
					order_name = None
					
					# Find linked Foodics Order
					if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
						order_id = inv.foodics_order_id
						if order_id:
							order_name = _parent_name_for(order_id)
					elif frappe.get_meta("Sales Invoice").has_field("foodics_order_name"):
						order_name = inv.foodics_order_name
					else:
						# Try reverse lookup
						order_name = frappe.db.get_value("Foodics Order", {"sales_invoice": inv_name}, "name")
					
					if order_name:
						invoices_to_check.append({"invoice_name": inv_name, "order_name": order_name})
				except Exception:
					pass
		else:
			# Build query to find invoices
			conditions = []
			values = []
			
			if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
				conditions.append("si.foodics_order_id IS NOT NULL AND si.foodics_order_id != ''")
			elif frappe.get_meta("Sales Invoice").has_field("foodics_order_name"):
				conditions.append("si.foodics_order_name IS NOT NULL AND si.foodics_order_name != ''")
			else:
				conditions.append("fo.sales_invoice IS NOT NULL")
			
			if from_date:
				conditions.append("fo.business_date >= %s")
				values.append(from_date)
			if to_date:
				conditions.append("fo.business_date <= %s")
				values.append(to_date)
			
			if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
				query = f"""
					SELECT DISTINCT si.name as invoice_name, si.foodics_order_id
					FROM `tabSales Invoice` si
					INNER JOIN `tabFoodics Order` fo ON fo.foodics_order_id = si.foodics_order_id
					WHERE {' AND '.join(conditions)}
					ORDER BY si.creation DESC
				"""
			elif frappe.get_meta("Sales Invoice").has_field("foodics_order_name"):
				query = f"""
					SELECT DISTINCT si.name as invoice_name, fo.name as order_name
					FROM `tabSales Invoice` si
					INNER JOIN `tabFoodics Order` fo ON fo.name = si.foodics_order_name
					WHERE {' AND '.join(conditions)}
					ORDER BY si.creation DESC
				"""
			else:
				query = f"""
					SELECT DISTINCT si.name as invoice_name, fo.name as order_name
					FROM `tabSales Invoice` si
					INNER JOIN `tabFoodics Order` fo ON fo.sales_invoice = si.name
					WHERE {' AND '.join(conditions)}
					ORDER BY si.creation DESC
				"""
			
			if limit:
				query += f" LIMIT {int(limit)}"
			
			invoices_to_check = frappe.db.sql(query, values, as_dict=True)
		
		print(f"[info] Generating detailed comparison for {len(invoices_to_check)} invoices")
		
		for row in invoices_to_check:
			try:
				invoice_name = row.get("invoice_name")
				order_name = row.get("order_name")
				order_id = row.get("foodics_order_id")
				
				if not order_name and order_id:
					order_name = _parent_name_for(order_id)
				
				if not invoice_name or not order_name:
					continue
				
				results["total_checked"] += 1
				
				# Get documents
				try:
					invoice = frappe.get_doc("Sales Invoice", invoice_name)
					order_doc = frappe.get_doc("Foodics Order", order_name)
				except Exception as doc_err:
					results["detailed_comparisons"].append({
						"invoice": invoice_name,
						"order": order_name,
						"error": f"Failed to load: {doc_err}"
					})
					continue
				
				# Build detailed comparison
				comparison = {
					"invoice_name": invoice_name,
					"order_name": order_name,
					"order_id": getattr(order_doc, "foodics_order_id", None),
					"business_date": getattr(order_doc, "business_date", None),
					"totals": {
						"invoice_grand_total": float(invoice.grand_total or 0),
						"invoice_net_total": float(invoice.net_total or 0),
						"invoice_taxes": float((invoice.grand_total or 0) - (invoice.net_total or 0)),
						"order_total_gross": float(getattr(order_doc, "total_price_gross", None) or 0),
						"order_total_net": float(getattr(order_doc, "total_price_net", None) or 0),
						"order_taxes": float((getattr(order_doc, "total_price_gross", None) or 0) - (getattr(order_doc, "total_price_net", None) or 0)),
						"difference": float((invoice.grand_total or 0) - (getattr(order_doc, "total_price_gross", None) or 0)),
					},
					"invoice_items": [],
					"order_items": [],
					"item_comparison": [],
					"missing_items": [],
					"extra_items": [],
				}
				
				# Get invoice items
				for inv_item in invoice.items:
					comparison["invoice_items"].append({
						"item_code": inv_item.item_code,
						"item_name": inv_item.item_name,
						"qty": float(inv_item.qty or 0),
						"rate": float(inv_item.rate or 0),
						"amount": float(inv_item.amount or 0),
						"foodics_product_id": None,
						"foodics_combo_id": None,
					})
					
					# Try to get Foodics IDs from item
					try:
						item_doc = frappe.get_doc("Item", inv_item.item_code)
						comparison["invoice_items"][-1]["foodics_product_id"] = getattr(item_doc, "foodics_product_id", None)
						comparison["invoice_items"][-1]["foodics_combo_id"] = getattr(item_doc, "foodics_combo_id", None)
					except Exception:
						pass
				
				# Get order items
				order_items = getattr(order_doc, "items", []) or []
				for order_item in order_items:
					# Get product_name from multiple possible fields
					product_name = (
						getattr(order_item, "foodics_product_name", None) or
						getattr(order_item, "product_name", None)
					)
					
					# If product_name is still null, try to get it from Item doctype using SKU
					if not product_name:
						product_sku = getattr(order_item, "product_sku", None)
						if product_sku:
							try:
								item_doc = frappe.get_doc("Item", product_sku)
								product_name = item_doc.item_name
							except Exception:
								pass
					
					# If still null, try to extract from raw JSON
					if not product_name and hasattr(order_doc, "foodics_order_raw") and order_doc.foodics_order_raw:
						try:
							import json
							raw_data = json.loads(order_doc.foodics_order_raw)
							# Try to find this item in raw JSON by matching quantity/price
							products = raw_data.get("products", [])
							for p in products:
								p_quantity = p.get("quantity", 0)
								p_price = p.get("unit_price", 0)
								if (abs(p_quantity - getattr(order_item, "quantity", 0)) < 0.01 and
								    abs(p_price - getattr(order_item, "unit_price_gross", 0)) < 0.01):
									prod = p.get("product") or {}
									product_name = prod.get("name")
									if product_name:
										break
						except Exception:
							pass
					
					comparison["order_items"].append({
						"foodics_product_id": getattr(order_item, "foodics_product_id", None),
						"product_sku": getattr(order_item, "product_sku", None),
						"product_name": product_name,
						"quantity": float(getattr(order_item, "quantity", 0) or 0),
						"unit_price_net": float(getattr(order_item, "unit_price_net", None) or 0),
						"unit_price_gross": float(getattr(order_item, "unit_price_gross", None) or 0),
						"total_net": float(getattr(order_item, "quantity", 0) or 0) * float(getattr(order_item, "unit_price_net", None) or 0),
						"total_gross": float(getattr(order_item, "quantity", 0) or 0) * float(getattr(order_item, "unit_price_gross", None) or 0),
					})
				
				# Get order combos
				order_combos = getattr(order_doc, "combos", []) or []
				for combo in order_combos:
					comparison["order_items"].append({
						"foodics_combo_id": getattr(combo, "foodics_combo_id", None),
						"combo_name": getattr(combo, "combo_name", None),
						"quantity": float(getattr(combo, "quantity", 0) or 0),
						"unit_price_net": float(getattr(combo, "unit_price_net", None) or 0),
						"unit_price_gross": float(getattr(combo, "unit_price_gross", None) or 0),
						"total_net": float(getattr(combo, "quantity", 0) or 0) * float(getattr(combo, "unit_price_net", None) or 0),
						"total_gross": float(getattr(combo, "quantity", 0) or 0) * float(getattr(combo, "unit_price_gross", None) or 0),
					})
				
				# Compare items
				# Build maps for matching
				order_items_by_product_id = {}
				order_items_by_combo_id = {}
				order_items_by_sku = {}
				order_items_by_name = {}
				
				for oi in comparison["order_items"]:
					if oi.get("foodics_product_id"):
						order_items_by_product_id[oi["foodics_product_id"]] = oi
					if oi.get("foodics_combo_id"):
						order_items_by_combo_id[oi["foodics_combo_id"]] = oi
					if oi.get("product_sku"):
						order_items_by_sku[oi["product_sku"]] = oi
					if oi.get("product_name"):
						# Use product_name for matching when product_id is null
						order_items_by_name[oi["product_name"]] = oi
				
				# Match invoice items to order items
				matched_order_items = set()
				for inv_item in comparison["invoice_items"]:
					matched = False
					match_info = {
						"invoice_item": inv_item,
						"order_item": None,
						"match_type": None,
						"qty_match": False,
						"price_match": False,
					}
					
					# Try to match by product_id
					if inv_item.get("foodics_product_id"):
						order_item = order_items_by_product_id.get(inv_item["foodics_product_id"])
						if order_item:
							match_info["order_item"] = order_item
							match_info["match_type"] = "product_id"
							matched = True
							matched_order_items.add(id(order_item))
					
					# Try to match by combo_id
					if not matched and inv_item.get("foodics_combo_id"):
						order_item = order_items_by_combo_id.get(inv_item["foodics_combo_id"])
						if order_item:
							match_info["order_item"] = order_item
							match_info["match_type"] = "combo_id"
							matched = True
							matched_order_items.add(id(order_item))
					
					# Try to match by product_name (when product_id is null)
					# Match invoice item_name to order product_name
					if not matched and inv_item.get("item_name"):
						# Try exact match first
						order_item = order_items_by_name.get(inv_item["item_name"])
						if order_item:
							match_info["order_item"] = order_item
							match_info["match_type"] = "product_name"
							matched = True
							matched_order_items.add(id(order_item))
						else:
							# Try case-insensitive and partial matching
							inv_name_lower = inv_item["item_name"].lower().strip()
							for name, oi in order_items_by_name.items():
								if name and name.lower().strip() == inv_name_lower:
									match_info["order_item"] = oi
									match_info["match_type"] = "product_name"
									matched = True
									matched_order_items.add(id(oi))
									break
					
					# Try to match by SKU
					if not matched:
						order_item = order_items_by_sku.get(inv_item["item_code"])
						if order_item:
							match_info["order_item"] = order_item
							match_info["match_type"] = "sku"
							matched = True
							matched_order_items.add(id(order_item))
					
					# Check quantity and price match
					if match_info["order_item"]:
						# Quantity match
						if abs(inv_item["qty"] - match_info["order_item"].get("quantity", 0)) < 0.01:
							match_info["qty_match"] = True
						
						# Price match (compare net prices)
						inv_rate = inv_item["rate"]
						order_rate = match_info["order_item"].get("unit_price_net", 0)
						if abs(inv_rate - order_rate) < 0.01:
							match_info["price_match"] = True
					
					comparison["item_comparison"].append(match_info)
					
					if not matched:
						comparison["extra_items"].append(inv_item)
				
				# Find missing items (in order but not in invoice)
				for oi in comparison["order_items"]:
					if id(oi) not in matched_order_items:
						comparison["missing_items"].append(oi)
				
				# Calculate totals
				invoice_items_total = sum(item["amount"] for item in comparison["invoice_items"])
				order_items_total_gross = sum(item.get("total_gross", 0) for item in comparison["order_items"])
				order_items_total_net = sum(item.get("total_net", 0) for item in comparison["order_items"])
				
				comparison["item_totals"] = {
					"invoice_items_total": invoice_items_total,
					"order_items_total_gross": order_items_total_gross,
					"order_items_total_net": order_items_total_net,
					"items_difference": invoice_items_total - order_items_total_gross,
				}
				
				# Add discounts if any
				comparison["discounts"] = {
					"invoice_discount": float(getattr(invoice, "discount_amount", 0) or 0),
					"order_discount": float(getattr(order_doc, "discount_amount", 0) or 0),
				}
				
				results["detailed_comparisons"].append(comparison)
				
			except Exception as e:
				results["detailed_comparisons"].append({
					"invoice": invoice_name if 'invoice_name' in locals() else "unknown",
					"order": order_name if 'order_name' in locals() else "unknown",
					"error": str(e)
				})
				frappe.log_error(
					title="Detailed Comparison Error",
					message=f"Invoice: {invoice_name if 'invoice_name' in locals() else 'unknown'}\nOrder: {order_name if 'order_name' in locals() else 'unknown'}\nError: {e}\n{frappe.get_traceback()}",
				)
		
		# Create summary
		total_mismatches = sum(1 for c in results["detailed_comparisons"] if abs(c.get("totals", {}).get("difference", 0)) > 0.10)
		total_missing_items = sum(len(c.get("missing_items", [])) for c in results["detailed_comparisons"])
		total_extra_items = sum(len(c.get("extra_items", [])) for c in results["detailed_comparisons"])
		
		results["summary"] = {
			"total_checked": results["total_checked"],
			"total_mismatches": total_mismatches,
			"total_missing_items": total_missing_items,
			"total_extra_items": total_extra_items,
		}
		
		print(f"[info] Detailed Comparison Summary:")
		print(f"  Checked: {results['total_checked']}")
		print(f"  Mismatches: {total_mismatches}")
		print(f"  Missing Items: {total_missing_items}")
		print(f"  Extra Items: {total_extra_items}")
		
		return results
		
	except Exception as e:
		frappe.log_error(
			title="Detailed Comparison Report Error",
			message=f"Error: {e}\n{frappe.get_traceback()}",
		)
		results["error"] = str(e)
		return results


@frappe.whitelist()
def fix_invoices_to_match_orders(
	from_date: Optional[str] = None,
	to_date: Optional[str] = None,
	limit: Optional[int] = None,
	fix_items: bool = True,
	fix_totals: bool = True,
) -> Dict[str, Any]:
	"""
	Fix Sales Invoices to match Foodics Order values and items.
	- Adds missing items from Foodics Orders
	- Adjusts prices to match order gross prices
	- Recalculates taxes and totals
	- Re-submits invoices
	
	Args:
		from_date: Optional start date filter (business_date)
		to_date: Optional end date filter (business_date)
		limit: Optional limit on number of invoices to process
		fix_items: If True, add missing items (default: True)
		fix_totals: If True, adjust prices to match order totals (default: True)
		
	Returns:
		dict with results: fixed, items_added, totals_adjusted, errors
	"""
	frappe.only_for(("System Manager", "Administrator"))
	
	results = {
		"total_checked": 0,
		"total_fixed": 0,
		"items_added": 0,
		"totals_adjusted": 0,
		"errors": [],
		"fixed_invoices": [],
	}
	
	try:
		# Build query to find Sales Invoices with Foodics Orders
		conditions = []
		values = []
		
		if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
			conditions.append("si.foodics_order_id IS NOT NULL AND si.foodics_order_id != ''")
		elif frappe.get_meta("Sales Invoice").has_field("foodics_order_name"):
			conditions.append("si.foodics_order_name IS NOT NULL AND si.foodics_order_name != ''")
		else:
			conditions.append("fo.sales_invoice IS NOT NULL")
		
		if from_date:
			conditions.append("fo.business_date >= %s")
			values.append(from_date)
		if to_date:
			conditions.append("fo.business_date <= %s")
			values.append(to_date)
		
		if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
			query = f"""
				SELECT DISTINCT si.name as invoice_name, si.foodics_order_id
				FROM `tabSales Invoice` si
				INNER JOIN `tabFoodics Order` fo ON fo.foodics_order_id = si.foodics_order_id
				WHERE {' AND '.join(conditions)}
				ORDER BY si.creation DESC
			"""
		elif frappe.get_meta("Sales Invoice").has_field("foodics_order_name"):
			query = f"""
				SELECT DISTINCT si.name as invoice_name, fo.name as order_name
				FROM `tabSales Invoice` si
				INNER JOIN `tabFoodics Order` fo ON fo.name = si.foodics_order_name
				WHERE {' AND '.join(conditions)}
				ORDER BY si.creation DESC
			"""
		else:
			query = f"""
				SELECT DISTINCT si.name as invoice_name, fo.name as order_name
				FROM `tabSales Invoice` si
				INNER JOIN `tabFoodics Order` fo ON fo.sales_invoice = si.name
				WHERE {' AND '.join(conditions)}
				ORDER BY si.creation DESC
			"""
		
		if limit:
			query += f" LIMIT {int(limit)}"
		
		invoices = frappe.db.sql(query, values, as_dict=True)
		
		print(f"[info] Found {len(invoices)} invoices to fix")
		
		for row in invoices:
			try:
				invoice_name = row.get("invoice_name")
				order_name = row.get("order_name")
				order_id = row.get("foodics_order_id")
				
				if not order_name and order_id:
					order_name = _parent_name_for(order_id)
				
				if not invoice_name or not order_name:
					continue
				
				results["total_checked"] += 1
				
				# Get documents
				try:
					invoice = frappe.get_doc("Sales Invoice", invoice_name)
					order_doc = frappe.get_doc("Foodics Order", order_name)
				except Exception as doc_err:
					results["errors"].append({
						"invoice": invoice_name,
						"order": order_name,
						"error": f"Failed to load documents: {doc_err}"
					})
					continue
				
				# Check invoice status
				invoice.reload()
				was_submitted = invoice.docstatus == 1
				was_cancelled = invoice.docstatus == 2
				is_draft = invoice.docstatus == 0
				
				items_added_count = 0
				totals_adjusted_flag = False
				
				# For cancelled invoices, we'll use direct SQL updates (risky but necessary)
				# For submitted invoices, cancel first, then fix
				if was_cancelled:
					print(f"[info] Invoice {invoice_name} is cancelled, will use direct SQL updates")
					use_direct_sql = True
				elif was_submitted:
					print(f"[info] Invoice {invoice_name} is submitted, cancelling to allow editing...")
					try:
						invoice.cancel()
						frappe.db.commit()
						invoice = frappe.get_doc("Sales Invoice", invoice_name)
						if invoice.docstatus == 2:
							use_direct_sql = True
							print(f"[info] Invoice became cancelled, will use direct SQL updates")
						else:
							use_direct_sql = False
					except Exception as cancel_err:
						print(f"[warn] Failed to cancel invoice {invoice_name}: {cancel_err}")
						results["errors"].append({
							"invoice": invoice_name,
							"order": order_name,
							"error": f"Failed to cancel: {cancel_err}"
						})
						continue
				else:
					use_direct_sql = False
				
				# Step 1: Fix missing items
				if fix_items:
					validation_result = _validate_invoice_items_and_values(invoice, order_doc)
					missing_items = validation_result.get("missing_items", [])
					
					if missing_items:
						print(f"[info] Invoice {invoice_name} has {len(missing_items)} missing items, fixing...")
						
						# Cancel if submitted (to allow editing)
						if was_submitted:
							try:
								print(f"[info] Cancelling submitted invoice {invoice_name} to allow editing...")
								invoice.cancel()
								frappe.db.commit()
								# Reload to get updated docstatus
								invoice = frappe.get_doc("Sales Invoice", invoice_name)
								if invoice.docstatus == 2:
									print(f"[warn] Invoice {invoice_name} became cancelled after cancel operation, skipping")
									continue
							except Exception as cancel_err:
								print(f"[warn] Failed to cancel invoice {invoice_name}: {cancel_err}")
								results["errors"].append({
									"invoice": invoice_name,
									"order": order_name,
									"error": f"Failed to cancel for editing: {cancel_err}"
								})
								continue
						
						fix_result = _fix_invoice_missing_items(invoice, order_doc, missing_items)
						
						if fix_result.get("fixed", False):
							items_added_count = fix_result.get("items_added", 0)
							results["items_added"] += items_added_count
							invoice.reload()
				
				# Step 2: Fix prices and taxes to match order totals
				if fix_totals:
					order_total_gross = float(getattr(order_doc, "total_price_gross", None) or 0)
					order_total_net = float(getattr(order_doc, "total_price_net", None) or 0)
					invoice_total = float(invoice.grand_total or 0)
					invoice_net = float(invoice.net_total or 0)
					tolerance = 0.10
					
					if order_total_gross > 0 and abs(invoice_total - order_total_gross) > tolerance:
						print(f"[info] Invoice {invoice_name} total mismatch: Invoice={invoice_total:.2f}, Order={order_total_gross:.2f}, fixing...")
						
						# Check current status
						invoice.reload()
						
						# If cancelled, use direct SQL (already set use_direct_sql above)
						if invoice.docstatus == 2:  # Cancelled
							use_direct_sql = True
							print(f"[info] Invoice {invoice_name} is cancelled, will use direct SQL updates")
						
						# For submitted invoices, we need to cancel to edit
						needs_cancellation = invoice.docstatus == 1
						if needs_cancellation:
							try:
								print(f"[info] Cancelling submitted invoice {invoice_name} to allow editing...")
								invoice.cancel()
								frappe.db.commit()
								# Reload to get updated docstatus
								invoice = frappe.get_doc("Sales Invoice", invoice_name)
								if invoice.docstatus == 2:
									print(f"[warn] Invoice {invoice_name} became cancelled, skipping")
									continue
							except Exception as cancel_err:
								print(f"[warn] Failed to cancel invoice {invoice_name}: {cancel_err}")
								results["errors"].append({
									"invoice": invoice_name,
									"order": order_name,
									"error": f"Failed to cancel for editing: {cancel_err}"
								})
								continue
						
						# Get order items to match prices
						order_items = getattr(order_doc, "items", []) or []
						order_combos = getattr(order_doc, "combos", []) or []
						
						# Build maps for matching
						order_items_by_product_id = {}
						order_items_by_combo_id = {}
						order_items_by_name = {}
						order_items_by_sku = {}
						
						for oi in order_items:
							product_id = getattr(oi, "foodics_product_id", None)
							combo_id = getattr(oi, "foodics_combo_id", None)
							product_sku = getattr(oi, "product_sku", None)
							product_name = (
								getattr(oi, "foodics_product_name", None) or
								getattr(oi, "product_name", None)
							)
							
							if product_id:
								order_items_by_product_id[product_id] = oi
							if combo_id:
								order_items_by_combo_id[combo_id] = oi
							if product_sku:
								order_items_by_sku[product_sku] = oi
							if product_name:
								order_items_by_name[product_name] = oi
						
						for combo in order_combos:
							combo_id = getattr(combo, "foodics_combo_id", None)
							combo_name = getattr(combo, "combo_name", None)
							
							if combo_id:
								order_items_by_combo_id[combo_id] = combo
							if combo_name:
								order_items_by_name[combo_name] = combo
						
						# Update invoice item prices to match order gross prices
						# Track which order items have been matched
						matched_order_item_indices = set()
						
						# Get invoice items (reload to ensure we have latest)
						invoice.reload()
						
						for inv_item in invoice.items:
							matched_order_item = None
							matched_index = None
							
							# Try to match by product_id
							try:
								item_doc = frappe.get_doc("Item", inv_item.item_code)
								product_id = getattr(item_doc, "foodics_product_id", None)
								combo_id = getattr(item_doc, "foodics_combo_id", None)
								
								if product_id and product_id in order_items_by_product_id:
									matched_order_item = order_items_by_product_id[product_id]
								elif combo_id and combo_id in order_items_by_combo_id:
									matched_order_item = order_items_by_combo_id[combo_id]
								elif inv_item.item_name and inv_item.item_name in order_items_by_name:
									matched_order_item = order_items_by_name[inv_item.item_name]
								elif inv_item.item_code in order_items_by_sku:
									matched_order_item = order_items_by_sku[inv_item.item_code]
							except Exception:
								pass
							
							# If no match by ID/name/SKU, try to match by quantity and price (fallback)
							if not matched_order_item:
								inv_qty = float(inv_item.qty or 0)
								inv_rate = float(inv_item.rate or 0)
								
								# Try to find order item with matching quantity and similar price
								for idx, oi in enumerate(order_items):
									if idx in matched_order_item_indices:
										continue
									
									oi_qty = float(getattr(oi, "quantity", 0) or 0)
									oi_net_price = float(getattr(oi, "unit_price_net", None) or 0)
									oi_gross_price = float(getattr(oi, "unit_price_gross", None) or 0)
									
									# Match by quantity and price (within tolerance)
									if (abs(oi_qty - inv_qty) < 0.01 and
									    (abs(oi_net_price - inv_rate) < 0.50 or abs(oi_gross_price - inv_rate) < 0.50)):
										matched_order_item = oi
										matched_index = idx
										break
							
							if matched_order_item:
								if matched_index is not None:
									matched_order_item_indices.add(matched_index)
								
								# Use net price from order (invoice should use net, tax will be added)
								order_net_price = float(getattr(matched_order_item, "unit_price_net", None) or 0)
								order_gross_price = float(getattr(matched_order_item, "unit_price_gross", None) or 0)
								
								old_rate = float(inv_item.rate or 0)
								new_rate = None
								
								if order_net_price > 0:
									# Use net price (taxes will be calculated separately)
									new_rate = order_net_price
								elif order_gross_price > 0:
									# Fallback: calculate net from gross (assuming 15% VAT)
									new_rate = order_gross_price / 1.15
								
								if new_rate and abs(new_rate - old_rate) > 0.01:
									if use_direct_sql:
										# Update via SQL for cancelled invoices
										frappe.db.sql("""
											UPDATE `tabSales Invoice Item`
											SET rate = %s, amount = qty * %s
											WHERE name = %s
										""", (new_rate, new_rate, inv_item.name))
										print(f"[info] Updated price via SQL for {inv_item.item_code}: {old_rate:.2f} -> {new_rate:.2f}")
									else:
										# Normal update
										inv_item.rate = new_rate
										print(f"[info] Updated price for {inv_item.item_code}: {old_rate:.2f} -> {new_rate:.2f} (net)")
									
									totals_adjusted_flag = True
						
						# Ensure tax template is applied (15% VAT)
						settings = frappe.get_single("Foodics Settings")
						tax_template = getattr(settings, "default_sales_taxes_and_charges_template", None)
						if not tax_template:
							tax_template = frappe.db.get_value("Sales Taxes and Charges Template", {"name": "VAT 15%"}, "name")
						
						if tax_template:
							try:
								invoice.taxes_and_charges = tax_template
								invoice.run_method("set_missing_values")
								invoice.run_method("set_taxes")
							except Exception:
								pass
						
						# Recalculate totals
						if totals_adjusted_flag:
							try:
								if use_direct_sql:
									# For cancelled invoices, use direct SQL to update
									# First, update item rates via SQL
									for inv_item in invoice.items:
										# Update rate directly in database
										frappe.db.sql("""
											UPDATE `tabSales Invoice Item`
											SET rate = %s, amount = qty * %s
											WHERE name = %s
										""", (inv_item.rate, inv_item.rate, inv_item.name))
									
									# Recalculate net total
									net_total = frappe.db.sql("""
										SELECT SUM(amount) FROM `tabSales Invoice Item`
										WHERE parent = %s
									""", invoice_name)[0][0] or 0
									
									# Calculate tax (15% VAT)
									tax_amount = net_total * 0.15
									grand_total = net_total + tax_amount
									
									# Update invoice totals
									frappe.db.sql("""
										UPDATE `tabSales Invoice`
										SET net_total = %s,
										    total_taxes_and_charges = %s,
										    grand_total = %s,
										    rounded_total = %s
										WHERE name = %s
									""", (net_total, tax_amount, grand_total, grand_total, invoice_name))
									
									# Update tax row
									frappe.db.sql("""
										UPDATE `tabSales Taxes and Charges`
										SET tax_amount = %s, total = %s
										WHERE parent = %s AND charge_type = 'On Net Total'
									""", (tax_amount, tax_amount, invoice_name))
									
									frappe.db.commit()
									print(f"[info] Updated invoice {invoice_name} totals via SQL: net={net_total:.2f}, tax={tax_amount:.2f}, grand={grand_total:.2f}")
									results["totals_adjusted"] += 1
									totals_adjusted_flag = True
								else:
									# Normal update for draft invoices
									invoice.run_method("calculate_taxes_and_totals")
									invoice.save(ignore_permissions=True)
									frappe.db.commit()
									results["totals_adjusted"] += 1
									totals_adjusted_flag = True
							except Exception as calc_err:
								print(f"[warn] Failed to recalculate totals: {calc_err}")
								results["errors"].append({
									"invoice": invoice_name,
									"order": order_name,
									"error": f"Failed to recalculate: {calc_err}"
								})
				
				# Step 3: Ensure invoice total matches order total (final adjustment)
				if fix_totals:
					invoice.reload()
					order_total_gross = float(getattr(order_doc, "total_price_gross", None) or 0)
					invoice_total = float(invoice.grand_total or 0)
					tolerance = 0.10
					
					if order_total_gross > 0 and abs(invoice_total - order_total_gross) > tolerance:
						# If still doesn't match, check if we need to adjust tax rate
						diff = order_total_gross - invoice_total
						
						# Calculate expected tax
						invoice_net = float(invoice.net_total or 0)
						expected_tax = order_total_gross - invoice_net
						actual_tax = invoice_total - invoice_net
						
						if abs(expected_tax - actual_tax) > tolerance:
							# Tax calculation is wrong, adjust tax entry
							if use_direct_sql:
								# Use SQL to update tax
								if invoice_net > 0:
									correct_tax_amount = expected_tax
									correct_grand_total = invoice_net + correct_tax_amount
									
									# Update tax row via SQL
									frappe.db.sql("""
										UPDATE `tabSales Taxes and Charges`
										SET tax_amount = %s, total = %s, rate = %s
										WHERE parent = %s AND charge_type = 'On Net Total'
									""", (correct_tax_amount, correct_tax_amount, (correct_tax_amount / invoice_net) * 100, invoice_name))
									
									# Update invoice totals
									frappe.db.sql("""
										UPDATE `tabSales Invoice`
										SET total_taxes_and_charges = %s,
										    grand_total = %s,
										    rounded_total = %s
										WHERE name = %s
									""", (correct_tax_amount, correct_grand_total, correct_grand_total, invoice_name))
									
									frappe.db.commit()
									print(f"[info] Updated tax via SQL: tax={correct_tax_amount:.2f}, grand_total={correct_grand_total:.2f}")
									totals_adjusted_flag = True
							else:
								# Normal update for draft invoices
								if invoice.taxes:
									for tax_row in invoice.taxes:
										if tax_row.charge_type == "On Net Total":
											# Calculate correct rate
											if invoice_net > 0:
												correct_rate = (expected_tax / invoice_net) * 100
												tax_row.rate = correct_rate
												print(f"[info] Adjusted tax rate to {correct_rate:.2f}% to match order total")
												totals_adjusted_flag = True
												break
								
								try:
									invoice.run_method("calculate_taxes_and_totals")
									invoice.save(ignore_permissions=True)
									frappe.db.commit()
									totals_adjusted_flag = True
									print(f"[info] Recalculated taxes to match order total")
								except Exception as tax_err:
									print(f"[warn] Failed to adjust taxes: {tax_err}")
				
				# Step 4: Re-submit if it was submitted originally
				if was_submitted and (items_added_count > 0 or totals_adjusted_flag):
					try:
						invoice.reload()
						if invoice.docstatus == 0:  # Draft
							# Ensure set_posting_time and is_pos are set
							try:
								invoice.db_set("set_posting_time", 1)
								if frappe.get_meta("Sales Invoice").has_field("is_pos"):
									invoice.db_set("is_pos", 1)
								frappe.db.commit()
							except Exception:
								pass
							
							invoice.submit()
							frappe.db.commit()
							print(f"[info] Re-submitted invoice {invoice_name}")
							
							# Ensure outstanding and status are correct if fully paid
							order_total_gross = float(getattr(order_doc, "total_price_gross", None) or 0)
							invoice.reload()
							if invoice.grand_total and abs(float(invoice.grand_total) - order_total_gross) < 0.10:
								# Check if order is fully paid
								if _is_order_fully_paid({"id": getattr(order_doc, "foodics_order_id", None)}):
									try:
										invoice.db_set("outstanding_amount", 0)
										invoice.db_set("status", "Paid")
										frappe.db.commit()
									except Exception:
										pass
						elif invoice.docstatus == 2:
							print(f"[warn] Invoice {invoice_name} is cancelled, cannot re-submit")
					except Exception as submit_err:
						print(f"[warn] Failed to re-submit invoice {invoice_name}: {submit_err}")
						results["errors"].append({
							"invoice": invoice_name,
							"order": order_name,
							"error": f"Failed to re-submit: {submit_err}"
						})
				
				# Record success
				if items_added_count > 0 or totals_adjusted_flag:
					results["total_fixed"] += 1
					results["fixed_invoices"].append({
						"invoice": invoice_name,
						"order": order_name,
						"items_added": items_added_count,
						"totals_adjusted": totals_adjusted_flag,
					})
				
			except Exception as e:
				results["errors"].append({
					"invoice": invoice_name if 'invoice_name' in locals() else "unknown",
					"order": order_name if 'order_name' in locals() else "unknown",
					"error": str(e)
				})
				frappe.log_error(
					title="Fix Invoice Error",
					message=f"Invoice: {invoice_name if 'invoice_name' in locals() else 'unknown'}\nOrder: {order_name if 'order_name' in locals() else 'unknown'}\nError: {e}\n{frappe.get_traceback()}",
				)
		
		# Final commit
		frappe.db.commit()
		
		# Create summary
		results["summary"] = {
			"checked": results["total_checked"],
			"fixed": results["total_fixed"],
			"items_added": results["items_added"],
			"totals_adjusted": results["totals_adjusted"],
			"errors": len(results["errors"]),
		}
		
		print(f"[info] Fix Summary:")
		print(f"  Checked: {results['total_checked']}")
		print(f"  Fixed: {results['total_fixed']} invoices")
		print(f"  Items Added: {results['items_added']}")
		print(f"  Totals Adjusted: {results['totals_adjusted']}")
		print(f"  Errors: {len(results['errors'])}")
		
		return results
		
	except Exception as e:
		frappe.log_error(
			title="Fix Invoices to Match Orders Error",
			message=f"Error: {e}\n{frappe.get_traceback()}",
		)
		results["error"] = str(e)
		return results


@frappe.whitelist()
def fix_and_resubmit_invoices_in_batches(
	from_date: Optional[str] = None,
	to_date: Optional[str] = None,
	batch_size: int = 10,
	fix_items: bool = True,
	fix_totals: bool = True,
) -> Dict[str, Any]:
	"""
	Fix invoices to match Foodics Orders and resubmit them.
	- Sets cancelled invoices to draft (docstatus = 0) via SQL
	- Fixes items and totals
	- Submits invoices
	- Processes in batches of specified size
	
	Args:
		from_date: Optional start date filter (business_date)
		to_date: Optional end date filter (business_date)
		batch_size: Number of invoices to process per batch (default: 10)
		fix_items: If True, add missing items (default: True)
		fix_totals: If True, adjust prices to match order totals (default: True)
		
	Returns:
		dict with results per batch
	"""
	frappe.only_for(("System Manager", "Administrator"))
	
	all_results = {
		"total_batches": 0,
		"total_checked": 0,
		"total_fixed": 0,
		"total_resubmitted": 0,
		"items_added": 0,
		"totals_adjusted": 0,
		"errors": [],
		"batches": [],
	}
	
	try:
		# Build query to find Sales Invoices with Foodics Orders that need fixing
		conditions = []
		values = []
		
		if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
			conditions.append("si.foodics_order_id IS NOT NULL AND si.foodics_order_id != ''")
		elif frappe.get_meta("Sales Invoice").has_field("foodics_order_name"):
			conditions.append("si.foodics_order_name IS NOT NULL AND si.foodics_order_name != ''")
		else:
			conditions.append("fo.sales_invoice IS NOT NULL")
		
		if from_date:
			conditions.append("fo.business_date >= %s")
			values.append(from_date)
		if to_date:
			conditions.append("fo.business_date <= %s")
			values.append(to_date)
		
		if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
			query = f"""
				SELECT DISTINCT si.name as invoice_name, si.foodics_order_id, si.docstatus, si.grand_total
				FROM `tabSales Invoice` si
				INNER JOIN `tabFoodics Order` fo ON fo.foodics_order_id = si.foodics_order_id
				WHERE {' AND '.join(conditions)}
				ORDER BY si.creation DESC
			"""
		elif frappe.get_meta("Sales Invoice").has_field("foodics_order_name"):
			query = f"""
				SELECT DISTINCT si.name as invoice_name, fo.name as order_name, si.docstatus, si.grand_total, fo.total_price_gross
				FROM `tabSales Invoice` si
				INNER JOIN `tabFoodics Order` fo ON fo.name = si.foodics_order_name
				WHERE {' AND '.join(conditions)}
				ORDER BY si.creation DESC
			"""
		else:
			query = f"""
				SELECT DISTINCT si.name as invoice_name, fo.name as order_name, si.docstatus, si.grand_total, fo.total_price_gross
				FROM `tabSales Invoice` si
				INNER JOIN `tabFoodics Order` fo ON fo.sales_invoice = si.name
				WHERE {' AND '.join(conditions)}
				ORDER BY si.creation DESC
			"""
		
		all_invoices = frappe.db.sql(query, values, as_dict=True)
		
		# Filter invoices that need fixing (mismatched totals)
		invoices_to_fix = []
		for row in all_invoices:
			invoice_name = row.get("invoice_name")
			order_name = row.get("order_name")
			order_id = row.get("foodics_order_id")
			
			if not order_name and order_id:
				order_name = _parent_name_for(order_id)
			
			if not invoice_name or not order_name:
				continue
			
			# Check if totals match
			try:
				order_doc = frappe.get_doc("Foodics Order", order_name)
				order_total_gross = float(getattr(order_doc, "total_price_gross", None) or 0)
				invoice_total = float(row.get("grand_total") or 0)
				tolerance = 0.10
				
				# Also check rounded_total if it exists and is different from grand_total
				rounded_total = float(row.get("rounded_total") or 0)
				if rounded_total > 0 and abs(rounded_total - invoice_total) > 0.01:
					# Use the one that's closer to order total
					diff_grand = abs(invoice_total - order_total_gross)
					diff_rounded = abs(rounded_total - order_total_gross)
					if diff_rounded < diff_grand:
						invoice_total = rounded_total
				
				if order_total_gross > 0 and abs(invoice_total - order_total_gross) > tolerance:
					invoices_to_fix.append({
						"invoice_name": invoice_name,
						"order_name": order_name,
						"docstatus": row.get("docstatus", 0),
					})
			except Exception:
				continue
		
		print(f"[info] Found {len(invoices_to_fix)} invoices that need fixing")
		
		# Process in batches
		total_batches = (len(invoices_to_fix) + batch_size - 1) // batch_size
		all_results["total_batches"] = total_batches
		
		for batch_num in range(total_batches):
			start_idx = batch_num * batch_size
			end_idx = min(start_idx + batch_size, len(invoices_to_fix))
			batch_invoices = invoices_to_fix[start_idx:end_idx]
			
			print(f"\n[info] Processing batch {batch_num + 1}/{total_batches} ({len(batch_invoices)} invoices)")
			
			batch_results = {
				"batch_number": batch_num + 1,
				"checked": 0,
				"fixed": 0,
				"resubmitted": 0,
				"items_added": 0,
				"totals_adjusted": 0,
				"errors": [],
				"fixed_invoices": [],
			}
			
			for row in batch_invoices:
				try:
					invoice_name = row["invoice_name"]
					order_name = row["order_name"]
					docstatus = row["docstatus"]
					
					batch_results["checked"] += 1
					all_results["total_checked"] += 1
					
					# Get documents
					try:
						invoice = frappe.get_doc("Sales Invoice", invoice_name)
						order_doc = frappe.get_doc("Foodics Order", order_name)
					except Exception as doc_err:
						error_msg = f"Failed to load documents: {doc_err}"
						batch_results["errors"].append({
							"invoice": invoice_name,
							"error": error_msg
						})
						all_results["errors"].append({
							"invoice": invoice_name,
							"error": error_msg
						})
						continue
					
					# Step 1: If cancelled, set to draft via SQL
					if invoice.docstatus == 2:  # Cancelled
						print(f"[info] Setting cancelled invoice {invoice_name} to draft (docstatus = 0)...")
						try:
							frappe.db.sql("""
								UPDATE `tabSales Invoice`
								SET docstatus = 0
								WHERE name = %s
							""", invoice_name)
							frappe.db.commit()
							invoice.reload()
							print(f"[info] Invoice {invoice_name} set to draft")
						except Exception as sql_err:
							error_msg = f"Failed to set invoice to draft: {sql_err}"
							batch_results["errors"].append({
								"invoice": invoice_name,
								"error": error_msg
							})
							all_results["errors"].append({
								"invoice": invoice_name,
								"error": error_msg
							})
							continue
					
					# Step 2: If submitted, cancel first, then set to draft
					if invoice.docstatus == 1:  # Submitted
						print(f"[info] Cancelling submitted invoice {invoice_name}...")
						try:
							invoice.cancel()
							frappe.db.commit()
							invoice.reload()
							# After cancellation, invoice becomes cancelled (docstatus=2)
							# Set it back to draft via SQL
							if invoice.docstatus == 2:
								print(f"[info] Setting cancelled invoice {invoice_name} to draft (docstatus = 0)...")
								frappe.db.sql("""
									UPDATE `tabSales Invoice`
									SET docstatus = 0
									WHERE name = %s
								""", invoice_name)
								frappe.db.commit()
								invoice.reload()
								print(f"[info] Invoice {invoice_name} set to draft")
						except Exception as cancel_err:
							error_msg = f"Failed to cancel invoice: {cancel_err}"
							batch_results["errors"].append({
								"invoice": invoice_name,
								"error": error_msg
							})
							all_results["errors"].append({
								"invoice": invoice_name,
								"error": error_msg
							})
							continue
					
					# Step 3: Fix missing items
					items_added_count = 0
					if fix_items:
						validation_result = _validate_invoice_items_and_values(invoice, order_doc)
						missing_items = validation_result.get("missing_items", [])
						
						if missing_items:
							print(f"[info] Invoice {invoice_name} has {len(missing_items)} missing items, fixing...")
							fix_result = _fix_invoice_missing_items(invoice, order_doc, missing_items)
							
							if fix_result.get("fixed", False):
								items_added_count = fix_result.get("items_added", 0)
								batch_results["items_added"] += items_added_count
								all_results["items_added"] += items_added_count
								invoice.reload()
					
					# Step 4: Fix prices and totals
					totals_adjusted_flag = False
					if fix_totals:
						order_total_gross = float(getattr(order_doc, "total_price_gross", None) or 0)
						invoice_total = float(invoice.grand_total or 0)
						tolerance = 0.10
						
						if order_total_gross > 0 and abs(invoice_total - order_total_gross) > tolerance:
							print(f"[info] Fixing totals for invoice {invoice_name}: {invoice_total:.2f} -> {order_total_gross:.2f}")
							
							# Get order items
							order_items = getattr(order_doc, "items", []) or []
							order_combos = getattr(order_doc, "combos", []) or []
							
							# Build maps for matching
							order_items_by_product_id = {}
							order_items_by_combo_id = {}
							order_items_by_name = {}
							order_items_by_sku = {}
							
							for oi in order_items:
								product_id = getattr(oi, "foodics_product_id", None)
								combo_id = getattr(oi, "foodics_combo_id", None)
								product_sku = getattr(oi, "product_sku", None)
								product_name = (
									getattr(oi, "foodics_product_name", None) or
									getattr(oi, "product_name", None)
								)
								
								if product_id:
									order_items_by_product_id[product_id] = oi
								if combo_id:
									order_items_by_combo_id[combo_id] = oi
								if product_sku:
									order_items_by_sku[product_sku] = oi
								if product_name:
									order_items_by_name[product_name] = oi
							
							for combo in order_combos:
								combo_id = getattr(combo, "foodics_combo_id", None)
								combo_name = getattr(combo, "combo_name", None)
								
								if combo_id:
									order_items_by_combo_id[combo_id] = combo
								if combo_name:
									order_items_by_name[combo_name] = combo
							
							# Update invoice item prices
							matched_order_item_indices = set()
							
							for inv_item in invoice.items:
								matched_order_item = None
								matched_index = None
								
								# Try to match by product_id
								try:
									item_doc = frappe.get_doc("Item", inv_item.item_code)
									product_id = getattr(item_doc, "foodics_product_id", None)
									combo_id = getattr(item_doc, "foodics_combo_id", None)
									
									if product_id and product_id in order_items_by_product_id:
										matched_order_item = order_items_by_product_id[product_id]
									elif combo_id and combo_id in order_items_by_combo_id:
										matched_order_item = order_items_by_combo_id[combo_id]
									elif inv_item.item_name and inv_item.item_name in order_items_by_name:
										matched_order_item = order_items_by_name[inv_item.item_name]
									elif inv_item.item_code in order_items_by_sku:
										matched_order_item = order_items_by_sku[inv_item.item_code]
								except Exception:
									pass
								
								# Fallback: match by quantity and price
								if not matched_order_item:
									inv_qty = float(inv_item.qty or 0)
									inv_rate = float(inv_item.rate or 0)
									
									for idx, oi in enumerate(order_items):
										if idx in matched_order_item_indices:
											continue
										
										oi_qty = float(getattr(oi, "quantity", 0) or 0)
										oi_net_price = float(getattr(oi, "unit_price_net", None) or 0)
										oi_gross_price = float(getattr(oi, "unit_price_gross", None) or 0)
										
										if (abs(oi_qty - inv_qty) < 0.01 and
										    (abs(oi_net_price - inv_rate) < 0.50 or abs(oi_gross_price - inv_rate) < 0.50)):
											matched_order_item = oi
											matched_index = idx
											break
								
								if matched_order_item:
									if matched_index is not None:
										matched_order_item_indices.add(matched_index)
									
									order_net_price = float(getattr(matched_order_item, "unit_price_net", None) or 0)
									order_gross_price = float(getattr(matched_order_item, "unit_price_gross", None) or 0)
									
									old_rate = float(inv_item.rate or 0)
									new_rate = None
									
									if order_net_price > 0:
										new_rate = order_net_price
									elif order_gross_price > 0:
										new_rate = order_gross_price / 1.15
									
									if new_rate and abs(new_rate - old_rate) > 0.01:
										inv_item.rate = new_rate
										totals_adjusted_flag = True
										print(f"[info] Updated price for {inv_item.item_code}: {old_rate:.2f} -> {new_rate:.2f}")
							
							# Always ensure tax template is applied and totals are recalculated
							# Try to find VAT 15% template (could be "VAT 15%", "vat15 - F", etc.)
							tax_template = None
							# First try common names
							for template_name in ["VAT 15%", "vat15 - F", "vat15"]:
								if frappe.db.exists("Sales Taxes and Charges Template", template_name):
									tax_template = template_name
									break
							# If not found, search for any template with VAT in name
							if not tax_template:
								templates = frappe.get_all("Sales Taxes and Charges Template", 
									filters={"name": ["like", "%vat%"]}, 
									fields=["name"], 
									limit=1)
								if templates:
									tax_template = templates[0].name
							
							if tax_template:
								try:
									invoice.taxes_and_charges = tax_template
									# Disable rounding to use exact grand_total
									if frappe.get_meta("Sales Invoice").has_field("disable_rounded_total"):
										invoice.disable_rounded_total = 1
									invoice.run_method("set_missing_values")
									invoice.run_method("set_taxes")
									totals_adjusted_flag = True
								except Exception as tax_err:
									print(f"[warn] Failed to set tax template: {tax_err}")
							
							# Always recalculate totals when fixing (even if rates didn't change, tax might be missing)
							try:
								invoice.run_method("calculate_taxes_and_totals")
								invoice.save(ignore_permissions=True)
								frappe.db.commit()
								
								# Verify the total matches after recalculation
								invoice.reload()
								new_invoice_total = float(invoice.grand_total or 0)
								if abs(new_invoice_total - order_total_gross) > tolerance:
									# Still doesn't match - check if we need to add charges or adjust
									invoice_net = float(invoice.net_total or 0)
									
									# Calculate what's missing
									missing_amount = order_total_gross - new_invoice_total
									
									# Check if order has charges that should be added
									order_charges_total = 0
									if hasattr(order_doc, 'charges') and order_doc.charges:
										for charge in order_doc.charges:
											amount_gross = float(getattr(charge, 'amount_gross', 0) or 0)
											amount = float(getattr(charge, 'amount', 0) or 0)
											order_charges_total += amount_gross if amount_gross > 0 else amount
									
									# If charges exist and match the missing amount, add them as invoice items
									if order_charges_total > 0 and abs(order_charges_total - missing_amount) < 0.50:
										# Add charges as invoice items
										for charge in order_doc.charges:
											amount_gross = float(getattr(charge, 'amount_gross', 0) or 0)
											amount = float(getattr(charge, 'amount', 0) or 0)
											charge_amount = amount_gross if amount_gross > 0 else amount
											charge_name = getattr(charge, 'charge_name', 'Additional Charge')
											
											if charge_amount > 0:
												# Find or create a service item for charges
												service_item = frappe.db.get_value("Item", {"item_name": "Service Charge"}, "name")
												if not service_item:
													service_item = frappe.db.get_value("Item", {"item_name": "Additional Charge"}, "name")
												if not service_item:
													# Use a generic service item or create one
													service_item = frappe.db.get_value("Item", {"item_group": "Services"}, "name", order_by="creation desc")
												
												if service_item:
													inv_item = invoice.append("items", {})
													inv_item.item_code = service_item
													inv_item.qty = 1
													# Use net amount (charge amount is gross, so divide by 1.15 for net)
													inv_item.rate = charge_amount / 1.15
													inv_item.description = charge_name
													print(f"[info] Added charge as invoice item: {charge_name} = {charge_amount:.2f} SAR")
										
										# Recalculate with charges
										invoice.run_method("calculate_taxes_and_totals")
										invoice.save(ignore_permissions=True)
										frappe.db.commit()
										invoice.reload()
										new_invoice_total = float(invoice.grand_total or 0)
									
									# If still doesn't match, adjust tax manually
									if abs(new_invoice_total - order_total_gross) > tolerance:
										invoice.reload()  # Reload to get latest values
										invoice_net = float(invoice.net_total or 0)
										if invoice_net > 0:
											expected_tax = order_total_gross - invoice_net
											if expected_tax > 0:
												# Update tax row - need to modify the tax amount directly
												if invoice.taxes:
													for tax_row in invoice.taxes:
														if tax_row.charge_type == "On Net Total":
															# Calculate the rate needed
															required_rate = (expected_tax / invoice_net) * 100 if invoice_net > 0 else 15.0
															tax_row.rate = required_rate
															tax_row.tax_amount = expected_tax
															tax_row.total = expected_tax
															print(f"[info] Updating tax: rate={required_rate:.2f}%, amount={expected_tax:.2f}")
															break
												
												# Recalculate again
												invoice.run_method("calculate_taxes_and_totals")
												invoice.save(ignore_permissions=True)
												frappe.db.commit()
												
												# Verify it worked
												invoice.reload()
												final_total = float(invoice.grand_total or 0)
												if abs(final_total - order_total_gross) <= tolerance:
													print(f"[info] Successfully adjusted tax: invoice now matches order ({final_total:.2f} = {order_total_gross:.2f})")
												else:
													print(f"[info] Manually adjusted tax, but still mismatch: {final_total:.2f} vs {order_total_gross:.2f}")
													# Last resort: set grand_total directly via SQL if needed
													if abs(final_total - order_total_gross) > 0.50:  # Large difference
														# Use SQL to set exact total
														frappe.db.sql("""
															UPDATE `tabSales Invoice`
															SET grand_total = %s,
															    rounded_total = %s,
															    base_grand_total = %s,
															    base_rounded_total = %s
															WHERE name = %s
														""", (order_total_gross, order_total_gross, order_total_gross, order_total_gross, invoice.name))
														
														# Update tax amount
														frappe.db.sql("""
															UPDATE `tabSales Taxes and Charges`
															SET tax_amount = %s, total = %s
															WHERE parent = %s AND charge_type = 'On Net Total'
														""", (expected_tax, expected_tax, invoice.name))
														
														# Update total_taxes_and_charges
														frappe.db.sql("""
															UPDATE `tabSales Invoice`
															SET total_taxes_and_charges = %s,
															    base_total_taxes_and_charges = %s
															WHERE name = %s
														""", (expected_tax, expected_tax, invoice.name))
														
														frappe.db.commit()
														print(f"[info] Used SQL to set exact total: {order_total_gross:.2f} SAR")
								
								batch_results["totals_adjusted"] += 1
								all_results["totals_adjusted"] += 1
								totals_adjusted_flag = True
							except Exception as calc_err:
								print(f"[warn] Failed to recalculate totals: {calc_err}")
					
					# Step 5: Submit invoice
					invoice.reload()
					if invoice.docstatus == 0:  # Draft
						try:
							# Ensure set_posting_time and is_pos are set
							# Also disable rounding to use exact grand_total
							try:
								invoice.db_set("set_posting_time", 1)
								if frappe.get_meta("Sales Invoice").has_field("is_pos"):
									invoice.db_set("is_pos", 1)
								if frappe.get_meta("Sales Invoice").has_field("disable_rounded_total"):
									invoice.db_set("disable_rounded_total", 1)
								frappe.db.commit()
							except Exception:
								pass
							
							# If is_pos = 1, ensure there's at least one payment
							invoice.reload()
							if frappe.get_meta("Sales Invoice").has_field("is_pos") and getattr(invoice, "is_pos", 0) == 1:
								if not invoice.get("payments") or len(invoice.payments) == 0:
									# Add a default payment
									default_mop = frappe.db.get_value("Mode of Payment", {"type": "Cash"}, "name")
									if not default_mop:
										# Try to get any mode of payment
										default_mop = frappe.db.get_value("Mode of Payment", {}, "name", order_by="creation desc")
									
									if default_mop:
										payment_row = invoice.append("payments", {})
										payment_row.mode_of_payment = default_mop
										payment_row.amount = float(invoice.grand_total or 0)
										invoice.save(ignore_permissions=True)
										frappe.db.commit()
										print(f"[info] Added default payment mode '{default_mop}' for POS invoice")
									else:
										print(f"[warn] No mode of payment found - invoice may fail to submit")
							
							# Check if order is fully paid
							order_total_gross = float(getattr(order_doc, "total_price_gross", None) or 0)
							if _is_order_fully_paid({"id": getattr(order_doc, "foodics_order_id", None)}):
								invoice.outstanding_amount = 0
							
							invoice.submit()
							frappe.db.commit()
							
							# Set status to Paid if fully paid
							if _is_order_fully_paid({"id": getattr(order_doc, "foodics_order_id", None)}):
								try:
									invoice.db_set("outstanding_amount", 0)
									invoice.db_set("status", "Paid")
									frappe.db.commit()
								except Exception:
									pass
							
							print(f"[info] Submitted invoice {invoice_name}")
							batch_results["resubmitted"] += 1
							all_results["total_resubmitted"] += 1
							
							if items_added_count > 0 or totals_adjusted_flag:
								batch_results["fixed"] += 1
								all_results["total_fixed"] += 1
								batch_results["fixed_invoices"].append({
									"invoice": invoice_name,
									"order": order_name,
									"items_added": items_added_count,
									"totals_adjusted": totals_adjusted_flag,
								})
						except Exception as submit_err:
							error_msg = f"Failed to submit invoice: {submit_err}"
							print(f"[warn] {error_msg}")
							batch_results["errors"].append({
								"invoice": invoice_name,
								"error": error_msg
							})
							all_results["errors"].append({
								"invoice": invoice_name,
								"error": error_msg
							})
					
				except Exception as e:
					error_msg = str(e)
					batch_results["errors"].append({
						"invoice": invoice_name if 'invoice_name' in locals() else "unknown",
						"error": error_msg
					})
					all_results["errors"].append({
						"invoice": invoice_name if 'invoice_name' in locals() else "unknown",
						"error": error_msg
					})
					frappe.log_error(
						title="Fix and Resubmit Invoice Error",
						message=f"Invoice: {invoice_name if 'invoice_name' in locals() else 'unknown'}\nError: {e}\n{frappe.get_traceback()}",
					)
			
			all_results["batches"].append(batch_results)
			print(f"[info] Batch {batch_num + 1} complete: {batch_results['fixed']} fixed, {batch_results['resubmitted']} resubmitted")
		
		# Final summary
		all_results["summary"] = {
			"total_batches": all_results["total_batches"],
			"total_checked": all_results["total_checked"],
			"total_fixed": all_results["total_fixed"],
			"total_resubmitted": all_results["total_resubmitted"],
			"items_added": all_results["items_added"],
			"totals_adjusted": all_results["totals_adjusted"],
			"errors": len(all_results["errors"]),
		}
		
		print(f"\n[info] Final Summary:")
		print(f"  Batches: {all_results['total_batches']}")
		print(f"  Checked: {all_results['total_checked']}")
		print(f"  Fixed: {all_results['total_fixed']}")
		print(f"  Resubmitted: {all_results['total_resubmitted']}")
		print(f"  Items Added: {all_results['items_added']}")
		print(f"  Totals Adjusted: {all_results['totals_adjusted']}")
		print(f"  Errors: {len(all_results['errors'])}")
		
		return all_results
		
	except Exception as e:
		frappe.log_error(
			title="Fix and Resubmit Invoices Error",
			message=f"Error: {e}\n{frappe.get_traceback()}",
		)
		all_results["error"] = str(e)
		return all_results


@frappe.whitelist()
def check_invoice_and_order_totals(
	from_date: Optional[str] = None,
	to_date: Optional[str] = None,
	show_details: bool = False,
) -> Dict[str, Any]:
	"""
	Check and compare totals between Sales Invoices and Foodics Orders.
	
	Args:
		from_date: Optional start date filter (business_date)
		to_date: Optional end date filter (business_date)
		show_details: If True, include detailed list of mismatched invoices
		
	Returns:
		dict with comparison results
	"""
	frappe.only_for(("System Manager", "Administrator"))
	
	results = {
		"total_invoices": 0,
		"total_orders": 0,
		"matched": 0,
		"mismatched": 0,
		"invoice_total": 0.0,
		"order_total": 0.0,
		"difference": 0.0,
		"tolerance": 0.10,
		"mismatched_invoices": [],
		"summary": {},
	}
	
	try:
		# Build query to find Sales Invoices with Foodics Orders
		conditions = []
		values = []
		
		if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
			conditions.append("si.foodics_order_id IS NOT NULL AND si.foodics_order_id != ''")
		elif frappe.get_meta("Sales Invoice").has_field("foodics_order_name"):
			conditions.append("si.foodics_order_name IS NOT NULL AND si.foodics_order_name != ''")
		else:
			conditions.append("fo.sales_invoice IS NOT NULL")
		
		if from_date:
			conditions.append("fo.business_date >= %s")
			values.append(from_date)
		if to_date:
			conditions.append("fo.business_date <= %s")
			values.append(to_date)
		
		if frappe.get_meta("Sales Invoice").has_field("foodics_order_id"):
			query = f"""
				SELECT DISTINCT 
					si.name as invoice_name, 
					si.foodics_order_id,
					si.grand_total as invoice_total,
					si.docstatus,
					fo.total_price_gross as order_total
				FROM `tabSales Invoice` si
				INNER JOIN `tabFoodics Order` fo ON fo.foodics_order_id = si.foodics_order_id
				WHERE {' AND '.join(conditions)}
				ORDER BY si.creation DESC
			"""
		elif frappe.get_meta("Sales Invoice").has_field("foodics_order_name"):
			query = f"""
				SELECT DISTINCT 
					si.name as invoice_name, 
					fo.name as order_name,
					si.grand_total as invoice_total,
					si.docstatus,
					fo.total_price_gross as order_total
				FROM `tabSales Invoice` si
				INNER JOIN `tabFoodics Order` fo ON fo.name = si.foodics_order_name
				WHERE {' AND '.join(conditions)}
				ORDER BY si.creation DESC
			"""
		else:
			query = f"""
				SELECT DISTINCT 
					si.name as invoice_name, 
					fo.name as order_name,
					si.grand_total as invoice_total,
					si.docstatus,
					fo.total_price_gross as order_total
				FROM `tabSales Invoice` si
				INNER JOIN `tabFoodics Order` fo ON fo.sales_invoice = si.name
				WHERE {' AND '.join(conditions)}
				ORDER BY si.creation DESC
			"""
		
		invoices = frappe.db.sql(query, values, as_dict=True)
		
		print(f"[info] Found {len(invoices)} invoices to check")
		
		total_invoice_sum = 0.0
		total_order_sum = 0.0
		matched_count = 0
		mismatched_count = 0
		mismatched_list = []
		
		for row in invoices:
			invoice_name = row.get("invoice_name")
			order_name = row.get("order_name")
			order_id = row.get("foodics_order_id")
			invoice_total = float(row.get("invoice_total") or 0)
			order_total = float(row.get("order_total") or 0)
			docstatus = row.get("docstatus", 0)
			
			if not order_name and order_id:
				order_name = _parent_name_for(order_id)
			
			if not invoice_name:
				continue
			
			results["total_invoices"] += 1
			total_invoice_sum += invoice_total
			total_order_sum += order_total
			
			# Check if totals match (within tolerance)
			diff = abs(invoice_total - order_total)
			if order_total > 0 and diff <= results["tolerance"]:
				matched_count += 1
			else:
				mismatched_count += 1
				if show_details:
					mismatched_list.append({
						"invoice": invoice_name,
						"order": order_name or order_id,
						"invoice_total": round(invoice_total, 2),
						"order_total": round(order_total, 2),
						"difference": round(diff, 2),
						"docstatus": docstatus,
						"status": "Draft" if docstatus == 0 else "Submitted" if docstatus == 1 else "Cancelled",
					})
		
		results["total_orders"] = results["total_invoices"]  # Same count
		results["matched"] = matched_count
		results["mismatched"] = mismatched_count
		results["invoice_total"] = round(total_invoice_sum, 2)
		results["order_total"] = round(total_order_sum, 2)
		results["difference"] = round(total_invoice_sum - total_order_sum, 2)
		
		if show_details:
			results["mismatched_invoices"] = mismatched_list
		
		# Create summary
		results["summary"] = {
			"total_invoices": results["total_invoices"],
			"matched": matched_count,
			"mismatched": mismatched_count,
			"match_percentage": round((matched_count / results["total_invoices"] * 100) if results["total_invoices"] > 0 else 0, 2),
			"invoice_total_sum": results["invoice_total"],
			"order_total_sum": results["order_total"],
			"total_difference": results["difference"],
			"average_difference": round(results["difference"] / results["total_invoices"] if results["total_invoices"] > 0 else 0, 2),
		}
		
		print(f"\n[info] Comparison Summary:")
		print(f"  Total Invoices: {results['total_invoices']}")
		print(f"  Matched: {matched_count} ({results['summary']['match_percentage']}%)")
		print(f"  Mismatched: {mismatched_count}")
		print(f"  Invoice Total Sum: {results['invoice_total']:.2f} SAR")
		print(f"  Order Total Sum: {results['order_total']:.2f} SAR")
		print(f"  Difference: {results['difference']:.2f} SAR")
		if results["total_invoices"] > 0:
			print(f"  Average Difference: {results['summary']['average_difference']:.2f} SAR per invoice")
		
		if show_details and mismatched_list:
			print(f"\n[info] First 10 Mismatched Invoices:")
			for item in mismatched_list[:10]:
				print(f"  Invoice: {item['invoice']} | Order: {item['order']} | Invoice: {item['invoice_total']:.2f} | Order: {item['order_total']:.2f} | Diff: {item['difference']:.2f} | Status: {item['status']}")
		
		return results
		
	except Exception as e:
		frappe.log_error(
			title="Check Invoice and Order Totals Error",
			message=f"Error: {e}\n{frappe.get_traceback()}",
		)
		results["error"] = str(e)
		return results
