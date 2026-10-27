# apps/foodics_migration/foodics_migration/api_combo.py

from __future__ import annotations
import json
import time
from typing import Any, Dict, Iterable, List, Optional

import frappe
import requests
from frappe.model.rename_doc import rename_doc
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# ---------- SETTINGS ----------
DEFAULT_PER_PAGE = 100


def _cfg() -> frappe._dict:
    doc = frappe.get_single("Foodics Settings")
    if not doc.foodics_base_url:
        frappe.throw("Foodics Settings: `foodics_base_url` is required.")
    if not doc.foodics_token:
        frappe.throw("Foodics Settings: `foodics_token` is required.")
    return frappe._dict(
        base_url=doc.foodics_base_url.strip(),
        token=doc.foodics_token.strip(),
        per_page=int(getattr(doc, "foodics_per_page", None) or DEFAULT_PER_PAGE),
        foodics_price_list=getattr(doc, "foodics_price_list", None),
        foodics_currency=getattr(doc, "foodics_currency", None),
        vat_rate=getattr(doc, "vat_rate", 0.15),
    )


def _safe_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    return base


def _new_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "ERPNext-Foodics-ComboSync/1.0",
    })
    return s


def _get_with_backoff(s: requests.Session, url: str) -> Dict[str, Any]:
    backoff = 2.0
    for attempt in range(6):
        try:
            resp = s.get(url, timeout=60)
        except requests.RequestException:
            if attempt == 5:
                raise
            time.sleep(backoff)
            backoff *= 1.6
            continue

        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            ra = resp.headers.get("Retry-After")
            try:
                sleep_for = float(ra) if ra is not None else backoff
            except ValueError:
                sleep_for = backoff
            print(f"[warn] {resp.status_code} on {url} — retrying in {sleep_for:.1f}s")
            time.sleep(sleep_for)
            backoff *= 1.6
            continue

        resp.raise_for_status()
        return resp.json()
    raise requests.HTTPError(f"Failed to GET after retries: {url}")


# ---------- ITEM GROUP HELPER ----------

def _ensure_item_group(name: str, parent: Optional[str] = "All Item Groups", foodics_category_id: Optional[str] = None, foodics_category_data: Optional[dict] = None) -> str:
    if not name:
        name = "Unassigned (Foodics)"

    # Check by foodics_category_id first if provided
    if foodics_category_id and frappe.get_meta("Item Group").has_field("foodics_category_id"):
        existing = frappe.db.get_value(
            "Item Group",
            {"foodics_category_id": foodics_category_id},
            "name",
        )
        if existing:
            return existing

    # Check by name
    existing = frappe.db.get_value("Item Group", {"item_group_name": name}, "name")
    if existing:
        # Update with Foodics ID if not set
        if foodics_category_id and frappe.get_meta("Item Group").has_field("foodics_category_id"):
            try:
                ig_doc = frappe.get_doc("Item Group", existing)
                if not ig_doc.get("foodics_category_id"):
                    ig_doc.foodics_category_id = foodics_category_id
                if foodics_category_data and frappe.get_meta("Item Group").has_field("foodics_category_data"):
                    ig_doc.foodics_category_data = json.dumps(foodics_category_data, ensure_ascii=False)
                ig_doc.save(ignore_permissions=True)
            except Exception:
                pass
        return existing

    if parent and not frappe.db.exists("Item Group", parent):
        root = frappe.get_doc({
            "doctype": "Item Group",
            "item_group_name": parent,
            "is_group": 1,
            "parent_item_group": None,
        })
        root.insert(ignore_permissions=True)

    doc = frappe.get_doc({
        "doctype": "Item Group",
        "item_group_name": name,
        "is_group": 0,
        "parent_item_group": parent if frappe.db.exists("Item Group", parent) else None,
    })
    
    # Set Foodics fields if available
    if foodics_category_id and frappe.get_meta("Item Group").has_field("foodics_category_id"):
        doc.foodics_category_id = foodics_category_id
    if foodics_category_data and frappe.get_meta("Item Group").has_field("foodics_category_data"):
        doc.foodics_category_data = json.dumps(foodics_category_data, ensure_ascii=False)
    
    doc.insert(ignore_permissions=True)
    print(f"    · created Item Group: {doc.name} (parent={parent})")
    return doc.name


# ---------- CUSTOM FIELDS ----------

