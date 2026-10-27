import json
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

PARENT_DT = "Foodics Order"
ITEM_DT   = "Foodics Order Item"

# -----------------------------
# 1) Custom fields (add-if-missing)
# -----------------------------
def bootstrap_foodics_order_custom_fields():
    """Add missing fields to Foodics Order and Foodics Order Item (idempotent)."""
    custom_fields = {
        PARENT_DT: [
            dict(fieldname="reference", label="Reference", fieldtype="Data", insert_after="check_number"),
            dict(fieldname="has_products", label="Has Products", fieldtype="Check", insert_after="reference"),
            dict(fieldname="has_combos", label="Has Combos", fieldtype="Check", insert_after="has_products"),
            dict(fieldname="payment_total", label="Payment Total", fieldtype="Currency", insert_after="total_price_gross"),
            dict(fieldname="payment_methods_csv", label="Payment Methods CSV", fieldtype="Small Text", insert_after="payment_total"),
            dict(fieldname="charges_total", label="Charges Total", fieldtype="Currency", insert_after="payment_methods_csv"),
            dict(fieldname="foodics_order_raw", label="Foodics Order Raw", fieldtype="Long Text", insert_after="charges_total"),
        ],
        ITEM_DT: [
            dict(fieldname="item_type", label="Item Type", fieldtype="Select", options="\nproduct\ncombo_product", default="product", insert_after="idx"),
            dict(fieldname="foodics_item_id", label="Foodics Item ID", fieldtype="Data", insert_after="item_type"),
            dict(fieldname="product_id", label="Product ID", fieldtype="Data", insert_after="foodics_item_id"),
            dict(fieldname="product_name", label="Product Name", fieldtype="Data", insert_after="product_id"),
            dict(fieldname="category_id", label="Category ID", fieldtype="Data", insert_after="product_name"),
            dict(fieldname="category_name", label="Category Name", fieldtype="Data", insert_after="category_id"),
            dict(fieldname="unit_price_gross", label="Unit Price (Gross)", fieldtype="Currency", insert_after="category_name"),
            dict(fieldname="total_price_gross", label="Total Price (Gross)", fieldtype="Currency", insert_after="unit_price_gross"),
            dict(fieldname="unit_price_net", label="Unit Price (Net)", fieldtype="Currency", insert_after="total_price_gross"),
            dict(fieldname="total_price_net", label="Total Price (Net)", fieldtype="Currency", insert_after="unit_price_net"),
            dict(fieldname="discount_amount", label="Discount Amount", fieldtype="Currency", insert_after="total_price_net"),
            dict(fieldname="kitchen_notes", label="Kitchen Notes", fieldtype="Small Text", insert_after="discount_amount"),
            dict(fieldname="options_json", label="Options JSON", fieldtype="Long Text", insert_after="kitchen_notes"),
            dict(fieldname="taxes_json", label="Taxes JSON", fieldtype="Long Text", insert_after="options_json"),
            dict(fieldname="product_raw", label="Product Raw", fieldtype="Long Text", insert_after="taxes_json"),
            # combo linkage / metadata
            dict(fieldname="combo_id", label="Combo ID", fieldtype="Data", insert_after="product_raw"),
            dict(fieldname="combo_name", label="Combo Name", fieldtype="Data", insert_after="combo_id"),
            dict(fieldname="combo_size_name", label="Combo Size Name", fieldtype="Data", insert_after="combo_name"),
            dict(fieldname="combo_line_id", label="Combo Line ID", fieldtype="Data", insert_after="combo_size_name"),
        ],
    }
    create_custom_fields(custom_fields, ignore_validate=True)
    frappe.db.commit()


