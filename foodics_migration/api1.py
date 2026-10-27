# apps/foodics_migration/foodics_migration/api.py

from __future__ import annotations
import json
import time
from typing import Any, Dict, Iterable, Optional, List

import frappe
import requests

# ---------- SETTINGS ----------
DEFAULT_PER_PAGE = 100

def _cfg() -> frappe._dict:
	doc = frappe.get_single("Foodics Settings")
	if not doc.foodics_base_url:
		frappe.throw("Foodics Settings: `foodics_base_url` is required.")
	if not doc.foodics_token:
		frappe.throw("Foodics Settings: `foodics_token` is required.")
	return frappe._dict(
		base_url=doc.foodics_base_url.strip(),
		token=doc.foodics_token.strip(),
		per_page=int(doc.foodics_per_page or DEFAULT_PER_PAGE),
	)

def _safe_base_url(base_url: str) -> str:
	base = base_url.rstrip("/")
	if not base.startswith(("http://", "https://")):
		base = "https://" + base
	return base

def _new_session(token: str) -> requests.Session:
	s = requests.Session()
	s.headers.update({
		"Authorization": f"Bearer {token}",
		"Accept": "application/json",
		"User-Agent": "ERPNext-Foodics-Migration/1.0",
	})
	# Note: requests does not use Session.timeout automatically; pass timeout in each call.
	return s

def _get_with_backoff(s: requests.Session, url: str) -> Dict[str, Any]:
	backoff = 2.0
	for attempt in range(6):
		try:
			resp = s.get(url, timeout=60)  # enforce timeout here
		except requests.RequestException:
			if attempt == 5:
				raise
			time.sleep(backoff)
			backoff *= 1.6
			continue

		if resp.status_code == 429 or 500 <= resp.status_code < 600:
			ra = resp.headers.get("Retry-After")
			try:
				sleep_for = float(ra) if ra is not None else backoff
			except ValueError:
				sleep_for = backoff
			print(f"[warn] {resp.status_code} on {url} — retrying in {sleep_for:.1f}s")
			time.sleep(sleep_for)
			backoff *= 1.6
			continue

		resp.raise_for_status()
		return resp.json()
	raise requests.HTTPError(f"Failed to GET after retries: {url}")

# ---------- ITEM GROUP HELPERS ----------

def _ensure_item_group(name: str, parent: Optional[str] = "All Item Groups") -> str:
	"""Return the Item Group name; create if missing (under parent)."""
	if not name:
		name = "Unassigned (Foodics)"

	existing = frappe.db.get_value("Item Group", {"item_group_name": name}, "name")
	if existing:
		return existing

	parent_name = parent
	if parent_name and not frappe.db.exists("Item Group", parent_name):
		root = frappe.get_doc({
			"doctype": "Item Group",
			"item_group_name": parent_name,
			"is_group": 1,
			"parent_item_group": None
		})
		root.insert(ignore_permissions=True)

	doc = frappe.get_doc({
		"doctype": "Item Group",
		"item_group_name": name,
		"is_group": 0,
		"parent_item_group": parent_name if frappe.db.exists("Item Group", parent_name) else None,
	})
	doc.insert(ignore_permissions=True)
	print(f"    · created Item Group: {doc.name} (parent={parent_name})")
	return doc.name

# ---------- PRODUCT PAGINATION (include=category only) ----------

