# Copyright (c) 2025, ahmed and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""
	Create custom fields for Foodics User ID tracking on User doctype.
	"""
	custom_fields = {
		"User": [
			{
				"fieldname": "foodics_section",
				"fieldtype": "Section Break",
				"label": "Foodics",
				"insert_after": "__last",
			},
			{
				"fieldname": "foodics_user_id",
				"fieldtype": "Data",
				"label": "Foodics User ID",
				"unique": 0,
				"in_standard_filter": 1,
				"insert_after": "foodics_section",
			},
			{
				"fieldname": "foodics_user_data",
				"fieldtype": "Long Text",
				"label": "Foodics Raw JSON",
				"insert_after": "foodics_user_id",
			},
		],
	}

	create_custom_fields(custom_fields, ignore_validate=True)
	frappe.db.commit()
	frappe.msgprint("Custom fields for Foodics User ID tracking have been created successfully.")