def _ensure_combo_custom_fields() -> None:
    """Create custom fields on Item to store Foodics combo linkage and raw JSON."""
    defs = {
        "Item": [
            {
                "fieldname": "foodics_combo_id",
                "label": "Foodics Combo ID",
                "fieldtype": "Data",
                "insert_after": "item_code",
                "read_only": 1,
                "unique": 1,
                "translatable": 0,
                "hidden": 0,
            },
            {
                "fieldname": "foodics_combo_data",
                "label": "Foodics Combo Raw JSON",
                "fieldtype": "Long Text",
                "insert_after": "foodics_combo_id",
                "read_only": 1,
                "translatable": 0,
                "hidden": 0,
            },
        ]
    }
    # create only if missing
    to_create = []
    meta = frappe.get_meta("Item")
    for f in defs["Item"]:
        if not meta.get_field(f["fieldname"]):
            to_create.append(f)
    if to_create:
        print(f"[setup] Creating missing Item custom fields: {[f['fieldname'] for f in to_create]}")
        create_custom_fields({"Item": to_create}, ignore_validate=True)
        frappe.clear_cache(doctype="Item")


# ---------- VAT + DESCRIPTION HELPERS ----------

def _first_size_id(combo: Dict[str, Any]) -> Optional[str]:
    sizes = combo.get("sizes") or []
    if not sizes:
        return None
    sid = (sizes[0] or {}).get("id")
    return sid or None


def _price_exclusive_from_inclusive(price_inclusive: Optional[float], vat_rate: float) -> Optional[float]:
    if price_inclusive is None:
        return None
    try:
        base = float(price_inclusive) / (1.0 + float(vat_rate or 0.0))
        return round(base, 2)
    except Exception:
        return None


def _compute_combo_price_exclusive(cfg: frappe._dict, combo: Dict[str, Any]) -> Optional[float]:
    """
    Foodics combo prices are VAT-inclusive in your setup.
    Convert to VAT-exclusive for ERPNext Item Price using cfg.vat_rate (e.g., 0.15).
    Source price = top-level combo['price'] (if present) else the heuristic sum we compute in _extract_combo_price.
    """
    price_inclusive = combo.get("price")
    if price_inclusive is None:
        price_inclusive = _extract_combo_price(combo)
    return _price_exclusive_from_inclusive(price_inclusive, float(cfg.vat_rate or 0.0))


def _collect_combo_item_names(combo: Dict[str, Any]) -> List[str]:
    """
    Collect the visible combo item names from `items[*].name`.
    - De-duplicate while preserving order.
    - Ignores empty/None names.
    """
    names: List[str] = []
    seen = set()
    for item in (combo.get("items") or []):
        n = (item.get("name") or "").strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    return names


# ---------- COMBOS: fetch + normalize + upsert ----------

COMBOS_INCLUDE = (
    "category,tags,timed_events,branches,sizes,items.options.products,"
    "items.options.products.category,items.options.branches,items.options.price_tags,groups"
)


def _resolve_pricing_defaults(cfg: frappe._dict) -> frappe._dict:
    price_list = cfg.foodics_price_list or frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
    currency = cfg.foodics_currency or frappe.db.get_default("currency") or "SAR"
    return frappe._dict(price_list=price_list, currency=currency, vat_rate=float(cfg.vat_rate or 0.0))


def _extract_combo_price(combo: Dict[str, Any]) -> Optional[float]:
    """
    Prices for combos are nested per option per combo-size:
      items[*].options[*].sizes[<combo_size_id>].price
    Heuristic: pick the FIRST combo size and sum each option's price for that size.
    If nothing found, return None (avoid writing 0.0).
    """
    sizes = combo.get("sizes") or []
    if not sizes:
        return None
    first_size_id = (sizes[0] or {}).get("id")
    if not first_size_id:
        return None

    total = 0.0
    found_any = False
    for item in (combo.get("items") or []):
        for opt in (item.get("options") or []):
            size_map = opt.get("sizes") or {}
            slot = size_map.get(first_size_id)
            if isinstance(slot, dict) and slot.get("price") is not None:
                try:
                    total += float(slot.get("price") or 0.0)
                    found_any = True
                except (TypeError, ValueError):
                    pass
    return round(total, 2) if found_any else None


