// Copyright (c) 2025, ahmed and contributors
// For license information, please see license.txt

frappe.ui.form.on("Foodics Settings", {
	refresh(frm) {
		// Handle button clicks for sync buttons
		if (frm.is_new()) {
			return;
		}
		
		// Add button to fetch branches and populate mapping table
		if (frm.doc.branch_cost_center_mapping) {
			frm.add_custom_button(__("Fetch Branches from Foodics"), function() {
				fetch_and_populate_branches(frm);
			}, __("Branch Cost Center Mapping"));
		}
	},
	
	button_sync_item_groups(frm) {
		sync_item_groups(frm);
	},
	
	button_sync_products(frm) {
		sync_products(frm);
	},
	
	button_sync_combos(frm) {
		sync_combos(frm);
	},
	
	button_sync_customers(frm) {
		sync_customers(frm);
	},
	
	button_sync_orders(frm) {
		sync_orders(frm);
	},
	
	button_sync_users(frm) {
		sync_users(frm);
	},
	
	button_sync_payment_methods(frm) {
		sync_payment_methods(frm);
	},
	
	button_create_invoices(frm) {
		create_sales_invoices_from_orders(frm);
	},
	
	button_sync_all_master_data(frm) {
		sync_all_master_data(frm);
	},
	
	button_sync_orders_invoices(frm) {
		sync_orders_and_create_invoices(frm);
	}
});

function show_sync_actions_dialog(frm) {
	let dialog = new frappe.ui.Dialog({
		title: __("Foodics Sync Actions"),
		fields: [
			{
				fieldtype: "Section Break",
				label: __("Sync Options"),
			},
			{
				fieldtype: "Button",
				label: __("Sync Products"),
				click: function() {
					sync_products(frm);
					dialog.hide();
				}
			},
			{
				fieldtype: "Button",
				label: __("Sync Combos"),
				click: function() {
					sync_combos(frm);
					dialog.hide();
				}
			},
			{
				fieldtype: "Button",
				label: __("Sync Customers"),
				click: function() {
					sync_customers(frm);
					dialog.hide();
				}
			},
			{
				fieldtype: "Column Break",
			},
			{
				fieldtype: "Button",
				label: __("Sync Orders"),
				click: function() {
					sync_orders(frm);
					dialog.hide();
				}
			},
		],
	});
	dialog.show();
}

function sync_item_groups(frm) {
	frappe.confirm(
		__("This will sync all item groups (categories) from Foodics. Continue?"),
		function() {
			frappe.call({
				method: "foodics_migration.api.sync_foodics_item_groups",
				freeze: true,
				freeze_message: __("Syncing item groups from Foodics..."),
				callback: function(r) {
					if (r.exc) {
						frappe.msgprint(__("Error syncing item groups: {0}", [r.exc]));
					} else {
						frappe.show_alert({
							message: __("Item groups sync completed successfully"),
							indicator: "green",
						});
					}
				},
			});
		}
	);
}

function sync_products(frm) {
	frappe.confirm(
		__("This will sync all products from Foodics. Continue?"),
		function() {
			frappe.call({
				method: "foodics_migration.api.sync_foodics_products",
				freeze: true,
				freeze_message: __("Syncing products from Foodics..."),
				callback: function(r) {
					if (r.exc) {
						frappe.msgprint(__("Error syncing products: {0}", [r.exc]));
					} else {
						frappe.show_alert({
							message: __("Products sync completed successfully"),
							indicator: "green",
						});
					}
				},
			});
		}
	);
}

function sync_combos(frm) {
	frappe.confirm(
		__("This will sync all combos from Foodics. Continue?"),
		function() {
			frappe.call({
				method: "foodics_migration.api_combo.sync_foodics_combos",
				freeze: true,
				freeze_message: __("Syncing combos from Foodics..."),
				callback: function(r) {
					if (r.exc) {
						frappe.msgprint(__("Error syncing combos: {0}", [r.exc]));
					} else {
						frappe.show_alert({
							message: __("Combos sync completed successfully"),
							indicator: "green",
						});
					}
				},
			});
		}
	);
}

