# Copyright (c) 2025, ahmed and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""
	Add sales_invoice field to Foodics Order doctype.
	"""
	# Check if Foodics Order doctype exists
	if not frappe.db.exists("DocType", "Foodics Order"):
		frappe.msgprint("Foodics Order doctype does not exist yet. Skipping.")
		return
	
	# Check if field already exists
	if frappe.db.exists("Custom Field", {"dt": "Foodics Order", "fieldname": "sales_invoice"}):
		frappe.msgprint("Sales Invoice field already exists on Foodics Order.")
		return
	
	custom_fields = {
		"Foodics Order": [
			{
				"fieldname": "sales_invoice",
				"fieldtype": "Link",
				"label": "Sales Invoice",
				"options": "Sales Invoice",
				"insert_after": "__last",
			},
		],
	}
	
	create_custom_fields(custom_fields, ignore_validate=True)
	frappe.db.commit()
	frappe.msgprint("Sales Invoice field added to Foodics Order successfully.")




















