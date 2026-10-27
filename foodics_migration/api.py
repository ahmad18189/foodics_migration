import json
from typing import Optional

import frappe

from .utils import (
    FoodicsConfig,
    load_foodics_settings,
    make_foodics_session,
    paginated_get,
)


def _resolve_item_group_by_foodics_category_id(foodics_category_id: Optional[str], category_data: Optional[dict] = None, update_if_exists: bool = False) -> str:
    """
    Resolve or create Item Group from Foodics category.
    Returns Item Group name.
    Always creates missing Item Groups with "All Item Groups" as parent.
    """
    if not foodics_category_id:
        return "All Item Groups"
    
    # Ensure "All Item Groups" exists
    if not frappe.db.exists("Item Group", "All Item Groups"):
        try:
            frappe.get_doc({
                "doctype": "Item Group",
                "item_group_name": "All Item Groups",
                "is_group": 1,
                "parent_item_group": None,
            }).insert(ignore_permissions=True)
        except Exception:
            pass  # May already exist
    
    # Check if Item Group exists by foodics_category_id
    # Use get_value with proper field check
    if frappe.get_meta("Item Group").has_field("foodics_category_id"):
        ig = frappe.db.get_value(
            "Item Group",
            filters={"foodics_category_id": foodics_category_id},
            fieldname="name",
        )
    else:
        ig = None
    
    if ig:
        # Update if enabled and category_data provided
        if update_if_exists and category_data:
            try:
                ig_doc = frappe.get_doc("Item Group", ig)
                if frappe.get_meta("Item Group").has_field("foodics_category_id"):
                    ig_doc.foodics_category_id = foodics_category_id
                if frappe.get_meta("Item Group").has_field("foodics_category_data"):
                    ig_doc.foodics_category_data = json.dumps(category_data, ensure_ascii=False)
                # Ensure parent is set correctly
                if not ig_doc.parent_item_group or ig_doc.parent_item_group != "All Item Groups":
                    ig_doc.parent_item_group = "All Item Groups"
                ig_doc.save(ignore_permissions=True)
            except Exception:
                pass  # Continue if update fails
        return ig
    
    # Create new Item Group if category_data provided or if we have category_id
    category_name = None
    if category_data:
        category_name = category_data.get("name") or f"Category {foodics_category_id}"
    else:
        # If no category_data but we have category_id, create with a default name
        category_name = f"Category {foodics_category_id}"
    
    if category_name:
        try:
            # Check if Item Group exists by name
            existing_by_name = frappe.db.get_value("Item Group", {"item_group_name": category_name}, "name")
            if existing_by_name:
                # Update existing with Foodics ID and ensure parent is correct
                ig_doc = frappe.get_doc("Item Group", existing_by_name)
                if frappe.get_meta("Item Group").has_field("foodics_category_id"):
                    ig_doc.foodics_category_id = foodics_category_id
                if frappe.get_meta("Item Group").has_field("foodics_category_data") and category_data:
                    ig_doc.foodics_category_data = json.dumps(category_data, ensure_ascii=False)
                # Ensure parent is set correctly
                if not ig_doc.parent_item_group or ig_doc.parent_item_group != "All Item Groups":
                    ig_doc.parent_item_group = "All Item Groups"
                ig_doc.save(ignore_permissions=True)
                return ig_doc.name
            else:
                # Create new Item Group with "All Item Groups" as parent
                ig_doc = frappe.get_doc({
                    "doctype": "Item Group",
                    "item_group_name": category_name,
                    "parent_item_group": "All Item Groups",
                    "is_group": 0,
                })
                if frappe.get_meta("Item Group").has_field("foodics_category_id"):
                    ig_doc.foodics_category_id = foodics_category_id
                if frappe.get_meta("Item Group").has_field("foodics_category_data") and category_data:
                    ig_doc.foodics_category_data = json.dumps(category_data, ensure_ascii=False)
                ig_doc.insert(ignore_permissions=True)
                frappe.logger().info(f"＋ Created Item Group: {ig_doc.name} (Foodics {foodics_category_id}) with parent 'All Item Groups'")
                return ig_doc.name
        except Exception as e:
            frappe.log_error(
                title="Item Group Creation Error",
                message=f"Failed to create Item Group for category {foodics_category_id}: {e}\n{frappe.get_traceback()}",
            )
            frappe.logger().error(f"❌ Failed to create Item Group for category {foodics_category_id}: {e}")
    
    # Fallback to default
    return "All Item Groups"


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
        "item_code": item_code,
        "price_list": cfg.foodics_price_list,
        "price_list_rate": rate,
        "currency": cfg.foodics_currency,
    }
    if existing_name:
        item_price = frappe.get_doc("Item Price", existing_name)
        item_price.update(doc)
        item_price.save(ignore_permissions=True)
    else:
        frappe.get_doc(doc).insert(ignore_permissions=True)