function sync_customers(frm) {
	frappe.confirm(
		__("This will sync all customers from Foodics. Continue?"),
		function() {
			frappe.call({
				method: "foodics_migration.api.sync_foodics_customers",
				freeze: true,
				freeze_message: __("Syncing customers from Foodics..."),
				callback: function(r) {
					if (r.exc) {
						frappe.msgprint(__("Error syncing customers: {0}", [r.exc]));
					} else {
						frappe.show_alert({
							message: __("Customers sync completed successfully"),
							indicator: "green",
						});
					}
				},
			});
		}
	);
}

function sync_orders(frm) {
	// Show date range dialog for orders
	let dialog = new frappe.ui.Dialog({
		title: __("Sync Orders"),
		fields: [
			{
				fieldtype: "Date",
				fieldname: "from_date",
				label: __("From Date"),
				default: frm.doc.orders_sync_from_date || "",
			},
			{
				fieldtype: "Date",
				fieldname: "to_date",
				label: __("To Date"),
				default: frm.doc.orders_sync_to_date || "",
			},
			{
				fieldtype: "Select",
				fieldname: "date_field",
				label: __("Date Field"),
				options: "business_date\nupdated_at\ncreated_at",
				default: frm.doc.orders_sync_date_field || "business_date",
			},
		],
		primary_action_label: __("Sync"),
		primary_action(values) {
			// Update settings with date filters
			frappe.db.set_value("Foodics Settings", "Foodics Settings", {
				orders_sync_from_date: values.from_date || null,
				orders_sync_to_date: values.to_date || null,
				orders_sync_date_field: values.date_field || "business_date",
			}).then(() => {
				dialog.hide();
				frappe.confirm(
					__("This will sync orders from Foodics. Continue?"),
					function() {
						frappe.call({
							method: "foodics_migration.api_orders.sync_foodics_orders",
							freeze: true,
							freeze_message: __("Syncing orders from Foodics..."),
							callback: function(r) {
								if (r.exc) {
									frappe.msgprint(__("Error syncing orders: {0}", [r.exc]));
								} else {
									frappe.show_alert({
										message: __("Orders sync completed successfully"),
										indicator: "green",
									});
									frm.reload_doc();
								}
							},
						});
					}
				);
			});
		},
	});
	dialog.show();
}

function sync_users(frm) {
	frappe.confirm(
		__("This will sync all users from Foodics. Continue?"),
		function() {
			frappe.call({
				method: "foodics_migration.api.sync_foodics_users",
				freeze: true,
				freeze_message: __("Syncing users from Foodics..."),
				callback: function(r) {
					if (r.exc) {
						frappe.msgprint(__("Error syncing users: {0}", [r.exc]));
					} else {
						let message = __("Users sync completed successfully");
						if (r.message) {
							message += `\nCreated: ${r.message.created || 0}, Updated: ${r.message.updated || 0}, Skipped: ${r.message.skipped || 0}, Failed: ${r.message.failed || 0}`;
						}
						frappe.show_alert({
							message: message,
							indicator: "green",
						});
					}
				},
			});
		}
	);
}

function sync_payment_methods(frm) {
	frappe.confirm(
		__("This will sync all payment methods from Foodics. Continue?"),
		function() {
			frappe.call({
				method: "foodics_migration.api.sync_foodics_payment_methods",
				freeze: true,
				freeze_message: __("Syncing payment methods from Foodics..."),
				callback: function(r) {
					if (r.exc) {
						frappe.msgprint(__("Error syncing payment methods: {0}", [r.exc]));
					} else {
						let message = __("Payment methods sync completed successfully");
						if (r.message) {
							message += `\nCreated: ${r.message.created || 0}, Updated: ${r.message.updated || 0}, Skipped: ${r.message.skipped || 0}, Failed: ${r.message.failed || 0}`;
						}
						frappe.show_alert({
							message: message,
							indicator: "green",
						});
					}
				},
			});
		}
	);
}

