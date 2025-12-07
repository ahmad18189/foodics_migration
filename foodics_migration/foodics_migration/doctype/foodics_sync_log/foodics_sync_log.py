# Copyright (c) 2025, ahmed and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from datetime import datetime
import json


class FoodicsSyncLog(Document):
	def before_insert(self):
		if not self.started_at:
			self.started_at = frappe.utils.now()
		if not self.status:
			self.status = "In Progress"
		if not self.log_details_html:
			self.log_details_html = "<h3>Sync Log Started</h3><p>Sync Type: {}</p><p>Started At: {}</p><hr>".format(
				self.sync_type, self.started_at
			)
		if not self.log_details_txt:
			self.log_details_txt = "Sync Log Started\nSync Type: {}\nStarted At: {}\n{}\n".format(
				self.sync_type, self.started_at, "=" * 50
			)

	def add_log_entry(self, message, record_type=None, record_name=None, action="INFO"):
		"""
		Add a log entry to both HTML and TXT logs.
		
		Args:
			message: Log message
			record_type: Type of record (e.g., "Item", "Customer", "Order")
			record_name: Name of the record
			action: "CREATED", "UPDATED", "SKIPPED", "FAILED", "INFO"
		"""
		timestamp = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S")
		
		# Determine color/style based on action
		color_map = {
			"CREATED": "#28a745",  # Green
			"UPDATED": "#17a2b8",  # Blue
			"SKIPPED": "#ffc107",  # Yellow
			"FAILED": "#dc3545",   # Red
			"INFO": "#6c757d"      # Gray
		}
		color = color_map.get(action, "#6c757d")
		
		# HTML entry
		html_entry = f"""
		<div style="margin: 5px 0; padding: 5px; border-left: 3px solid {color};">
			<strong>[{timestamp}]</strong> <span style="color: {color}; font-weight: bold;">{action}</span>
			{(' - ' + record_type + ': ' + record_name if record_type and record_name else '')}
			<br>{message}
		</div>
		"""
		
		# TXT entry
		txt_entry = f"[{timestamp}] {action}"
		if record_type and record_name:
			txt_entry += f" - {record_type}: {record_name}"
		txt_entry += f"\n{message}\n"
		
		# Append to logs
		if not self.log_details_html:
			self.log_details_html = ""
		if not self.log_details_txt:
			self.log_details_txt = ""
		
		self.log_details_html += html_entry
		self.log_details_txt += txt_entry
		
		# Auto-save if document exists
		if self.name:
			try:
				self.save(ignore_permissions=True)
				frappe.db.commit()
			except Exception:
				pass  # Don't fail if save fails

	def finalize_log(self, status="Completed"):
		"""
		Mark log as completed and add final summary.
		"""
		self.completed_at = frappe.utils.now()
		self.status = status
		
		# Add summary to logs
		summary_html = f"""
		<hr>
		<h3>Sync Summary</h3>
		<table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
			<tr><th>Metric</th><th>Count</th></tr>
			<tr><td>Total Records</td><td>{self.total_records or 0}</td></tr>
			<tr><td>Created</td><td style="color: #28a745;">{self.created or 0}</td></tr>
			<tr><td>Updated</td><td style="color: #17a2b8;">{self.updated or 0}</td></tr>
			<tr><td>Skipped</td><td style="color: #ffc107;">{self.skipped or 0}</td></tr>
			<tr><td>Failed</td><td style="color: #dc3545;">{self.failed or 0}</td></tr>
		"""
		if self.invoices_created:
			summary_html += f'<tr><td>Invoices Created</td><td style="color: #28a745;">{self.invoices_created or 0}</td></tr>'
		summary_html += f"""
		</table>
		<p><strong>Status:</strong> {status}</p>
		<p><strong>Completed At:</strong> {self.completed_at}</p>
		"""
		
		summary_txt = f"""
{'=' * 50}
Sync Summary
{'=' * 50}
Total Records: {self.total_records or 0}
Created: {self.created or 0}
Updated: {self.updated or 0}
Skipped: {self.skipped or 0}
Failed: {self.failed or 0}
"""
		if self.invoices_created:
			summary_txt += f"Invoices Created: {self.invoices_created or 0}\n"
		summary_txt += f"""
Status: {status}
Completed At: {self.completed_at}
"""
		
		if not self.log_details_html:
			self.log_details_html = ""
		if not self.log_details_txt:
			self.log_details_txt = ""
		
		self.log_details_html += summary_html
		self.log_details_txt += summary_txt
		
		self.save(ignore_permissions=True)
		frappe.db.commit()

