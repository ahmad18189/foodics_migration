# Copyright (c) 2025, ahmed and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""
	Add customer_id field to Foodics Order doctype to store Foodics customer ID.
	"""
	custom_fields = {
		"Foodics Order": [
			{
				"fieldname": "customer_id",
				"fieldtype": "Data",
				"label": "Customer ID",
				"insert_after": "closer_name",
			},
		],
	}

	create_custom_fields(custom_fields, ignore_validate=True)
	frappe.db.commit()
	frappe.msgprint("Customer ID field has been added to Foodics Order doctype.")

