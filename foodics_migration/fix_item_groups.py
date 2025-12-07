# Copyright (c) 2025, ahmed and contributors
# For license information, please see license.txt

"""
Script to fix Item Groups for items that have "All Item Groups" but should have specific groups.
Run via: bench --site food.milestoneksa.com execute foodics_migration.fix_item_groups.fix_item_group_for_item --kwargs "{'item_code': 'sk-0292'}"
"""

import json
import frappe


def _resolve_item_group_by_foodics_category_id(foodics_category_id, category_data=None, update_if_exists=False):
	"""Resolve or create Item Group from Foodics category."""
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
			pass
	
	# Check if Item Group exists by foodics_category_id
	if frappe.get_meta("Item Group").has_field("foodics_category_id"):
		ig = frappe.db.get_value(
			"Item Group",
			filters={"foodics_category_id": foodics_category_id},
			fieldname="name",
		)
	else:
		ig = None
	
	if ig:
		return ig
	
	# Create new Item Group
	category_name = None
	if category_data:
		category_name = category_data.get("name") or f"Category {foodics_category_id}"
	else:
		category_name = f"Category {foodics_category_id}"
	
	if category_name:
		try:
			# Check if Item Group exists by name
			existing_by_name = frappe.db.get_value("Item Group", {"item_group_name": category_name}, "name")
			if existing_by_name:
				ig_doc = frappe.get_doc("Item Group", existing_by_name)
				if frappe.get_meta("Item Group").has_field("foodics_category_id"):
					ig_doc.foodics_category_id = foodics_category_id
				if frappe.get_meta("Item Group").has_field("foodics_category_data") and category_data:
					ig_doc.foodics_category_data = json.dumps(category_data, ensure_ascii=False)
				if not ig_doc.parent_item_group or ig_doc.parent_item_group != "All Item Groups":
					ig_doc.parent_item_group = "All Item Groups"
				ig_doc.save(ignore_permissions=True)
				return ig_doc.name
			else:
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
				return ig_doc.name
		except Exception as e:
			frappe.log_error(
				title="Item Group Creation Error",
				message=f"Failed to create Item Group for category {foodics_category_id}: {e}",
			)
	
	return "All Item Groups"


@frappe.whitelist()
def fix_item_group_for_item(item_code: str):
	"""Fix Item Group for a specific item by reading its Foodics data."""
	frappe.only_for(("System Manager", "Administrator"))
	
	try:
		item = frappe.get_doc("Item", item_code)
	except frappe.DoesNotExistError:
		frappe.throw(f"Item {item_code} not found")
	
	# Get Foodics product data
	foodics_data_str = getattr(item, "foodics_product_data", None)
	if not foodics_data_str:
		frappe.throw(f"Item {item_code} has no Foodics product data. Cannot determine category.")
	
	try:
		product_data = json.loads(foodics_data_str)
	except Exception as e:
		frappe.throw(f"Failed to parse Foodics data for item {item_code}: {e}")
	
	# Extract category
	category = product_data.get("category") or {}
	category_id = None
	category_data = None
	
	if category:
		if isinstance(category, dict):
			category_id = category.get("id")
			category_data = category
		elif isinstance(category, str):
			category_id = category
	
	if not category_id:
		category_id = product_data.get("category_id")
	
	if not category_id:
		frappe.throw(f"Item {item_code} has no category information in Foodics data.")
	
	# Resolve Item Group
	settings = frappe.get_single("Foodics Settings")
	update_if_exists = bool(getattr(settings, "update_products_if_exists", 0))
	
	item_group = _resolve_item_group_by_foodics_category_id(
		category_id,
		category_data=category_data if category_data else category if category else None,
		update_if_exists=update_if_exists
	)
	
	if item_group and item_group != "All Item Groups":
		item.item_group = item_group
		item.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.msgprint(f"✅ Updated Item {item_code} to Item Group: {item_group} (Category: {category_id})")
		return item_group
	else:
		frappe.throw(f"Failed to resolve Item Group for category {category_id}. Item Group may need to be synced first.")