# -----------------------------
# 2) Create child Doctypes (if missing)
# -----------------------------
def _ensure_child_doctype(name, fields, module="Foodics Migration"):
    """Create a child table DocType if it doesn't exist (idempotent)."""
    if frappe.db.exists("DocType", name):
        return

    doc = frappe.get_doc({
        "doctype": "DocType",
        "name": name,
        "module": module,
        "custom": 1,
        "is_tree": 0,
        "istable": 1,
        "editable_grid": 1,
        "fields": fields,
        "permissions": []  # child tables don't need explicit perms
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()


def bootstrap_foodics_order_children_doctypes():
    """Create Foodics Order Combo / Payment / Charge child doctypes if missing, and
    ensure the parent has corresponding child table fields (add-if-missing)."""

    # Foodics Order Combo (header info only; combo components go into Foodics Order Item)
    combo_dt = "Foodics Order Combo"
    _ensure_child_doctype(combo_dt, [
        {"fieldname": "foodics_combo_id", "label": "Foodics Combo Line ID", "fieldtype": "Data"},
        {"fieldname": "combo_id", "label": "Combo ID", "fieldtype": "Data"},
        {"fieldname": "combo_name", "label": "Combo Name", "fieldtype": "Data"},
        {"fieldname": "combo_size_id", "label": "Combo Size ID", "fieldtype": "Data"},
        {"fieldname": "combo_size_name", "label": "Combo Size Name", "fieldtype": "Data"},
        {"fieldname": "quantity", "label": "Quantity", "fieldtype": "Float", "precision": "3"},
        {"fieldname": "unit_price_gross", "label": "Unit Price (Gross)", "fieldtype": "Currency"},
        {"fieldname": "total_price_gross", "label": "Total Price (Gross)", "fieldtype": "Currency"},
        {"fieldname": "unit_price_net", "label": "Unit Price (Net)", "fieldtype": "Currency"},
        {"fieldname": "total_price_net", "label": "Total Price (Net)", "fieldtype": "Currency"},
        {"fieldname": "discount_amount", "label": "Discount Amount", "fieldtype": "Currency"},
        {"fieldname": "status", "label": "Status", "fieldtype": "Int"},
        {"fieldname": "combo_raw", "label": "Combo Raw", "fieldtype": "Long Text"},
    ])

    # Foodics Order Payment
    payment_dt = "Foodics Order Payment"
    _ensure_child_doctype(payment_dt, [
        {"fieldname": "payment_id", "label": "Payment ID", "fieldtype": "Data"},
        {"fieldname": "amount", "label": "Amount", "fieldtype": "Currency"},
        {"fieldname": "currency", "label": "Currency", "fieldtype": "Data"},
        {"fieldname": "payment_method_id", "label": "Payment Method ID", "fieldtype": "Data"},
        {"fieldname": "payment_method_name", "label": "Payment Method Name", "fieldtype": "Data"},
        {"fieldname": "user_id", "label": "User ID", "fieldtype": "Data"},
        {"fieldname": "user_name", "label": "User Name", "fieldtype": "Data"},
        {"fieldname": "paid_at", "label": "Paid At", "fieldtype": "Datetime"},
        {"fieldname": "payment_raw", "label": "Payment Raw", "fieldtype": "Long Text"},
    ])

    # Foodics Order Charge
    charge_dt = "Foodics Order Charge"
    _ensure_child_doctype(charge_dt, [
        {"fieldname": "charge_id", "label": "Charge ID", "fieldtype": "Data"},
        {"fieldname": "charge_code", "label": "Charge Code", "fieldtype": "Data"},
        {"fieldname": "charge_name", "label": "Charge Name", "fieldtype": "Data"},
        {"fieldname": "amount_gross", "label": "Amount (Gross)", "fieldtype": "Currency"},
        {"fieldname": "amount_net", "label": "Amount (Net)", "fieldtype": "Currency"},
        {"fieldname": "taxes_json", "label": "Taxes JSON", "fieldtype": "Long Text"},
        {"fieldname": "charge_raw", "label": "Charge Raw", "fieldtype": "Long Text"},
    ])

    # Add childtable links on parent if missing
    # (these add-if-missing and won’t duplicate)
    create_custom_fields({
        PARENT_DT: [
            dict(fieldname="combos", label="Combos", fieldtype="Table",
                 options=combo_dt, insert_after="foodics_order_raw"),
            dict(fieldname="payments", label="Payments", fieldtype="Table",
                 options=payment_dt, insert_after="combos"),
            dict(fieldname="charges", label="Charges", fieldtype="Table",
                 options=charge_dt, insert_after="payments"),
        ]
    }, ignore_validate=True)
    frappe.db.commit()


# -----------------------------
# 3) Helpers to map one API order into ERP rows
# -----------------------------
def _safe(v, default=None):
    return v if v is not None else default

def append_products_items(order_json: dict, order_doc):
    """Append normal products (not combos) to Foodics Order Item child table."""
    products = order_json.get("products") or []
    for p in products:
        prod = p.get("product") or {}
        cat = prod.get("category") or {}
        item = order_doc.append("items", {})
        item.item_type = "product"
        item.foodics_item_id = _safe(p.get("id"))
        item.product_id = _safe(prod.get("id"))
        item.product_name = _safe(prod.get("name"))
        item.category_id = _safe(cat.get("id"))
        item.category_name = _safe(cat.get("name"))
        item.quantity = _safe(p.get("quantity"), 0)
        item.unit_price_gross = _safe(p.get("unit_price"), 0)
        item.total_price_gross = _safe(p.get("total_price"), 0)
        item.unit_price_net = _safe(p.get("tax_exclusive_unit_price"), 0)
        item.total_price_net = _safe(p.get("tax_exclusive_total_price"), 0)
        item.discount_amount = _safe(p.get("discount_amount"), 0)
        item.kitchen_notes = _safe(p.get("kitchen_notes"))
        item.options_json = json.dumps(_safe(p.get("options"), []), ensure_ascii=False)
        item.taxes_json = json.dumps(_safe(p.get("taxes"), []), ensure_ascii=False)
        item.product_raw = json.dumps(p, ensure_ascii=False)


def append_combos_and_flatten_items(order_json: dict, order_doc):
    """Create Foodics Order Combo child rows and also flatten each combo component
    into Foodics Order Item with combo metadata on each row."""
    combos = order_json.get("combos") or []
    if not combos:
        return

    order_doc.has_combos = 1

    for c in combos:
        combo_size = (c.get("combo_size") or {})
        combo_obj  = (combo_size.get("combo") or {})
        # Create combo header row
        combo_row = order_doc.append("combos", {})
        combo_row.foodics_combo_id = _safe(c.get("id"))
        combo_row.combo_id = _safe(combo_obj.get("id"))
        combo_row.combo_name = _safe(combo_obj.get("name"))
        combo_row.combo_size_id = _safe(combo_size.get("id"))
        combo_row.combo_size_name = _safe(combo_size.get("name"))
        combo_row.quantity = _safe(c.get("quantity"), 0)
        combo_row.unit_price_gross = _safe(c.get("unit_price"), 0)
        combo_row.total_price_gross = _safe(c.get("total_price"), 0)
        combo_row.unit_price_net = _safe(c.get("tax_exclusive_unit_price"), 0)
        combo_row.total_price_net = _safe(c.get("tax_exclusive_total_price"), 0)
        combo_row.discount_amount = _safe((c.get("discount") or {}).get("amount"), 0)
        combo_row.status = _safe(c.get("status"))
        combo_row.combo_raw = json.dumps(c, ensure_ascii=False)

        # Flatten combo products into items with combo metadata
        combo_products = _safe(c.get("products"), [])
        for cp in combo_products:
            prod = _safe(cp.get("product"), {})
            cat  = _safe(prod.get("category"), {})
            item = order_doc.append("items", {})
            item.item_type = "combo_product"
            item.foodics_item_id = _safe(cp.get("id"))
            item.product_id = _safe(prod.get("id"))
            item.product_name = _safe(prod.get("name"))
            item.category_id = _safe(cat.get("id"))
            item.category_name = _safe(cat.get("name"))
            item.quantity = _safe(cp.get("quantity"), 0)
            item.unit_price_gross = _safe(cp.get("unit_price"), 0)
            item.total_price_gross = _safe(cp.get("total_price"), 0)
            item.unit_price_net = _safe(cp.get("tax_exclusive_unit_price"), 0)
            item.total_price_net = _safe(cp.get("tax_exclusive_total_price"), 0)
            item.discount_amount = _safe(cp.get("discount_amount"), 0)
            item.taxes_json = json.dumps(_safe(cp.get("taxes"), []), ensure_ascii=False)
            item.product_raw = json.dumps(cp, ensure_ascii=False)
            # combo metadata for traceability
            item.combo_id = combo_row.combo_id
            item.combo_name = combo_row.combo_name
            item.combo_size_name = combo_row.combo_size_name
            item.combo_line_id = combo_row.foodics_combo_id

    frappe.db.commit()


def append_payments(order_json: dict, order_doc):
    payments = order_json.get("payments") or []
    total = 0.0
    methods = []
    for pay in payments:
        pm = _safe(pay.get("payment_method"), {})
        user = _safe(pay.get("user"), {})
        row = order_doc.append("payments", {})
        row.payment_id = _safe(pay.get("id"))
        row.amount = _safe(pay.get("amount"), 0)
        row.currency = _safe(pay.get("currency"))
        row.payment_method_id = _safe(pm.get("id"))
        row.payment_method_name = _safe(pm.get("name"))
        row.user_id = _safe(user.get("id"))
        row.user_name = _safe(user.get("name"))
        row.paid_at = _safe(pay.get("created_at"))  # or pay['paid_at'] if present in your payload
        row.payment_raw = json.dumps(pay, ensure_ascii=False)

        total += float(row.amount or 0)
        if row.payment_method_name:
            methods.append(row.payment_method_name)

    order_doc.payment_total = total
    order_doc.payment_methods_csv = ", ".join(sorted(set(methods))) if methods else None
    frappe.db.commit()


def append_charges(order_json: dict, order_doc):
    charges = order_json.get("charges") or []
    total = 0.0
    for ch in charges:
        chg = _safe(ch.get("charge"), {})
        row = order_doc.append("charges", {})
        row.charge_id = _safe(ch.get("id"))
        row.charge_code = _safe(chg.get("code"))
        row.charge_name = _safe(chg.get("name"))
        row.amount_gross = _safe(ch.get("amount"), 0)
        row.amount_net = _safe(ch.get("tax_exclusive_amount"), 0)
        row.taxes_json = json.dumps(_safe(ch.get("taxes"), []), ensure_ascii=False)
        row.charge_raw = json.dumps(ch, ensure_ascii=False)
        total += float(row.amount_gross or 0)

    order_doc.charges_total = total
    frappe.db.commit()


# -----------------------------
# 4) One-call bootstrap for schema (fields + child doctypes)
# -----------------------------
def bootstrap_foodics_order_schema():
    """Run once (or anytime) to ensure schema is ready."""
    bootstrap_foodics_order_custom_fields()
    bootstrap_foodics_order_children_doctypes()
    frappe.msgprint("Foodics Order schema bootstrapped.")



def hit(order_doc, order_json):
    """Populate Foodics order children from API JSON (pass Foodics Order doc + API payload dict)."""

    # Clear previous children if you are upserting:
    order_doc.set("items", [])
    order_doc.set("combos", [])
    order_doc.set("payments", [])
    order_doc.set("charges", [])

    # Normal products
    append_products_items(order_json, order_doc)

    # Combos (also flattens components into items with combo metadata)
    append_combos_and_flatten_items(order_json, order_doc)

    # Payments
    append_payments(order_json, order_doc)

    # Charges (optional, only if you included them in the API call)
    append_charges(order_json, order_doc)

    # Save the parent and store the raw JSON
    order_doc.foodics_order_raw = json.dumps(order_json, ensure_ascii=False)
    order_doc.has_products = 1 if (order_json.get("products") or []) else 0
    order_doc.save(ignore_permissions=True)
    frappe.db.commit()