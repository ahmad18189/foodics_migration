# Foodics Migration Setup Instructions

## Initial Setup

### 1. Create Custom Fields

Run the following command to create custom fields on Customer, Sales Invoice, Item, Item Group, and Product Bundle:

```bash
bench --site food.milestoneksa.com execute foodics_migration.patches.v1_0.add_foodics_custom_fields.execute
```

### 2. Add Fields to Foodics Settings

Run the following command to add sync control fields and branch mapping to Foodics Settings:

```bash
bench --site food.milestoneksa.com execute foodics_migration.patches.v1_0.add_foodics_settings_fields.execute
```

### 3. Create Branch Cost Center Mapping DocType

The Branch Cost Center Mapping child doctype will be created automatically when you migrate the app:

```bash
bench --site food.milestoneksa.com migrate
```

Or if you need to create it manually, it's located at:
`foodics_migration/foodics_migration/doctype/branch_cost_center_mapping/`

## Usage

### Sync Actions

1. Open **Foodics Settings** in ERPNext
2. Configure your Foodics API credentials
3. Set update checkboxes (default: 0 = skip if exists, 1 = update if exists)
4. Configure date range for order sync (optional)
5. Set up Branch to Cost Center mappings
6. Click sync buttons:
   - **Sync Products** - Syncs all products from Foodics
   - **Sync Combos** - Syncs all combos from Foodics
   - **Sync Customers** - Syncs all customers from Foodics
   - **Sync Orders** - Syncs orders (with optional date filtering) and creates Sales Invoices

### Branch Cost Center Mapping

1. In Foodics Settings, go to "Branch Cost Center Mapping" section
2. Click "Fetch from Foodics" button to load branches from Foodics API
3. Select a branch and map it to an ERPNext Cost Center
4. Save the settings

### Order Sync with Date Filtering

1. Set `orders_sync_from_date` and `orders_sync_to_date` in Foodics Settings
2. Select `orders_sync_date_field` (business_date, updated_at, or created_at)
3. Click "Sync Orders" button
4. Orders in the date range will be synced
5. Sales Invoices will be created automatically if `create_sales_invoices` is enabled

## API Functions

### Fetch Branches

```bash
bench --site food.milestoneksa.com execute foodics_migration.api.get_foodics_branches
```

### Sync Functions

```bash
# Sync Products
bench --site food.milestoneksa.com execute foodics_migration.api.sync_foodics_products

# Sync Combos
bench --site food.milestoneksa.com execute foodics_migration.api_combo.sync_foodics_combos

# Sync Customers
bench --site food.milestoneksa.com execute foodics_migration.api.sync_foodics_customers

# Sync Orders
bench --site food.milestoneksa.com execute foodics_migration.api_orders.sync_foodics_orders
```

### Create Sales Invoice from Order

```bash
bench --site food.milestoneksa.com execute foodics_migration.api_orders.create_sales_invoice_from_order --kwargs "{'foodics_order_name': 'ORDER-00001'}"
```

## Features

- **Update Control**: Checkboxes to control whether existing records should be updated (default: skip)
- **Date Filtering**: Filter order sync by date range (business_date, updated_at, or created_at)
- **Branch Mapping**: Map Foodics branches to ERPNext Cost Centers
- **Sales Invoice Creation**: Automatically create Sales Invoices from synced orders
- **Foodics ID Tracking**: All synced records store their Foodics IDs for reference




