def _replace_item_price(cfg: FoodicsConfig, item_code: str, gross_price: float):
    """Replace Item Price with VAT-exclusive value."""
    from foodics_migration.api_combo import _price_exclusive_from_inclusive
    
    vat_rate = getattr(frappe.get_single("Foodics Settings"), "vat_rate", 0.15)
    net_price = _price_exclusive_from_inclusive(gross_price, vat_rate)
    
    existing_name = frappe.db.get_value(
        "Item Price",
        filters={"item_code": item_code, "price_list": cfg.foodics_price_list},
        fieldname="name",
    )

    doc = {
        "doctype": "Item Price",
        "item_code": item_code,
        "price_list": cfg.foodics_price_list,
        "price_list_rate": net_price,
        "currency": cfg.foodics_currency,
    }
    
    if existing_name:
        item_price = frappe.get_doc("Item Price", existing_name)
        item_price.update(doc)
        item_price.save(ignore_permissions=True)
    else:
        frappe.get_doc(doc).insert(ignore_permissions=True)

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


def _upsert_item(cfg: FoodicsConfig, product: dict, update_if_exists: bool = False):
    fid = product.get("id")
    if not fid:
        return

    sku = (product.get("sku") or "").strip() or fid
    name = product.get("name") or sku
    maintain_stock = 1 if product.get("is_stock_product") else 0
    cost = float(product.get("cost") or 0.0)
    price = float(product.get("price") or 0.0)
    
    # Extract category - handle both direct object and nested structure
    category = product.get("category") or {}
    category_id = None
    category_data = None
    
    # Debug: log the category structure
    if product.get("category"):
        frappe.logger().debug(f"Item {sku} category structure: {type(product.get('category'))} = {product.get('category')}")
    
    if category:
        # Category might be a dict with id, or just an id string
        if isinstance(category, dict):
            category_id = category.get("id")
            category_data = category
            # Also check for nested category object
            if not category_id and category.get("category"):
                nested_cat = category.get("category")
                if isinstance(nested_cat, dict):
                    category_id = nested_cat.get("id")
                    category_data = nested_cat
        elif isinstance(category, str):
            category_id = category
        else:
            # Try to get category_id directly from product
            category_id = product.get("category_id")
    
    # If category_id is still None, try to get it from product directly
    if not category_id:
        category_id = product.get("category_id")
    
    # If we have category_id but no category_data, try to fetch it
    if category_id and not category_data:
        # Try to find existing Item Group to get the data
        existing_ig = frappe.db.get_value(
            "Item Group",
            filters={"foodics_category_id": category_id},
            fieldname=["name", "foodics_category_data"],
            as_dict=True,
        )
        if existing_ig and existing_ig.get("foodics_category_data"):
            try:
                category_data = json.loads(existing_ig.get("foodics_category_data"))
            except Exception:
                pass
    
    # Resolve Item Group - will create if missing with "All Item Groups" as parent
    item_group = _resolve_item_group_by_foodics_category_id(
        category_id,
        category_data=category_data if category_data else category if category else None,
        update_if_exists=update_if_exists
    )
    
    # Log if Item Group was created or if using default
    if item_group == "All Item Groups":
        if category_id:
            frappe.logger().warning(f"⚠️ Item {sku} (Foodics {fid}): Category {category_id} found but Item Group resolution failed. Check if Item Groups were synced first.")
        else:
            frappe.logger().info(f"ℹ️ Item {sku} (Foodics {fid}): No category assigned in Foodics data, using default Item Group 'All Item Groups'")
    elif category_id:
        frappe.logger().debug(f"✓ Item {sku} (Foodics {fid}): Assigned to Item Group '{item_group}' (Category {category_id})")
    
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
        if not update_if_exists:
            frappe.logger().info(f"⊘ Skipped Item (exists, update disabled): {sku} (Foodics {fid})")
            return
        item = frappe.get_doc("Item", existing["name"])
        item.update(payload)
        item.save(ignore_permissions=True)
        frappe.logger().info(f"↻ Updated Item: {sku} (Foodics {fid})")
        item_code = item.item_code
    else:
        by_code = _get_item_by_item_code(sku)
        if by_code:
            if not update_if_exists:
                frappe.logger().info(f"⊘ Skipped Item (exists by code, update disabled): {sku}")
                return
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


