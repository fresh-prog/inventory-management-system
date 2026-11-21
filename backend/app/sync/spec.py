"""What replicates from a branch to the cloud, and how.

Branch-owned rows have exactly ONE writer (the branch server). The cloud holds a read replica per
branch (its own PostgreSQL schema) and never edits these rows, so no last-write-wins is possible.
"""

# Alembic revision of the branch schema this code writes. Every event carries it.
SCHEMA_VERSION = "0002sync"
# Schema versions the cloud can apply (it must be upgraded before branches; supports N and N-1).
KNOWN_SCHEMA_VERSIONS = ("511ffdd9a91e", "0002sync")

# Foreign-key-safe order (parents first). Used for snapshots and as documentation.
REPLICATED_TABLES = [
    "branch", "app_user", "user_branch_role", "device", "ingredient_category", "supplier", "ingredient",
    "ingredient_conversion", "ingredient_branch_setting", "supplier_ingredient", "recipe", "recipe_version",
    "recipe_line", "stored_file", "purchase_invoice", "purchase_line", "purchase_attachment",
    "ingredient_price_history", "ingredient_current_cost", "supplier_payment", "payment_allocation",
    "supplier_credit", "supplier_ledger_entry", "stock_item", "stock_batch", "stock_movement",
    "stock_allocation", "production_run", "stock_count", "stock_count_line", "recipe_cost_snapshot",
    "audit_log",
]

# Columns that never leave the branch. NOT NULL ones get a placeholder value in the replica.
EXCLUDED_COLUMNS: dict[str, dict[str, object]] = {
    "app_user": {"password_hash": "!replica-no-login", "failed_logins": 0, "locked_until": None,
                 "token_version": 1},
    "stored_file": {"cloud_verified_at": None},
}

# Business events: insert-only on both sides (reversals are new rows, never edits).
IMMUTABLE_TABLES = {"stock_movement", "stock_allocation", "supplier_ledger_entry", "audit_log"}

# Event kinds
CHANGESET, SNAPSHOT = "changeset", "snapshot"
