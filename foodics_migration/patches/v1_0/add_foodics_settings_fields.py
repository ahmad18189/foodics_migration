# Copyright (c) 2025, ahmed and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""
	Add fields to Foodics Settings doctype:
	- Update checkboxes for sync control
	- Date fields for order sync filtering
	- Branch Cost Center Mapping child table
	"""
	# Get Foodics Settings doctype
	doctype = frappe.get_doc("DocType", "Foodics Settings")
	
	# Check if fields already exist
	existing_fieldnames = [f.fieldname for f in doctype.fields]
	
	# Fields to add
	new_fields = [
		{
			"fieldname": "section_break_sync_actions",
			"fieldtype": "Section Break",
			"label": "Sync Actions",
			"insert_after": "respect_foodics_deleted_customers",
		},
		{
			"fieldname": "update_products_if_exists",
			"fieldtype": "Check",
			"label": "Update Products If Exists",
			"default": "0",
			"insert_after": "section_break_sync_actions",
		},
		{
			"fieldname": "update_combos_if_exists",
			"fieldtype": "Check",
			"label": "Update Combos If Exists",
			"default": "0",
			"insert_after": "update_products_if_exists",
		},
		{
			"fieldname": "update_customers_if_exists",
			"fieldtype": "Check",
			"label": "Update Customers If Exists",
			"default": "0",
			"insert_after": "update_combos_if_exists",
		},
		{
			"fieldname": "update_orders_if_exists",
			"fieldtype": "Check",
			"label": "Update Orders If Exists",
			"default": "0",
			"insert_after": "update_customers_if_exists",
		},
		{
			"fieldname": "update_users_if_exists",
			"fieldtype": "Check",
			"label": "Update Users If Exists",
			"default": "0",
			"insert_after": "update_orders_if_exists",
		},
		{
			"fieldname": "update_payment_methods_if_exists",
			"fieldtype": "Check",
			"label": "Update Payment Methods If Exists",
			"default": "0",
			"insert_after": "update_users_if_exists",
		},
		{
			"fieldname": "create_sales_invoices",
			"fieldtype": "Check",
			"label": "Create Sales Invoices",
			"default": "1",
			"insert_after": "update_payment_methods_if_exists",
		},
		{
			"fieldname": "column_break_sync_buttons",
			"fieldtype": "Column Break",
			"insert_after": "create_sales_invoices",
		},
		{
			"fieldname": "button_sync_item_groups",
			"fieldtype": "Button",
			"label": "Sync Item Groups",
			"insert_after": "column_break_sync_buttons",
		},
		{
			"fieldname": "button_sync_products",
			"fieldtype": "Button",
			"label": "Sync Products",
			"insert_after": "button_sync_item_groups",
		},
		{
			"fieldname": "button_sync_combos",
			"fieldtype": "Button",
			"label": "Sync Combos",
			"insert_after": "button_sync_products",
		},
		{
			"fieldname": "button_sync_customers",
			"fieldtype": "Button",
			"label": "Sync Customers",
			"insert_after": "button_sync_combos",
		},
		{
			"fieldname": "button_sync_orders",
			"fieldtype": "Button",
			"label": "Sync Orders",
			"insert_after": "button_sync_customers",
		},
		{
			"fieldname": "button_sync_users",
			"fieldtype": "Button",
			"label": "Sync Users",
			"insert_after": "button_sync_orders",
		},
		{
			"fieldname": "button_sync_payment_methods",
			"fieldtype": "Button",
			"label": "Sync Payment Methods",
			"insert_after": "button_sync_users",
		},
		{
			"fieldname": "button_sync_all_master_data",
			"fieldtype": "Button",
			"label": "Sync All Master Data",
			"insert_after": "button_sync_payment_methods",
		},
		{
			"fieldname": "button_sync_orders_invoices",
			"fieldtype": "Button",
			"label": "Sync Orders & Create Invoices",
			"insert_after": "button_sync_all_master_data",
		},
		{
			"fieldname": "button_create_invoices",
			"fieldtype": "Button",
			"label": "Create Sales Invoices",
			"insert_after": "button_sync_orders_invoices",
		},
		{
			"fieldname": "column_break_order_dates",
			"fieldtype": "Column Break",
			"insert_after": "button_create_invoices",
		},
		{
			"fieldname": "orders_sync_from_date",
			"fieldtype": "Date",
			"label": "Orders Sync From Date",
			"insert_after": "column_break_order_dates",
		},
		{
			"fieldname": "orders_sync_to_date",
			"fieldtype": "Date",
			"label": "Orders Sync To Date",
			"insert_after": "orders_sync_from_date",
		},
		{
			"fieldname": "orders_sync_date_field",
			"fieldtype": "Select",
			"label": "Orders Sync Date Field",
			"options": "business_date\nupdated_at\ncreated_at",
			"default": "business_date",
			"insert_after": "orders_sync_to_date",
		},
		{
			"fieldname": "section_break_branch_mapping",
			"fieldtype": "Section Break",
			"label": "Branch Cost Center Mapping",
			"insert_after": "orders_sync_date_field",
		},
		{
			"fieldname": "branch_cost_center_mapping",
			"fieldtype": "Table",
			"label": "Branch Cost Center Mapping",
			"options": "Branch Cost Center Mapping",
			"insert_after": "section_break_branch_mapping",
		},
	]
	
	# Add fields that don't exist
	for field_config in new_fields:
		if field_config["fieldname"] not in existing_fieldnames:
			doctype.append("fields", field_config)
	
	# Save doctype
	doctype.save(ignore_permissions=True)
	frappe.db.commit()
	
	frappe.msgprint("Foodics Settings fields have been added successfully.")