def _sync_item_groups_internal(cfg: FoodicsConfig, settings, update_if_exists: bool):
    """
    Internal function to sync Item Groups (called by sync_foodics_products).
    """
    logger = frappe.logger()
    logger.setLevel(cfg.log_level)
    session = make_foodics_session(cfg)
    
    count = 0
    skipped = 0
    created = 0
    updated = 0
    try:
        for category in paginated_get(
            cfg,
            session,
            endpoint="categories",
            params=None,
        ):
            try:
                category_id = category.get("id")
                if not category_id:
                    continue
                
                # Check if Item Group exists
                existing = frappe.db.get_value(
                    "Item Group",
                    filters={"foodics_category_id": category_id},
                    fieldname="name",
                )
                was_existing = existing is not None
                
                # Use the resolve function to create/update
                item_group_name = _resolve_item_group_by_foodics_category_id(
                    category_id,
                    category_data=category,
                    update_if_exists=update_if_exists
                )
                
                if was_existing and not update_if_exists:
                    skipped += 1
                    logger.info(f"⊘ Skipped Item Group (exists, update disabled): {item_group_name} (Foodics {category_id})")
                elif was_existing:
                    updated += 1
                    count += 1
                    logger.info(f"↻ Updated Item Group: {item_group_name} (Foodics {category_id})")
                else:
                    created += 1
                    count += 1
                    logger.info(f"＋ Created Item Group: {item_group_name} (Foodics {category_id})")
                
                # commit batching
                if (count + skipped) % cfg.commit_every_n_records == 0:
                    frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.log_error(
                    title="Foodics Item Group Sync Error",
                    message=frappe.get_traceback(),
                )
                # continue with next record
        # final commit if remainder
        frappe.db.commit()
        logger.info(f"✅ Finished Foodics item groups sync. Created: {created}, Updated: {updated}, Skipped: {skipped}")
    except Exception:
        frappe.db.rollback()
        raise


@frappe.whitelist()
def sync_foodics_item_groups():
    """
    Pull Foodics categories and upsert ERPNext Item Groups.
    This should be run before syncing products.

    Run via:
      bench --site <yoursite> execute foodics_migration.api.sync_foodics_item_groups
    """
    frappe.only_for(("System Manager", "Administrator"))

    cfg = load_foodics_settings()
    settings = frappe.get_single("Foodics Settings")
    update_if_exists = bool(getattr(settings, "update_products_if_exists", 0))
    
    _sync_item_groups_internal(cfg, settings, update_if_exists)


@frappe.whitelist()
def sync_foodics_products():
    """
    Pull Foodics products (with category) and upsert ERPNext Items & Item Prices.
    This function will automatically sync Item Groups first if needed.

    Run via:
      bench --site <yoursite> execute foodics_migration.api.sync_foodics_products
    """
    frappe.only_for(("System Manager", "Administrator"))

    cfg = load_foodics_settings()
    logger = frappe.logger()
    logger.setLevel(cfg.log_level)
    
    # Get update setting from Foodics Settings
    settings = frappe.get_single("Foodics Settings")
    update_if_exists = bool(getattr(settings, "update_products_if_exists", 0))

    # First, sync Item Groups (categories) before syncing products
    logger.info("🔄 Syncing Item Groups (categories) first...")
    try:
        # Call the sync function directly (not via whitelist to avoid permission issues)
        _sync_item_groups_internal(cfg, settings, update_if_exists)
        logger.info("✅ Item Groups sync completed. Proceeding with products sync...")
        # Small delay to ensure Item Groups are committed
        frappe.db.commit()
    except Exception as e:
        logger.warning(f"⚠️ Item Groups sync had issues, continuing with products: {e}")
        frappe.log_error(
            title="Foodics Item Group Sync Warning",
            message=f"Item Group sync failed but continuing with products: {frappe.get_traceback()}",
        )

    session = make_foodics_session(cfg)

    count = 0
    skipped = 0
    created = 0
    updated = 0
    try:
        for prod in paginated_get(
            cfg,
            session,
            endpoint="products",
            params={"include": "category"},
        ):
            try:
                fid = prod.get("id")
                existing = _get_item_by_foodics_id(fid) if fid else None
                was_existing = existing is not None
                
                _upsert_item(cfg, prod, update_if_exists=update_if_exists)
                
                if was_existing and not update_if_exists:
                    skipped += 1
                elif was_existing:
                    updated += 1
                else:
                    created += 1
                count += 1
                
                # commit batching
                if (count + skipped) % cfg.commit_every_n_records == 0:
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
        logger.info(f"✅ Finished Foodics products sync. Created: {created}, Updated: {updated}, Skipped: {skipped}")
    except Exception:
        frappe.db.rollback()
        raise


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
    """Return first non-empty string from vals, or empty string."""
    for v in vals:
        if v:
            s = str(v).strip()
            if s:
                return s
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