def _iter_foodics_combos(cfg: frappe._dict, s: requests.Session) -> Iterable[Dict[str, Any]]:
    """
    Iterator over Foodics combos. We rebuild the page URL every time to keep ?include=...
    because some backends drop query params on links.next.
    Also: copy inline fields (category, items...) since Foodics returns denormalized combos.
    """
    base = _safe_base_url(cfg.base_url)
    page = 1
    per_page = cfg.per_page

    def page_url(p: int) -> str:
        return f"{base}/combos?include={COMBOS_INCLUDE}&per_page={per_page}&page={p}"

    def index_included(included: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        idx: Dict[str, Dict[str, Any]] = {}
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

    def normalize_combo(c: Dict[str, Any], idx: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        # IMPORTANT: copy inline fields too (payload can be denormalized)
        attrs = c.get("attributes") or c
        out = dict(attrs)
        out["id"] = c.get("id") or attrs.get("id") or attrs.get("uuid")

        # category: prefer inline; else resolve relationship via included
        cat = out.get("category")
        if not (isinstance(cat, dict) and cat.get("name")):
            rel = ((c.get("relationships") or {}).get("category") or {}).get("data") or {}
            cid = rel.get("id")
            if cid and "categories" in idx and cid in idx["categories"]:
                out["category"] = {"id": cid, "name": idx["categories"][cid].get("name")}

        # sizes: keep inline if present; otherwise resolve via relationships/included
        out["sizes"] = out.get("sizes") or []
        if not out["sizes"]:
            sz_rel = ((c.get("relationships") or {}).get("sizes") or {}).get("data") or []
            sizes: List[Dict[str, Any]] = []
            for r in sz_rel:
                sid = r.get("id")
                if sid and "sizes" in idx and sid in idx["sizes"]:
                    sizes.append(idx["sizes"][sid])
            out["sizes"] = sizes

        # branches/tags/timed_events/groups: keep what's inline if provided
        out["branches"] = out.get("branches") or []
        out["tags"] = out.get("tags") or []
        out["timed_events"] = out.get("timed_events") or []
        out["groups"] = out.get("groups") or []

        # items/options are embedded inline in this payload – keep them as-is
        out["items"] = out.get("items") or []

        # compute representative price (AS-IS) if not given top-level
        out["price"] = out.get("price")
        if out["price"] is None:
            out["price"] = _extract_combo_price(out)

        # SKU passthrough (prefer trimmed string)
        sku = (out.get("sku") or "").strip()
        out["sku"] = sku or None

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
            print(f"[combo] {nc.get('id')} -> sku={nc.get('sku')} category={cname} price={nc.get('price')}")
            yield nc

        # Use links.next only as a signal; rebuild URL to preserve includes
        if not (data.get("links") or {}).get("next"):
            break
        page += 1


# ---------- PRICING (VAT-EXCLUSIVE WRITER) ----------

def _replace_item_price_vat_exclusive(cfg: frappe._dict, item_code: str, price_inclusive: Optional[float]) -> None:
    """
    Replace Item Price with VAT-exclusive number derived from a VAT-inclusive source.
    Uses Foodics Settings price list / currency.
    """
    if price_inclusive is None:
        return

    defs = _resolve_pricing_defaults(cfg)
    price_exclusive = _price_exclusive_from_inclusive(price_inclusive, defs.vat_rate)
    if price_exclusive is None:
        return

    # delete existing Item Price rows for this price_list
    names = frappe.get_all(
        "Item Price",
        filters={"item_code": item_code, "price_list": defs.price_list},
        pluck="name",
    )
    for n in names:
        frappe.delete_doc("Item Price", n, ignore_permissions=True, force=1)

    # create fresh Item Price (VAT-exclusive)
    doc = frappe.get_doc({
        "doctype": "Item Price",
        "item_code": item_code,
        "price_list": defs.price_list,
        "currency": defs.currency,
        "price_list_rate": float(price_exclusive),
    })
    doc.insert(ignore_permissions=True)
    print(f"    · replaced Item Price {defs.price_list} = {price_exclusive} {defs.currency} (excl. VAT)")


# ---------- UPSERT ----------

def _rename_item_to_sku_if_needed(item_doc, sku: Optional[str]) -> None:
    """Rename Item (and its link name) to SKU if available and different."""
    if not sku:
        return
    if item_doc.name == sku:
        return
    if frappe.db.exists("Item", sku):
        # If SKU already exists as another Item, do not rename to avoid merge.
        print(f"    · skip rename: target SKU already exists: {sku}")
        return
    old_name = item_doc.name
    print(f"    · renaming Item {old_name} → {sku}")
    rename_doc("Item", old_name, sku, force=True, merge=False, ignore_permissions=True)
    # reload after rename
    item_doc.reload()


def _upsert_combo_as_item(cfg: frappe._dict, combo: Dict[str, Any], update_if_exists: bool = False) -> None:
    """
    Create/Update ERPNext Item from a Foodics Combo.
    - Use SKU as Item.item_code (fallback to combo.id if SKU is missing).
    - Match existing Item by custom field 'foodics_combo_id' (preferred),
      then by item_code==SKU, then by item_code==combo.id.
    - Set Item Group from combo.category.name (auto-create when missing).
    - Set Item Name from combo.name (if provided).
    - Replace Item Price with VAT-EXCLUSIVE value converted from inclusive price.
    - Store raw combo JSON in 'foodics_combo_data' and combo ID in 'foodics_combo_id'.
    - Append a single 'Includes: ...' line in description with contained **item** names.
    """
    _ensure_combo_custom_fields()

    cid = (combo.get("id") or "").strip()
    sku = (combo.get("sku") or "").strip() or None
    target_code = sku or cid
    if not cid:
        print("[warn] combo without id, skipping")
        return

    # Locate existing Item
    item_name = None
    if frappe.get_meta("Item").has_field("foodics_combo_id"):
        item_name = frappe.db.get_value("Item", {"foodics_combo_id": cid}, "name")
    if not item_name and sku:
        item_name = frappe.db.get_value("Item", {"item_code": sku}, "name")
    if not item_name:
        item_name = frappe.db.get_value("Item", {"item_code": cid}, "name")

    if item_name:
        if not update_if_exists:
            print(f"[skip] Combo Item exists (update disabled): {item_name} (Foodics {cid})")
            return
        item_doc = frappe.get_doc("Item", item_name)
    else:
        # Create new Item with item_code = SKU (or ID fallback)
        item_doc = frappe.get_doc({
            "doctype": "Item",
            "item_code": target_code,
            "item_name": combo.get("name") or f"Combo {cid}",
            "item_group": "All Item Groups",  # temp; corrected below
            "is_stock_item": 0,
            "include_item_in_manufacturing": 0,
            "maintain_stock": 0,
        })
        item_doc.insert(ignore_permissions=True)
        print(f"[create] Item for Combo {cid} -> {item_doc.name}")

    # If this was found by old id name, try renaming to SKU
    if sku:
        _rename_item_to_sku_if_needed(item_doc, sku)

    # Refresh descriptive name
    if combo.get("name") and item_doc.item_name != combo.get("name"):
        item_doc.item_name = combo.get("name")

    # Set Item Group from combo.category
    category = combo.get("category") or {}
    cat_name = category.get("name")
    cat_id = category.get("id")
    
    # Use local function to ensure Item Group with Foodics ID tracking
    ig = _ensure_item_group(
        cat_name or "Unassigned (Foodics)",
        parent="All Item Groups",
        foodics_category_id=cat_id,
        foodics_category_data=category if category else None
    )
    if item_doc.item_group != ig:
        item_doc.item_group = ig
        print(f"    · set item_group = {ig}")

    # Persist Foodics combo id + raw payload
    if frappe.get_meta("Item").has_field("foodics_combo_id"):
        item_doc.set("foodics_combo_id", cid)
    if frappe.get_meta("Item").has_field("foodics_combo_data"):
        item_doc.set("foodics_combo_data", json.dumps(combo, ensure_ascii=False))

    # NEW: build a descriptive line-up from combo **item** names
    names = _collect_combo_item_names(combo)
    if names:
        includes_line = f"{item_doc.item_name}\n"+"يشمل / Includes: " + "\n".join(names)
        base_desc = (item_doc.get("description") or "").strip()
        if includes_line not in base_desc:
            item_doc.description = (includes_line).strip()

    item_doc.save(ignore_permissions=True)

    # Replace Item Price with VAT-exclusive value computed from inclusive source
    _replace_item_price_vat_exclusive(cfg, item_doc.name, combo.get("price"))


# ---------- PUBLIC TASKS ----------

@frappe.whitelist()
def sync_foodics_combos():
    """
    Fetch Foodics combos (with deep includes) and upsert each as an ERPNext Item:
    - Item Code uses Foodics combo SKU (fallback to combo ID)
    - Item Group from combo category
    - Price written as VAT-EXCLUSIVE (converted from Foodics inclusive price)
    - Raw combo JSON and combo ID stored in custom fields
    - Description lists included **item** names
    """
    cfg = _cfg()
    s = _new_session(cfg.token)
    
    # Get update setting from Foodics Settings
    settings = frappe.get_single("Foodics Settings")
    update_if_exists = bool(getattr(settings, "update_combos_if_exists", 0))

    processed = 0
    skipped = 0
    created = 0
    updated = 0
    try:
        for combo in _iter_foodics_combos(cfg, s):
            cid = (combo.get("id") or "").strip()
            # Check if exists
            item_name = None
            if frappe.get_meta("Item").has_field("foodics_combo_id"):
                item_name = frappe.db.get_value("Item", {"foodics_combo_id": cid}, "name")
            was_existing = item_name is not None
            
            _upsert_combo_as_item(cfg, combo, update_if_exists=update_if_exists)
            
            if was_existing and not update_if_exists:
                skipped += 1
            elif was_existing:
                updated += 1
                processed += 1
            else:
                created += 1
                processed += 1

        frappe.db.commit()
        print(f"[info] Done. Created: {created}, Updated: {updated}, Skipped: {skipped}")

    except Exception as e:
        frappe.db.rollback()
        print(f"[error] Combo sync aborted: {e}")
        raise


@frappe.whitelist()
def repair_combo_items_set_item_code_to_sku():
    """
    Repair utility:
    - For existing Items linked to Foodics combos (by foodics_combo_id or item_code==combo.id),
      rename the Item to the Foodics SKU (when present and not colliding),
      ensure combo custom fields are populated, update price as VAT-EXCLUSIVE, update description with item names.
    """
    cfg = _cfg()
    s = _new_session(cfg.token)
    _ensure_combo_custom_fields()

    fixed = 0
    skipped = 0

    try:
        for combo in _iter_foodics_combos(cfg, s):
            cid = (combo.get("id") or "").strip()
            sku = (combo.get("sku") or "").strip() or None
            if not cid:
                continue

            item_name = None
            if frappe.get_meta("Item").has_field("foodics_combo_id"):
                item_name = frappe.db.get_value("Item", {"foodics_combo_id": cid}, "name")
            if not item_name and frappe.db.exists("Item", cid):
                item_name = cid  # legacy naming with id

            # If neither found, try by SKU (may have been created with SKU already)
            if not item_name and sku and frappe.db.exists("Item", sku):
                item_name = sku

            if not item_name:
                print(f"[repair] no Item found for combo {cid}, creating new...")
                _upsert_combo_as_item(cfg, combo)
                fixed += 1
                continue

            item_doc = frappe.get_doc("Item", item_name)
            before_name = item_doc.name

            # Rename to SKU if possible
            if sku:
                _rename_item_to_sku_if_needed(item_doc, sku)

            # Ensure fields and price
            cat_name = (combo.get("category") or {}).get("name")
            ig = _ensure_item_group(cat_name or "Unassigned (Foodics)", parent="All Item Groups")
            if item_doc.item_group != ig:
                item_doc.item_group = ig

            if frappe.get_meta("Item").has_field("foodics_combo_id"):
                item_doc.set("foodics_combo_id", cid)
            if frappe.get_meta("Item").has_field("foodics_combo_data"):
                item_doc.set("foodics_combo_data", json.dumps(combo, ensure_ascii=False))

            if combo.get("name"):
                item_doc.item_name = combo.get("name")

            # NEW: description from combo **item** names
            names = _collect_combo_item_names(combo)
            if names:
                includes_line = "Includes: " + ", ".join(names)
                base_desc = (item_doc.get("description") or "").strip()
                if includes_line not in base_desc:
                    item_doc.description = (base_desc + ("\n\n" if base_desc else "") + includes_line).strip()

            item_doc.save(ignore_permissions=True)

            # NEW: price as VAT-exclusive
            _replace_item_price_vat_exclusive(cfg, item_doc.name, combo.get("price"))

            after_name = item_doc.name
            if before_name != after_name:
                print(f"[repair] renamed {before_name} → {after_name}")
            fixed += 1

        frappe.db.commit()
        print(f"[info] Repair done. items_fixed={fixed} skipped={skipped}")

    except Exception as e:
        frappe.db.rollback()
        print(f"[error] Repair aborted: {e}")
        raise
