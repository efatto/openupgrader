# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "MRP Production Move Price Sync",
    "version": "14.0.1.0.0",
    "development_status": "Beta",
    "license": "AGPL-3",
    "category": "Manufacturing",
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "summary": "Update cost of stock moves done in production when necessary, from "
    "purchase order, usually after having received related invoice.",
    "depends": [
        "account_invoice_update_purchase",
        "mrp_production_demo",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/mrp.xml",
        "wizard/mrp_sync_price.xml",
    ],
    "installable": True,
}