def _upsert_customer(cfg: FoodicsConfig, s, cust: dict, update_if_exists: bool = False) -> str:
    """
    Returns ERPNext Customer name.
    - 1:1 strict mapping
    - Always enabled
    - No PII (skip phone/email)
    """
    fid = cust.get("id")
    if not fid:
        return ""

    from frappe.utils import now_datetime
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
        if not update_if_exists:
            frappe.logger().info(f"⊘ Skipped Customer (exists, update disabled): {existing['name']} (Foodics {fid})")
            return existing["name"]
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

    from frappe.utils import now_datetime
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
    from foodics_migration.utils import ensure_custom_fields_for_foodics, assert_settings_for_parties
    ensure_custom_fields_for_foodics()

    cfg = load_foodics_settings()
    logger = frappe.logger()
    logger.setLevel(cfg.log_level)

    # Validate party-related settings
    assert_settings_for_parties(cfg)
    s = frappe.get_single("Foodics Settings")
    
    # Get update setting from Foodics Settings
    update_if_exists = bool(getattr(s, "update_customers_if_exists", 0))

    session = make_foodics_session(cfg)
    count = 0
    skipped = 0
    created = 0
    updated = 0

    try:
        for cust in paginated_get(cfg, session, endpoint="customers", params=None):
            try:
                fid = cust.get("id")
                existing = _get_customer_by_foodics_id(fid) if fid else None
                was_existing = existing is not None
                
                _upsert_customer(cfg, s, cust, update_if_exists=update_if_exists)
                
                if was_existing and not update_if_exists:
                    skipped += 1
                elif was_existing:
                    updated += 1
                else:
                    created += 1
                count += 1
                
                if (count + skipped) % cfg.commit_every_n_records == 0:
                    frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.log_error(title="Foodics Customer Sync Error", message=frappe.get_traceback())
        frappe.db.commit()
        logger.info(f"✅ Finished Foodics customer sync. Created: {created}, Updated: {updated}, Skipped: {skipped}")
    except Exception:
        frappe.db.rollback()
        raise


@frappe.whitelist()
def sync_foodics_addresses():
    """
    Sync Foodics Customer Addresses.
    Run via:
      bench --site <yoursite> execute foodics_migration.api.sync_foodics_addresses
    """
    frappe.only_for(("System Manager", "Administrator"))

    from foodics_migration.utils import ensure_custom_fields_for_foodics, assert_settings_for_parties
    ensure_custom_fields_for_foodics()

    cfg = load_foodics_settings()
    logger = frappe.logger()
    logger.setLevel(cfg.log_level)

    assert_settings_for_parties(cfg)
    s = frappe.get_single("Foodics Settings")

    session = make_foodics_session(cfg)
    count = 0

    try:
        for cust in paginated_get(cfg, session, endpoint="customers", params=None):
            try:
                fid = cust.get("id")
                customer_name = _get_customer_by_foodics_id(fid)
                if not customer_name:
                    frappe.logger().warning(f"Customer {fid} not found, skipping addresses")
                    continue
                customer_name = customer_name["name"]

                addresses = cust.get("addresses") or []
                for addr in addresses:
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


# ---------------------------
# Branch Fetching Functions
# ---------------------------

@frappe.whitelist()
@frappe.whitelist()
def fetch_foodics_branches():
    """
    Fetch branches from Foodics API for use in frontend/JavaScript.
    Returns list of branches with id, name, and reference.
    """
    frappe.only_for(("System Manager", "Administrator"))
    
    cfg = load_foodics_settings()
    session = make_foodics_session(cfg)
    
    branches = []
    try:
        for branch in paginated_get(cfg, session, endpoint="branches", params=None):
            # Handle JSON:API format - branch data might be in attributes
            branch_id = branch.get("id")
            branch_attrs = branch.get("attributes") or {}
            
            # Extract name - could be in attributes or directly in branch
            branch_name = branch_attrs.get("name") or branch.get("name") or ""
            branch_reference = branch_attrs.get("reference") or branch.get("reference") or ""
            
            branches.append({
                "id": branch_id or branch_attrs.get("id") or "",
                "name": branch_name,
                "reference": branch_reference,
            })
        return branches
    except Exception as e:
        frappe.log_error(title="Foodics Branches Fetch Error", message=frappe.get_traceback())
        frappe.throw(f"Error fetching branches: {str(e)}")


def get_foodics_branches():
    """
    Fetch branches from Foodics API (for bench execute).
    Returns list of branches with id, name, and reference.
    """
    cfg = load_foodics_settings()
    session = make_foodics_session(cfg)
    
    branches = []
    try:
        for branch in paginated_get(cfg, session, endpoint="branches", params=None):
            branches.append({
                "id": branch.get("id"),
                "name": branch.get("name"),
                "reference": branch.get("reference"),
            })
        print(f"✅ Fetched {len(branches)} branches from Foodics")
        return branches
    except Exception as e:
        print(f"❌ Error fetching branches: {e}")
        raise


# ---------------------------
# Payment Method Sync Functions
# ---------------------------

def _get_mode_of_payment_by_foodics_id(fid: str):
    """Get ERPNext Mode of Payment by Foodics payment method ID."""
    if not frappe.get_meta("Mode of Payment").has_field("foodics_payment_method_id"):
        return None
    return frappe.db.get_value(
        "Mode of Payment",
        filters={"foodics_payment_method_id": fid},
        fieldname="name",
    )


def _get_mode_of_payment_by_name(name: str):
    """Get ERPNext Mode of Payment by name."""
    return frappe.db.get_value("Mode of Payment", {"mode_of_payment": name}, "name")


