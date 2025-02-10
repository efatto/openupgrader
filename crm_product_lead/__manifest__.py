# Copyright 2021 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Sale Product Lead",
    "version": "14.0.1.0.0",
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "category": "Tools",
    "license": "AGPL-3",
    "depends": [
        "sale_crm",
        "stock",
    ],
    "summary": """Add to CRM lead:
- product
- estimated yearly quantity (EYQ)
- date start (può andare bene il campo "chiusura attesa"?)
If probability is ≥ 50%, add 20% of EYQ to reorder rules of all children of the BOM,
except for category with a SPECIAL flag.
If lead is won, add the residual 80% of EYQ to the same reorder rules.
Probability and reordering rules additional values are configurable with key
crm.product.lead.probability, the default value is: [(50, 20)]
You could put more values like: [(30, 10), (50, 20), (70, 100)]
""",
    "data": [
        "data/ir_config_parameter.xml",
        "views/product_category.xml",
        "views/crm.xml",
        "views/stock_warehouse_orderpoint.xml",
    ],
    "installable": True,
}
