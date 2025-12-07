// Copyright (c) 2025, ahmed and contributors
// For license information, please see license.txt

frappe.ui.form.on("Foodics Sync Log", {
	refresh(frm) {
		// Make log fields read-only after completion
		if (frm.doc.status === "Completed" || frm.doc.status === "Failed") {
			frm.set_read_only();
		}
		
		// Add button to view log in new window
		if (frm.doc.log_details_html) {
			frm.add_custom_button(__("View HTML Log"), function() {
				frappe.msgprint({
					title: __("Sync Log"),
					message: frm.doc.log_details_html,
					indicator: "blue"
				});
			});
		}
	}
});