def _upsert_mode_of_payment(cfg: FoodicsConfig, payment_method: dict, update_if_exists: bool = False):
    """
    Upsert ERPNext Mode of Payment from Foodics payment method.
    Returns Mode of Payment name.
    """
    fid = payment_method.get("id")
    if not fid:
        return None
    
    # Extract payment method data - handle both JSON:API format and flat structure
    pm_attrs = payment_method.get("attributes") or {}
    # If no attributes, use payment_method directly
    if not pm_attrs:
        pm_attrs = payment_method
    
    name = pm_attrs.get("name") or payment_method.get("name") or f"Payment Method {fid}"
    pm_type = pm_attrs.get("type") or payment_method.get("type") or "General"
    code = pm_attrs.get("code") or payment_method.get("code") or ""
    
    # Convert type to string if it's an integer
    if isinstance(pm_type, int):
        # Foodics payment type codes (common values)
        type_map_int = {
            1: "cash",
            2: "card",
            3: "bank",
            4: "credit",
        }
        pm_type = type_map_int.get(pm_type, "general")
    else:
        pm_type = str(pm_type) if pm_type else "general"
    
    # Map Foodics payment type to ERPNext type
    type_mapping = {
        "cash": "Cash",
        "card": "Bank",
        "bank": "Bank",
        "credit": "Bank",
        "general": "General",
    }
    erpnext_type = type_mapping.get(pm_type.lower() if isinstance(pm_type, str) else "general", "General")
    
    # Check if exists by Foodics ID
    existing_by_foodics = _get_mode_of_payment_by_foodics_id(fid) if fid else None
    
    # Check if exists by name
    existing_by_name = _get_mode_of_payment_by_name(name)
    
    existing = existing_by_foodics or existing_by_name
    
    if existing:
        if not update_if_exists:
            frappe.logger().info(f"⊘ Skipped Mode of Payment (exists, update disabled): {existing} (Foodics {fid})")
            return existing
        
        mop_doc = frappe.get_doc("Mode of Payment", existing)
        mop_doc.mode_of_payment = name
        mop_doc.type = erpnext_type
        mop_doc.enabled = 1
        
        # Add custom fields if they exist
        if frappe.get_meta("Mode of Payment").has_field("foodics_payment_method_id"):
            mop_doc.foodics_payment_method_id = fid
        if frappe.get_meta("Mode of Payment").has_field("foodics_payment_method_data"):
            mop_doc.foodics_payment_method_data = json.dumps(payment_method, ensure_ascii=False)
        if frappe.get_meta("Mode of Payment").has_field("foodics_payment_method_code"):
            mop_doc.foodics_payment_method_code = code
        
        mop_doc.save(ignore_permissions=True)
        frappe.logger().info(f"↻ Updated Mode of Payment: {mop_doc.name} (Foodics {fid})")
        return mop_doc.name
    else:
        # Create new Mode of Payment
        mop_doc = frappe.get_doc({
            "doctype": "Mode of Payment",
            "mode_of_payment": name,
            "type": erpnext_type,
            "enabled": 1,
        })
        
        # Add custom fields if they exist
        if frappe.get_meta("Mode of Payment").has_field("foodics_payment_method_id"):
            mop_doc.foodics_payment_method_id = fid
        if frappe.get_meta("Mode of Payment").has_field("foodics_payment_method_data"):
            mop_doc.foodics_payment_method_data = json.dumps(payment_method, ensure_ascii=False)
        if frappe.get_meta("Mode of Payment").has_field("foodics_payment_method_code"):
            mop_doc.foodics_payment_method_code = code
        
        mop_doc.insert(ignore_permissions=True)
        frappe.logger().info(f"＋ Created Mode of Payment: {mop_doc.name} (Foodics {fid})")
        return mop_doc.name


