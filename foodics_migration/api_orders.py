# apps/foodics_migration/foodics_migration/api_orders.py

from __future__ import annotations

import json
import time
from typing import Dict, Any, Iterable, Optional

import frappe
import requests
from urllib.parse import urlparse, parse_qs, urlencode

# at top:
from urllib.parse import quote

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
    base = _safe_base_url(cfg.base_url)
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
    base = _safe_base_url(cfg.base_url)
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

def _rebuild_next_with_include(cfg, next_url: str) -> str:
    """Foodics 'links.next' often misses include/per_page; re-attach them."""
    if not next_url:
        return next_url
    base = _safe_base_url(cfg.base_url)
    # next_url might be absolute ("https://api.foodics.com/v5/orders?page=2")
    # or relative (rare). We only keep the page param from it.
    pr = urlparse(next_url)
    qs = parse_qs(pr.query)
    page = qs.get("page", ["1"])[0]
    # Rebuild full list URL with our canonical params
    return (
        f"{base}/orders?"
        f"include={INCLUDE_Q}"
        f"&sort=id"
        f"&page={page}"
        f"&per_page={int(cfg.per_page)}"
    )

def _cfg() -> frappe._dict:
    """Load Foodics Settings (single doctype)."""
    doc = frappe.get_single("Foodics Settings")
    if not doc.foodics_base_url:
        frappe.throw("Foodics Settings: `foodics_base_url` is required.")
    if not doc.foodics_token:
        frappe.throw("Foodics Settings: `foodics_token` (Bearer) is required.")
    return frappe._dict(
        base_url=(doc.foodics_base_url or "").strip(),
        token=(doc.foodics_token or "").strip(),
        per_page=int(doc.foodics_per_page or DEFAULT_PER_PAGE),
        commit_every=int(getattr(doc, "commit_every_orders", None) or DEFAULT_COMMIT_EVERY),
    )


def _safe_base_url(base_url: str) -> str:
    """
    Build a safe base URL.
    - If stored as 'https://api.foodics.com/v5' -> use as-is.
    - If stored as 'api.foodics.com/v5'       -> prepend https://
    """
    if base_url.startswith("http://") or base_url.startswith("https://"):
        return base_url.rstrip("/")
    return "https://" + base_url.rstrip("/")


def _new_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
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

        pm = (pay.get("payment_method") or {})
        if "payment_method_id" in chp.as_dict():
            chp.payment_method_id = pm.get("id")
        if "payment_method_name" in chp.as_dict():
            chp.payment_method_name = pm.get("name")

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

def _paginate_orders(cfg: frappe._dict, s: requests.Session):
    base = _safe_base_url(cfg.base_url)
    page = 1
    print(f"[info] Page size: {cfg.per_page}")
    print(f"[info] Commit every: {cfg.commit_every} order(s)")
    print(f"[info] Using include={INCLUDE_Q}")

    url = (
        f"{base}/orders?"
        f"include={INCLUDE_Q}"
        f"&sort=id"
        f"&page={page}"
        f"&per_page={int(cfg.per_page)}"
    )

    while url:
        print(f"[info] === Page {page} ===")
        print(f"[debug] URL={url}")
        data = _get_with_backoff(s, url)
        rows = data.get("data") or []
        print(f"[debug] fetched {len(rows)} orders on this page")
        for row in rows:
            yield row
        links = data.get("links") or {}
        nxt = links.get("next")
        url = _rebuild_next_with_include(cfg, nxt) if nxt else None
        page += 1





def _upsert_one_order(order_json: Dict[str, Any]) -> str:
    print(_parent_log_header(order_json))
    oid = order_json.get("id")
    existing = _parent_name_for(oid)

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
            s_local = _new_session(cfg_local.token)
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
            s_local = _new_session(cfg_local.token)
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
        pm = (pay.get("payment_method") or {})
        chp.payment_method_id = pm.get("id")
        chp.payment_method_name = pm.get("name")
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
    Fetch ALL orders (all pages) with combos/payments/charges & products,
    store raw JSON, strict-mirror child rows, and commit every N orders.
    """
    try:
        cfg = _cfg()
        s = _new_session(cfg.token)

        count = 0
        for order in _paginate_orders(cfg, s):
            try:
                name = _upsert_one_order(order)
                count += 1
                # commit in batches
                if count % int(cfg.commit_every) == 0:
                    frappe.db.commit()
                    print(f"[info] ✔ committed batch (total so far: {count})")
            except Exception as e:
                frappe.db.rollback()
                print(f"[error] Order failed (id={order.get('id')}): {e}")
                raise

        # final commit
        frappe.db.commit()
        print(f"[info] Done. Total orders processed: {count}")

    except Exception as e:
        print(f"[error] Sync aborted: {e}")
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
