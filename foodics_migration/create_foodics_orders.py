import frappe

def execute():
   # Create child DocType: Foodics Order Item
    if not frappe.db.exists("DocType", "Foodics Order Item"):
        doc = frappe.get_doc({
            "doctype": "DocType",
            "name": "Foodics Order Item",
            "module": "Foodics Migration",
            "custom": 1,
            "istable": 1,
            "permissions": [],
            "fields": [
                {"fieldname": "foodics_order_item_id", "label": "Foodics Order Item ID", "fieldtype": "Data"},
                {"fieldname": "status", "label": "Status", "fieldtype": "Int"},
                {"fieldname": "quantity", "label": "Quantity", "fieldtype": "Float"},
                {"fieldname": "unit_price_gross", "label": "Unit Price Gross", "fieldtype": "Currency"},
                {"fieldname": "total_price_gross", "label": "Total Price Gross", "fieldtype": "Currency"},
                {"fieldname": "unit_price_net", "label": "Unit Price Net", "fieldtype": "Currency"},
                {"fieldname": "total_price_net", "label": "Total Price Net", "fieldtype": "Currency"},
                {"fieldname": "discount_amount", "label": "Discount Amount", "fieldtype": "Currency"},
                {"fieldname": "foodics_product_id", "label": "Foodics Product ID", "fieldtype": "Data"},
                {"fieldname": "foodics_product_name", "label": "Foodics Product Name", "fieldtype": "Data"},
                {"fieldname": "foodics_category_id", "label": "Foodics Category ID", "fieldtype": "Data"},
                {"fieldname": "foodics_category_name", "label": "Foodics Category Name", "fieldtype": "Data"},
                {"fieldname": "erp_item_code", "label": "ERPNext Item Code", "fieldtype": "Link", "options": "Item"},
                {"fieldname": "foodics_item_uuid", "label": "Foodics Item UUID", "fieldtype": "Data"},
                {"fieldname": "kitchen_notes", "label": "Kitchen Notes", "fieldtype": "Small Text"},
            ],
        })
        doc.insert(ignore_if_duplicate=True)
        frappe.db.commit()
        print("✔️ Created DocType: Foodics Order Item")
    # Create parent DocType: Foodics Order
    if not frappe.db.exists("DocType", "Foodics Order"):
        doc = frappe.get_doc({
            "doctype": "DocType",
            "name": "Foodics Order",
            "module": "Foodics Migration",
            "custom": 1,
            "is_submittable": 0,   # no Submit/Cancel
            "permissions": [
                {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
            ],
            "fields": [
                {"fieldname": "foodics_order_id", "label": "Foodics Order ID", "fieldtype": "Data", "reqd": 1, "unique": 1},
                {"fieldname": "number", "label": "Number", "fieldtype": "Int"},
                {"fieldname": "check_number", "label": "Check Number", "fieldtype": "Int"},
                {"fieldname": "status", "label": "Status", "fieldtype": "Int"},
                {"fieldname": "delivery_status", "label": "Delivery Status", "fieldtype": "Int"},
                {"fieldname": "guests", "label": "Guests", "fieldtype": "Int"},
                {"fieldname": "business_date", "label": "Business Date", "fieldtype": "Date"},
                {"fieldname": "opened_at", "label": "Opened At", "fieldtype": "Datetime"},
                {"fieldname": "closed_at", "label": "Closed At", "fieldtype": "Datetime"},
                {"fieldname": "subtotal_price_gross", "label": "Subtotal Gross", "fieldtype": "Currency"},
                {"fieldname": "total_price_gross", "label": "Total Gross", "fieldtype": "Currency"},
                {"fieldname": "total_price_net", "label": "Total Net", "fieldtype": "Currency"},
                {"fieldname": "discount_amount", "label": "Discount Amount", "fieldtype": "Currency"},
                {"fieldname": "rounding_amount", "label": "Rounding Amount", "fieldtype": "Currency"},
                {"fieldname": "branch_id", "label": "Branch ID", "fieldtype": "Data"},
                {"fieldname": "branch_name", "label": "Branch Name", "fieldtype": "Data"},
                {"fieldname": "device_id", "label": "Device ID", "fieldtype": "Data"},
                {"fieldname": "device_name", "label": "Device Name", "fieldtype": "Data"},
                {"fieldname": "creator_id", "label": "Creator ID", "fieldtype": "Data"},
                {"fieldname": "creator_name", "label": "Creator Name", "fieldtype": "Data"},
                {"fieldname": "closer_id", "label": "Closer ID", "fieldtype": "Data"},
                {"fieldname": "closer_name", "label": "Closer Name", "fieldtype": "Data"},
                {"fieldname": "foodics_order_raw", "label": "Foodics Raw JSON", "fieldtype": "Long Text"},
                {"fieldname": "items", "label": "Items", "fieldtype": "Table", "options": "Foodics Order Item"},
            ],
        })
        doc.insert(ignore_if_duplicate=True)
        frappe.db.commit()
        print("✔️ Created DocType: Foodics Order")

 