@frappe.whitelist()
def sync_all_foodics_master_data(sync_log_name=None, enqueue: bool = True):
	"""
	Sync all master data from Foodics: Item Groups, Products, Combos, Customers, Users, Payment Methods.
	Logs all operations to Foodics Sync Log if provided.
	
	Args:
		sync_log_name: Optional name of Foodics Sync Log record to log to
		enqueue: If True, enqueue as background job (default: True)
		
	Returns:
		dict with aggregated results from all sync operations, or job info if enqueued
	"""
	frappe.only_for(("System Manager", "Administrator"))
	
	# If enqueue is True, run in background
	if enqueue:
		# Create sync log first so user can track progress
		if not sync_log_name:
			sync_log = frappe.get_doc({
				"doctype": "Foodics Sync Log",
				"sync_type": "Master Data",
				"status": "In Progress",
				"started_at": frappe.utils.now(),
			})
			sync_log.insert(ignore_permissions=True)
			frappe.db.commit()
			sync_log_name = sync_log.name
			sync_log.add_log_entry("Job queued, waiting to start...", None, None, "INFO")
		
		job = frappe.enqueue(
			"foodics_migration.api.sync_all_foodics_master_data",
			sync_log_name=sync_log_name,
			enqueue=False,  # Don't enqueue recursively
			queue="long",
			timeout=3600,  # 1 hour timeout
			job_name="sync_all_master_data",
		)
		return {
			"job_id": job.id if hasattr(job, 'id') else str(job),
			"status": "queued",
			"sync_log_name": sync_log_name,
			"message": "Master data sync job has been queued. Check Foodics Sync Log for progress.",
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
			"sync_type": "Master Data",
			"status": "In Progress",
			"started_at": frappe.utils.now(),
		})
		sync_log.insert(ignore_permissions=True)
		frappe.db.commit()
		sync_log_name = sync_log.name
	
	total_results = {
		"item_groups": {"created": 0, "updated": 0, "skipped": 0, "failed": 0},
		"products": {"created": 0, "updated": 0, "skipped": 0, "failed": 0},
		"combos": {"created": 0, "updated": 0, "skipped": 0, "failed": 0},
		"customers": {"created": 0, "updated": 0, "skipped": 0, "failed": 0},
		"users": {"created": 0, "updated": 0, "skipped": 0, "failed": 0},
		"payment_methods": {"created": 0, "updated": 0, "skipped": 0, "failed": 0},
	}
	
	try:
		# 1. Sync Item Groups
		sync_log.add_log_entry("Starting Item Groups sync...", "Item Groups", None, "INFO")
		try:
			result = sync_foodics_item_groups()
			if result:
				total_results["item_groups"] = result
				sync_log.add_log_entry(
					f"Item Groups sync completed: Created={result.get('created', 0)}, Updated={result.get('updated', 0)}, Skipped={result.get('skipped', 0)}, Failed={result.get('failed', 0)}",
					"Item Groups", None, "INFO"
				)
		except Exception as e:
			sync_log.add_log_entry(f"Item Groups sync failed: {str(e)}", "Item Groups", None, "FAILED")
			frappe.log_error(title="Item Groups Sync Error", message=frappe.get_traceback())
		
		# 2. Sync Products
		sync_log.add_log_entry("Starting Products sync...", "Products", None, "INFO")
		try:
			result = sync_foodics_products()
			if result:
				total_results["products"] = result
				sync_log.add_log_entry(
					f"Products sync completed: Created={result.get('created', 0)}, Updated={result.get('updated', 0)}, Skipped={result.get('skipped', 0)}, Failed={result.get('failed', 0)}",
					"Products", None, "INFO"
				)
		except Exception as e:
			sync_log.add_log_entry(f"Products sync failed: {str(e)}", "Products", None, "FAILED")
			frappe.log_error(title="Products Sync Error", message=frappe.get_traceback())
		
		# 3. Sync Combos
		sync_log.add_log_entry("Starting Combos sync...", "Combos", None, "INFO")
		try:
			from foodics_migration.api_combo import sync_foodics_combos
			result = sync_foodics_combos()
			if result:
				total_results["combos"] = result
				sync_log.add_log_entry(
					f"Combos sync completed: Created={result.get('created', 0)}, Updated={result.get('updated', 0)}, Skipped={result.get('skipped', 0)}, Failed={result.get('failed', 0)}",
					"Combos", None, "INFO"
				)
		except Exception as e:
			sync_log.add_log_entry(f"Combos sync failed: {str(e)}", "Combos", None, "FAILED")
			frappe.log_error(title="Combos Sync Error", message=frappe.get_traceback())
		
		# 4. Sync Customers
		sync_log.add_log_entry("Starting Customers sync...", "Customers", None, "INFO")
		try:
			result = sync_foodics_customers()
			if result:
				total_results["customers"] = result
				sync_log.add_log_entry(
					f"Customers sync completed: Created={result.get('created', 0)}, Updated={result.get('updated', 0)}, Skipped={result.get('skipped', 0)}, Failed={result.get('failed', 0)}",
					"Customers", None, "INFO"
				)
		except Exception as e:
			sync_log.add_log_entry(f"Customers sync failed: {str(e)}", "Customers", None, "FAILED")
			frappe.log_error(title="Customers Sync Error", message=frappe.get_traceback())
		
		# 5. Sync Users
		sync_log.add_log_entry("Starting Users sync...", "Users", None, "INFO")
		try:
			result = sync_foodics_users()
			if result:
				total_results["users"] = result
				sync_log.add_log_entry(
					f"Users sync completed: Created={result.get('created', 0)}, Updated={result.get('updated', 0)}, Skipped={result.get('skipped', 0)}, Failed={result.get('failed', 0)}",
					"Users", None, "INFO"
				)
		except Exception as e:
			sync_log.add_log_entry(f"Users sync failed: {str(e)}", "Users", None, "FAILED")
			frappe.log_error(title="Users Sync Error", message=frappe.get_traceback())
		
		# 6. Sync Payment Methods
		sync_log.add_log_entry("Starting Payment Methods sync...", "Payment Methods", None, "INFO")
		try:
			result = sync_foodics_payment_methods()
			if result:
				total_results["payment_methods"] = result
				sync_log.add_log_entry(
					f"Payment Methods sync completed: Created={result.get('created', 0)}, Updated={result.get('updated', 0)}, Skipped={result.get('skipped', 0)}, Failed={result.get('failed', 0)}",
					"Payment Methods", None, "INFO"
				)
		except Exception as e:
			sync_log.add_log_entry(f"Payment Methods sync failed: {str(e)}", "Payment Methods", None, "FAILED")
			frappe.log_error(title="Payment Methods Sync Error", message=frappe.get_traceback())
		
		# Calculate totals
		total_created = sum(r.get("created", 0) for r in total_results.values())
		total_updated = sum(r.get("updated", 0) for r in total_results.values())
		total_skipped = sum(r.get("skipped", 0) for r in total_results.values())
		total_failed = sum(r.get("failed", 0) for r in total_results.values())
		total_records = total_created + total_updated + total_skipped + total_failed
		
		# Update sync log
		sync_log.total_records = total_records
		sync_log.created = total_created
		sync_log.updated = total_updated
		sync_log.skipped = total_skipped
		sync_log.failed = total_failed
		
		status = "Completed" if total_failed == 0 else "Partial"
		sync_log.finalize_log(status)
		
		return {
			"sync_log_name": sync_log.name,
			"total": {
				"created": total_created,
				"updated": total_updated,
				"skipped": total_skipped,
				"failed": total_failed,
				"total_records": total_records,
			},
			"details": total_results,
		}
		
	except Exception as e:
		if sync_log:
			sync_log.add_log_entry(f"Master data sync aborted: {str(e)}", None, None, "FAILED")
			sync_log.finalize_log("Failed")
		frappe.log_error(title="Master Data Sync Error", message=frappe.get_traceback())
		raise