def _iter_foodics_products(cfg: frappe._dict, s: requests.Session) -> Iterable[Dict[str, Any]]:
	base = _safe_base_url(cfg.base_url)
	page = 1
	per_page = cfg.per_page
	# Always include category on every page (Foodics "next" may omit it)
	def page_url(p: int) -> str:
		return f"{base}/combos?filter[is_active]=&include=category&per_page={per_page}&page={p}"

	url = page_url(page)
	print(f"[info] Fetching Foodics products per_page={per_page}")

	def normalize_product(p: Dict[str, Any], cat_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
		attrs = p.get("attributes") or p
		out = dict(attrs)
		out["id"] = p.get("id") or attrs.get("id") or attrs.get("uuid")

		# Ensure category has name
		cat = out.get("category")
		if not (isinstance(cat, dict) and cat.get("name")):
			rel_cat = ((p.get("relationships") or {}).get("category") or {}).get("data") or {}
			cid = rel_cat.get("id")
			if cid and cid in cat_by_id:
				out["category"] = cat_by_id[cid]

		# groups not requested in this mode
		if "groups" not in out or not isinstance(out.get("groups"), list):
			out["groups"] = []
		return out

	while True:
		print(f"[info] === Page {page} ===")
		data = _get_with_backoff(s, url)

		# Build category lookup from "included" (JSON:API)
		included = data.get("included") or []
		cat_by_id: Dict[str, Dict[str, Any]] = {}
		for inc in included:
			itype = inc.get("type")
			iid = inc.get("id")
			attrs = inc.get("attributes") or {}
			name = attrs.get("name") or inc.get("name")
			if iid and itype in ("categories", "category"):
				cat_by_id[iid] = {"id": iid, "name": name}

		rows = data.get("data") or []
		print(f"[debug] fetched {len(rows)} products")

		if not rows:
			break

		for p in rows:
			np = normalize_product(p, cat_by_id)
			# print category for each product (as requested)
			cname = (np.get("category") or {}).get("name")
			print(f"[category] product {np.get('id')} -> {cname}")
			yield np

		# If the API says there's a next page, move there — but rebuild URL to keep include=category
		has_next = bool((data.get("links") or {}).get("next"))
		if not has_next:
			break

		page += 1
		url = page_url(page)


# ---------- MAPPING / UPSERT ----------

def _pick_item_group(product: Dict[str, Any]) -> str:
	"""Prefer first Foodics 'group' name; fallback to category name; else 'Unassigned (Foodics)'."""
	groups = product.get("groups") or []
	if groups:
		gname = (groups[0] or {}).get("name")
		if gname:
			return gname
	cat = product.get("category") or {}
	if cat.get("name"):
		return cat.get("name")
	return "Unassigned (Foodics)"

def _collect_groups_meta(product: Dict[str, Any]) -> Dict[str, Any]:
	groups = product.get("groups") or []
	return {
		"ids": [g.get("id") for g in groups if isinstance(g, dict)],
		"names": [g.get("name") for g in groups if isinstance(g, dict)],
	}

def _update_item_fields_from_foodics(item_doc, product: Dict[str, Any]) -> None:
	# item_name refresh (non-destructive)
	if product.get("name") and item_doc.item_name != product.get("name"):
		item_doc.item_name = product.get("name")

	# Item Group mapping
	ig_name = _pick_item_group(product)
	erp_ig = _ensure_item_group(ig_name, parent="All Item Groups")
	if item_doc.item_group != erp_ig:
		item_doc.item_group = erp_ig
		print(f"    · set item_group = {erp_ig}")

	# Save groups meta to custom fields if they exist (will be empty with include=category)
	meta = _collect_groups_meta(product)
	if frappe.get_meta("Item").has_field("foodics_group_ids"):
		item_doc.set("foodics_group_ids", json.dumps(meta["ids"], ensure_ascii=False))
		print(f"    · set foodics_group_ids (len={len(meta['ids'])})")
	if frappe.get_meta("Item").has_field("foodics_group_names"):
		item_doc.set("foodics_group_names", json.dumps(meta["names"], ensure_ascii=False))
		print(f"    · set foodics_group_names (len={len(meta['names'])})")

	# Optional: keep raw blob updated
	if frappe.get_meta("Item").has_field("foodics_product_data"):
		item_doc.set("foodics_product_data", json.dumps(product, ensure_ascii=False))

@frappe.whitelist()
def sync_foodics_products():
	"""
	Updates ALL existing Items (matched by custom field `foodics_product_id`)
	and sets the correct Item Group based on Foodics groups (if any) or category (since we only include category here).
	Auto-creates Item Groups as needed under 'All Item Groups'.
	Also prints each product's category name.
	"""
	cfg = _cfg()
	s = _new_session(cfg.token)

	processed = 0
	updated = 0
	missing = 0

	try:
		for prod in _iter_foodics_products(cfg, s):
			fid = prod.get("id")
			if not fid:
				continue

			item_name = frappe.db.get_value("Item", {"foodics_product_id": fid}, "name")
			if not item_name:
				missing += 1
				print(f"[skip] No Item with foodics_product_id={fid}. (Create phase was earlier.)")
				continue

			print(f"[info] Update Item: {item_name} (Foodics {fid})")
			item_doc = frappe.get_doc("Item", item_name)

			_update_item_fields_from_foodics(item_doc, prod)
			item_doc.save(ignore_permissions=True)
			processed += 1
			updated += 1

		frappe.db.commit()
		print(f"[info] Done. processed={processed}, updated={updated}, missing_items={missing}")

	except Exception as e:
		frappe.db.rollback()
		print(f"[error] Sync aborted: {e}")
		raise
