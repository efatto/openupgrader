# Copyright 2024 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

{
    "name": "Supplierinfo Search Vendor Code",
    "version": "14.0.1.0.0",
    "category": "other",
    "summary": """
    Add field vendor code and name to supplierinfo search.
    """,
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "license": "AGPL-3",
    "depends": [
        "stock",
    ],
    "data": [
        "views/stock.xml",
    ],
    "installable": True,
}