@frappe.whitelist()
def sync_foodics_payment_methods():
    """
    Sync Foodics Payment Methods to ERPNext Mode of Payment.
    Maps Foodics payment methods to ERPNext Mode of Payment doctype.
    
    Run via:
      bench --site <yoursite> execute foodics_migration.api.sync_foodics_payment_methods
    """
    frappe.only_for(("System Manager", "Administrator"))
    
    cfg = load_foodics_settings()
    logger = frappe.logger()
    logger.setLevel(cfg.log_level)
    
    # Get update setting from Foodics Settings
    settings = frappe.get_single("Foodics Settings")
    update_if_exists = bool(getattr(settings, "update_payment_methods_if_exists", 0))
    
    session = make_foodics_session(cfg)
    
    count = 0
    skipped = 0
    created = 0
    updated = 0
    failed = 0
    
    try:
        for payment_method in paginated_get(cfg, session, endpoint="payment_methods", params=None):
            try:
                fid = payment_method.get("id")
                existing = _get_mode_of_payment_by_foodics_id(fid) if fid else None
                was_existing = existing is not None
                
                result = _upsert_mode_of_payment(cfg, payment_method, update_if_exists=update_if_exists)
                
                if not result:
                    failed += 1
                    continue
                
                if was_existing and not update_if_exists:
                    skipped += 1
                elif was_existing:
                    updated += 1
                else:
                    created += 1
                count += 1
                
                # Commit batching
                if (count + skipped + failed) % cfg.commit_every_n_records == 0:
                    frappe.db.commit()
                    logger.info(f"Processed {count + skipped + failed} payment methods so far...")
                    
            except Exception as e:
                failed += 1
                frappe.log_error(
                    title="Foodics Payment Method Sync Error",
                    message=f"Failed to sync payment method {payment_method.get('id', 'unknown')}: {e}\n{frappe.get_traceback()}",
                )
                logger.error(f"❌ Failed to sync payment method {payment_method.get('id', 'unknown')}: {e}")
                # Continue with next payment method
                
        # Final commit
        frappe.db.commit()
        logger.info(f"✅ Finished Foodics payment methods sync. Created: {created}, Updated: {updated}, Skipped: {skipped}, Failed: {failed}")
        
        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
        }
        
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(title="Foodics Payment Method Sync Error", message=frappe.get_traceback())
        raise


# ---------------------------
# User Sync Functions
# ---------------------------

def _get_user_by_foodics_id(fid: str):
    """Get ERPNext User by Foodics user ID."""
    if not frappe.get_meta("User").has_field("foodics_user_id"):
        return None
    return frappe.db.get_value(
        "User",
        filters={"foodics_user_id": fid},
        fieldname="name",
    )


def _get_user_by_email(email: str):
    """Get ERPNext User by email."""
    if not email:
        return None
    return frappe.db.get_value("User", {"email": email}, "name")


def _extract_user_name(user_data: dict) -> tuple:
    """Extract first_name, middle_name, last_name from Foodics user name."""
    name = user_data.get("name") or ""
    if not name:
        return ("", "", "")
    
    parts = name.strip().split()
    if len(parts) >= 3:
        return (parts[0], parts[1], " ".join(parts[2:]))
    elif len(parts) == 2:
        return (parts[0], "", parts[1])
    else:
        return (parts[0] if parts else "", "", "")


