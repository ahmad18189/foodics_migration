# Copyright (c) 2025, ahmed and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""
	Create custom fields for Foodics Payment Method ID tracking on Mode of Payment doctype.
	"""
	custom_fields = {
		"Mode of Payment": [
			{
				"fieldname": "foodics_section",
				"fieldtype": "Section Break",
				"label": "Foodics",
				"insert_after": "__last",
			},
			{
				"fieldname": "foodics_payment_method_id",
				"fieldtype": "Data",
				"label": "Foodics Payment Method ID",
				"unique": 0,
				"in_standard_filter": 1,
				"insert_after": "foodics_section",
			},
			{
				"fieldname": "foodics_payment_method_code",
				"fieldtype": "Data",
				"label": "Foodics Payment Method Code",
				"insert_after": "foodics_payment_method_id",
			},
			{
				"fieldname": "foodics_payment_method_data",
				"fieldtype": "Long Text",
				"label": "Foodics Raw JSON",
				"insert_after": "foodics_payment_method_code",
			},
		],
	}

	create_custom_fields(custom_fields, ignore_validate=True)
	frappe.db.commit()
	frappe.msgprint("Custom fields for Foodics Payment Method ID tracking have been created successfully.")

