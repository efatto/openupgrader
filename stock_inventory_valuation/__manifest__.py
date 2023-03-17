# Copyright 2018-2021 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Stock inventory valuation by date",
    "version": "12.0.1.0.0",
    "category": "other",
    "description": """
    Stock inventory valuation by FIFO, LIFO, AVERAGE or STANDARD by date.
    """,
    "author": "Sergio Corato",
    "website": "https://github.com/sergiocorato/efatto",
    "license": "AGPL-3",
    "depends": [
        "purchase",
        "stock_account",
        "stock_account_inventory_force_date",
        "stock_inventory_preparation_filter",
    ],
    "data": [
        "views/stock_inventory.xml",
    ],
    "installable": True,
}