def _upsert_user(cfg: FoodicsConfig, user_data: dict, update_if_exists: bool = False) -> str:
    """
    Upsert ERPNext User from Foodics user data.
    Returns User name (email).
    """
    from frappe.utils import now_datetime
    
    fid = user_data.get("id")
    if not fid:
        return ""
    
    # Extract user data - handle JSON:API format
    user_attrs = user_data.get("attributes") or user_data
    email = user_attrs.get("email") or user_data.get("email") or ""
    phone = user_attrs.get("phone") or user_data.get("phone") or ""
    name = user_attrs.get("name") or user_data.get("name") or ""
    number = user_attrs.get("number") or user_data.get("number") or ""
    
    # If no email, skip this user (email is required for ERPNext User)
    if not email:
        frappe.logger().warning(f"⚠️ Skipping Foodics user {fid}: No email address")
        return ""
    
    first_name, middle_name, last_name = _extract_user_name(user_attrs if user_attrs != user_data else user_data)
    
    # Check if user exists by Foodics ID or email
    existing_by_foodics = _get_user_by_foodics_id(fid) if fid else None
    existing_by_email = _get_user_by_email(email)
    
    existing = existing_by_foodics or existing_by_email
    
    now = now_datetime()
    
    user_fields = {
        "email": email,
        "first_name": first_name or email.split("@")[0],
        "middle_name": middle_name or "",
        "last_name": last_name or "",
        "phone": phone or "",
        "enabled": 1,
        "send_welcome_email": 0,
    }
    
    # Add custom fields if they exist
    if frappe.get_meta("User").has_field("foodics_user_id"):
        user_fields["foodics_user_id"] = fid
    if frappe.get_meta("User").has_field("foodics_user_data"):
        user_fields["foodics_user_data"] = json.dumps(user_data, ensure_ascii=False)
    
    if existing:
        if not update_if_exists:
            frappe.logger().info(f"⊘ Skipped User (exists, update disabled): {existing} (Foodics {fid})")
            return existing
        
        user_doc = frappe.get_doc("User", existing)
        user_doc.update(user_fields)
        user_doc.save(ignore_permissions=True)
        frappe.logger().info(f"↻ Updated User: {user_doc.name} (Foodics {fid})")
        return user_doc.name
    else:
        # Create new user
        user_doc = frappe.get_doc({
            "doctype": "User",
            **user_fields
        })
        user_doc.insert(ignore_permissions=True)
        
        # Add Employee role if it exists
        if frappe.db.exists("Role", "Employee"):
            user_doc.add_roles("Employee")
        
        frappe.logger().info(f"＋ Created User: {user_doc.name} (Foodics {fid})")
        return user_doc.name


@frappe.whitelist()
def sync_foodics_users():
    """
    Sync Foodics Users to ERPNext Users.
    Maps Foodics users to ERPNext User doctype.
    
    Run via:
      bench --site <yoursite> execute foodics_migration.api.sync_foodics_users
    """
    frappe.only_for(("System Manager", "Administrator"))
    
    cfg = load_foodics_settings()
    logger = frappe.logger()
    logger.setLevel(cfg.log_level)
    
    # Get update setting from Foodics Settings
    settings = frappe.get_single("Foodics Settings")
    update_if_exists = bool(getattr(settings, "update_users_if_exists", 0))
    
    session = make_foodics_session(cfg)
    
    count = 0
    skipped = 0
    created = 0
    updated = 0
    failed = 0
    
    try:
        for user in paginated_get(cfg, session, endpoint="users", params=None):
            try:
                fid = user.get("id")
                existing = _get_user_by_foodics_id(fid) if fid else None
                was_existing = existing is not None
                
                result = _upsert_user(cfg, user, update_if_exists=update_if_exists)
                
                if not result:
                    failed += 1
                    continue
                
                if was_existing and not update_if_exists:
                    skipped += 1
                elif was_existing:
                    updated += 1
                    count += 1
                else:
                    created += 1
                    count += 1
                
                # Commit batching
                if (count + skipped + failed) % cfg.commit_every_n_records == 0:
                    frappe.db.commit()
                    logger.info(f"Processed {count + skipped + failed} users so far...")
                    
            except Exception as e:
                failed += 1
                frappe.log_error(
                    title="Foodics User Sync Error",
                    message=f"Failed to sync user {user.get('id', 'unknown')}: {e}\n{frappe.get_traceback()}",
                )
                logger.error(f"❌ Failed to sync user {user.get('id', 'unknown')}: {e}")
                # Continue with next user
                
        # Final commit
        frappe.db.commit()
        logger.info(f"✅ Finished Foodics users sync. Created: {created}, Updated: {updated}, Skipped: {skipped}, Failed: {failed}")
        
        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
        }

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(title="Foodics User Sync Error", message=frappe.get_traceback())
        raise
