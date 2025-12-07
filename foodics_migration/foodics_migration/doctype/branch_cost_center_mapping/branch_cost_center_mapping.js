// Copyright (c) 2025, ahmed and contributors
// For license information, please see license.txt

frappe.ui.form.on("Branch Cost Center Mapping", {
	refresh(frm) {
		// Add button to fetch branches from Foodics API
		if (frm.is_new()) {
			frm.add_custom_button(__("Fetch from Foodics"), function() {
				fetch_branches_from_foodics(frm);
			});
		}
	}
});

function fetch_branches_from_foodics(frm) {
	frappe.call({
		method: "foodics_migration.api.fetch_foodics_branches",
		callback: function(r) {
			if (r.message && r.message.length > 0) {
				show_branch_selection_dialog(frm, r.message);
			} else {
				frappe.msgprint(__("No branches found or error fetching branches"));
			}
		},
		error: function(r) {
			frappe.msgprint(__("Error fetching branches: {0}", [r.message || "Unknown error"]));
		}
	});
}

function show_branch_selection_dialog(frm, branches) {
	let dialog = new frappe.ui.Dialog({
		title: __("Select Branch"),
		fields: [
			{
				fieldtype: "Select",
				fieldname: "branch",
				label: __("Branch"),
				options: branches.map(b => `${b.name} (${b.id})`).join("\n"),
				reqd: 1
			}
		],
		primary_action_label: __("Select"),
		primary_action(values) {
			let selected = branches.find(b => `${b.name} (${b.id})` === values.branch);
			if (selected) {
				frm.set_value("foodics_branch_id", selected.id);
				frm.set_value("foodics_branch_name", selected.name);
				if (selected.reference) {
					frm.set_value("branch_reference", selected.reference);
				}
				dialog.hide();
			}
		}
	});
	dialog.show();
}