function sync_all_master_data(frm) {
	frappe.confirm(
		__("This will sync all master data from Foodics (Item Groups, Products, Combos, Customers, Users, Payment Methods). This will run in the background and may take several minutes. Continue?"),
		function() {
			frappe.call({
				method: "foodics_migration.api.sync_all_foodics_master_data",
				args: {
					enqueue: true
				},
				freeze: true,
				freeze_message: __("Queuing master data sync job..."),
				callback: function(r) {
					if (r.exc) {
						frappe.msgprint(__("Error queuing master data sync: {0}", [r.exc]));
					} else {
						let message = __("Master data sync job has been queued successfully!");
						if (r.message) {
							if (r.message.job_id) {
								message += `\n\nJob ID: ${r.message.job_id}`;
							}
							if (r.message.sync_log_name) {
								message += `\n\nView progress: <a href="/app/foodics-sync-log/${r.message.sync_log_name}" target="_blank">${r.message.sync_log_name}</a>`;
							}
							message += `\n\nThe sync is running in the background. Check the Foodics Sync Log list for progress and results.`;
						}
						
						frappe.msgprint({
							title: __("Job Queued"),
							message: message,
							indicator: "blue"
						});
						
						// Refresh form to show updated data
						frm.reload_doc();
					}
				},
			});
		}
	);
}

function sync_orders_and_create_invoices(frm) {
	let dialog = new frappe.ui.Dialog({
		title: __("Sync Orders & Create Invoices"),
		fields: [
			{
				fieldtype: "Date",
				label: __("From Date"),
				fieldname: "from_date",
				reqd: 1,
				default: frappe.datetime.add_months(frappe.datetime.get_today(), -1)
			},
			{
				fieldtype: "Date",
				label: __("To Date"),
				fieldname: "to_date",
				reqd: 1,
				default: frappe.datetime.get_today()
			},
			{
				fieldtype: "Select",
				label: __("Date Field"),
				fieldname: "date_field",
				options: "business_date\ncreated_at\nupdated_at",
				default: "business_date",
				reqd: 1
			},
			{
				fieldtype: "Section Break",
				label: __("Note"),
			},
			{
				fieldtype: "HTML",
				options: __("<p style='color: #666;'>This will sync only fully paid orders from Foodics and create paid Sales Invoices. Orders that already exist or have invoices will be skipped.</p><p style='color: #666;'><strong>Note:</strong> This will run in the background to prevent timeouts.</p>")
			}
		],
		primary_action_label: __("Start Sync"),
		primary_action(values) {
			dialog.hide();
			
			frappe.confirm(
				__("This will sync fully paid orders and create Sales Invoices in the background. Continue?"),
				function() {
					frappe.call({
						method: "foodics_migration.api_orders.sync_orders_and_create_invoices",
						args: {
							from_date: values.from_date,
							to_date: values.to_date,
							date_field: values.date_field,
							enqueue: true
						},
						freeze: true,
						freeze_message: __("Queuing sync job..."),
						callback: function(r) {
							if (r.exc) {
								frappe.msgprint(__("Error: {0}", [r.exc]));
							} else {
								let message = __("Orders and invoices sync job has been queued!");
								if (r.message) {
									if (r.message.job_id) {
										message += `\n\nJob ID: ${r.message.job_id}`;
									}
									if (r.message.sync_log_name) {
										message += `\n\nView progress: <a href="/app/foodics-sync-log/${r.message.sync_log_name}" target="_blank">${r.message.sync_log_name}</a>`;
									}
									message += `\n\nThe sync is running in the background. Check the Foodics Sync Log list for progress and results.`;
								}
								
								frappe.msgprint({
									title: __("Job Queued"),
									message: message,
									indicator: "blue"
								});
								
								frm.reload_doc();
							}
						},
					});
				}
			);
		}
	});
	dialog.show();
}

