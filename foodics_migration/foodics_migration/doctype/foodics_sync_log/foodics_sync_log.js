// Copyright (c) 2025, ahmed and contributors
// For license information, please see license.txt

frappe.ui.form.on("Foodics Sync Log", {
	refresh(frm) {
		// Add "Continue Sync" button for interrupted "Orders with Invoices" syncs
		if (frm.doc.sync_type === "Orders with Invoices" && 
		    (frm.doc.status === "Failed" || frm.doc.status === "Partial" || frm.doc.status === "Stopped")) {
			
			frm.add_custom_button(__("Continue Sync"), function() {
				continue_sync(frm);
			}, __("Actions"));
		}
		
		// Allow manual status editing for flexibility
		if (frm.doc.status) {
			frm.set_df_property("status", "read_only", 0);
		}
	},
});

function continue_sync(frm) {
	frappe.confirm(
		__("This will resume the sync from where it stopped. Continue?"),
		function() {
			// Yes - proceed
			frappe.call({
				method: "foodics_migration.api_orders.resume_sync_orders",
				args: {
					sync_log_name: frm.doc.name
				},
				freeze: true,
				freeze_message: __("Resuming sync..."),
				callback: function(r) {
					if (r.exc) {
						frappe.msgprint({
							title: __("Error"),
							message: __("Error resuming sync: {0}", [r.exc]),
							indicator: "red"
						});
					} else {
						let message = __("Sync has been resumed!");
						if (r.message) {
							if (r.message.resume_from_date) {
								message += `\n\nResuming from date: ${r.message.resume_from_date}`;
							}
							if (r.message.resume_page) {
								message += `\n\nResuming from page: ${r.message.resume_page}`;
							}
							message += `\n\nThe sync is running in the background. This page will auto-refresh to show progress.`;
						}
						
						frappe.show_alert({
							message: message,
							indicator: "blue"
						});
						
						// Reload the form to show updated status
						setTimeout(function() {
							frm.reload_doc();
						}, 2000);
					}
				},
			});
		}
	);
}
