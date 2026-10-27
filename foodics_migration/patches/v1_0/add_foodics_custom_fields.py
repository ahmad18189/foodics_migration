# Copyright (c) 2025, ahmed and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""
	Create custom fields for Foodics ID tracking on:
	- Customer
	- Sales Invoice
	- Item
	- Item Group
	- Product Bundle
	"""
	custom_fields = {
		"Customer": [
			{
				"fieldname": "foodics_section",
				"fieldtype": "Section Break",
				"label": "Foodics",
				"insert_after": "__last",
			},
			{
				"fieldname": "foodics_customer_id",
				"fieldtype": "Data",
				"label": "Foodics Customer ID",
				"unique": 0,
				"in_standard_filter": 1,
				"insert_after": "foodics_section",
			},
			{
				"fieldname": "foodics_customer_data",
				"fieldtype": "Long Text",
				"label": "Foodics Raw JSON",
				"insert_after": "foodics_customer_id",
			},
		],
		"Sales Invoice": [
			{
				"fieldname": "foodics_section",
				"fieldtype": "Section Break",
				"label": "Foodics",
				"insert_after": "__last",
			},
			{
				"fieldname": "foodics_order_id",
				"fieldtype": "Data",
				"label": "Foodics Order ID",
				"insert_after": "foodics_section",
			},
			{
				"fieldname": "foodics_order_name",
				"fieldtype": "Link",
				"label": "Foodics Order",
				"options": "Foodics Order",
				"insert_after": "foodics_order_id",
			},
			{
				"fieldname": "foodics_branch_id",
				"fieldtype": "Data",
				"label": "Foodics Branch ID",
				"insert_after": "foodics_order_name",
			},
			{
				"fieldname": "foodics_branch_name",
				"fieldtype": "Data",
				"label": "Foodics Branch Name",
				"insert_after": "foodics_branch_id",
			},
		],
		"Item": [
			{
				"fieldname": "foodics_product_id",
				"fieldtype": "Data",
				"label": "Foodics Product ID",
				"insert_after": "item_code",
			},
			{
				"fieldname": "foodics_product_data",
				"fieldtype": "Long Text",
				"label": "Foodics Product Raw JSON",
				"insert_after": "foodics_product_id",
			},
			{
				"fieldname": "foodics_combo_id",
				"fieldtype": "Data",
				"label": "Foodics Combo ID",
				"insert_after": "foodics_product_data",
			},
			{
				"fieldname": "foodics_combo_data",
				"fieldtype": "Long Text",
				"label": "Foodics Combo Raw JSON",
				"insert_after": "foodics_combo_id",
			},
		],
		"Item Group": [
			{
				"fieldname": "foodics_section",
				"fieldtype": "Section Break",
				"label": "Foodics",
				"insert_after": "__last",
			},
			{
				"fieldname": "foodics_category_id",
				"fieldtype": "Data",
				"label": "Foodics Category ID",
				"insert_after": "foodics_section",
			},
			{
				"fieldname": "foodics_category_data",
				"fieldtype": "Long Text",
				"label": "Foodics Category Raw JSON",
				"insert_after": "foodics_category_id",
			},
		],
		"Product Bundle": [
			{
				"fieldname": "foodics_section",
				"fieldtype": "Section Break",
				"label": "Foodics",
				"insert_after": "__last",
			},
			{
				"fieldname": "foodics_combo_id",
				"fieldtype": "Data",
				"label": "Foodics Combo ID",
				"insert_after": "foodics_section",
			},
			{
				"fieldname": "foodics_combo_data",
				"fieldtype": "Long Text",
				"label": "Foodics Combo Raw JSON",
				"insert_after": "foodics_combo_id",
			},
		],
		# Foodics Order - only add if doctype exists
		# (This will be added after app migration)
	}

	create_custom_fields(custom_fields, ignore_validate=True)
	frappe.db.commit()
	frappe.msgprint("Custom fields for Foodics ID tracking have been created successfully.")




