function create_sales_invoices_from_orders(frm) {
	// Show date range dialog
	let dialog = new frappe.ui.Dialog({
		title: __("Create Sales Invoices from Foodics Orders"),
		fields: [
			{
				fieldtype: "Date",
				fieldname: "from_date",
				label: __("From Date"),
				reqd: 1,
				default: frm.doc.orders_sync_from_date || "",
			},
			{
				fieldtype: "Date",
				fieldname: "to_date",
				label: __("To Date"),
				reqd: 1,
				default: frm.doc.orders_sync_to_date || "",
			},
			{
				fieldtype: "Select",
				fieldname: "date_field",
				label: __("Date Field"),
				options: "business_date\nupdated_at\ncreated_at",
				default: frm.doc.orders_sync_date_field || "business_date",
			},
			{
				fieldtype: "Check",
				fieldname: "only_without_invoice",
				label: __("Only Orders Without Sales Invoice"),
				default: 1,
			},
		],
		primary_action_label: __("Create Invoices"),
		primary_action(values) {
			dialog.hide();
			
			// First, get count of orders to process
			frappe.call({
				method: "foodics_migration.api_orders.get_orders_count_for_invoice_creation",
				args: {
					from_date: values.from_date,
					to_date: values.to_date,
					date_field: values.date_field,
					only_without_invoice: values.only_without_invoice,
				},
				callback: function(r) {
					if (r.exc) {
						frappe.msgprint(__("Error getting orders count: {0}", [r.exc]));
						return;
					}
					
					let count = r.message || 0;
					if (count === 0) {
						frappe.msgprint(__("No orders found matching the criteria."));
						return;
					}
					
					frappe.confirm(
						__("This will create Sales Invoices for {0} Foodics Order(s). Continue?", [count]),
						function() {
							// Yes - proceed
							frappe.call({
								method: "foodics_migration.api_orders.create_sales_invoices_from_orders",
								args: {
									from_date: values.from_date,
									to_date: values.to_date,
									date_field: values.date_field,
									only_without_invoice: values.only_without_invoice,
								},
								freeze: true,
								freeze_message: __("Creating Sales Invoices from Foodics Orders..."),
								callback: function(r) {
									if (r.exc) {
										frappe.msgprint(__("Error creating invoices: {0}", [r.exc]));
									} else {
										let result = r.message || {};
										let message = __("Sales Invoices creation completed.");
										if (result.created) {
											message += `\nCreated: ${result.created}`;
										}
										if (result.skipped) {
											message += `\nSkipped: ${result.skipped}`;
										}
										if (result.failed) {
											message += `\nFailed: ${result.failed}`;
										}
										frappe.show_alert({
											message: message,
											indicator: "green",
										});
										frm.reload_doc();
									}
								},
							});
						}
					);
				},
			});
		},
	});
	dialog.show();
}

function fetch_and_populate_branches(frm) {
	frappe.confirm(
		__("This will fetch all branches from Foodics and populate the Branch Cost Center Mapping table. Existing mappings will be preserved. Continue?"),
		function() {
			frappe.call({
				method: "foodics_migration.api.fetch_foodics_branches",
				freeze: true,
				freeze_message: __("Fetching branches from Foodics..."),
				callback: function(r) {
					if (r.exc) {
						frappe.msgprint(__("Error fetching branches: {0}", [r.exc]));
					} else if (r.message && r.message.length > 0) {
						populate_branch_mapping_table(frm, r.message);
					} else {
						frappe.msgprint(__("No branches found in Foodics"));
					}
				},
			});
		}
	);
}

function populate_branch_mapping_table(frm, branches) {
	// Get existing branch IDs to avoid duplicates
	let existing_branch_ids = {};
	if (frm.doc.branch_cost_center_mapping) {
		frm.doc.branch_cost_center_mapping.forEach(function(row) {
			if (row.foodics_branch_id) {
				existing_branch_ids[row.foodics_branch_id] = true;
			}
		});
	}
	
	// Add new branches that don't exist yet
	let added_count = 0;
	branches.forEach(function(branch) {
		if (!existing_branch_ids[branch.id]) {
			let row = frm.add_child("branch_cost_center_mapping");
			row.foodics_branch_id = branch.id;
			row.foodics_branch_name = branch.name || "";
			row.branch_reference = branch.reference || "";
			// cost_center will be left empty for user to fill
			added_count++;
		}
	});
	
	frm.refresh_field("branch_cost_center_mapping");
	
	if (added_count > 0) {
		frappe.show_alert({
			message: __("Added {0} branch(es) to the mapping table. Please map them to Cost Centers.", [added_count]),
			indicator: "green",
		});
	} else {
		frappe.show_alert({
			message: __("All branches are already in the mapping table."),
			indicator: "blue",
		});
	}
}
