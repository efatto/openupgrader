# Copyright 2019 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
{
    "name": "Module to configure stock",
    "version": "14.0.1.0.0",
    "category": "other",
    "summary": """
Module to configure stock.
Add the next groups to base user:
* stock.group_stock_multi_locations
* stock.group_stock_adv_location
* uom.group_uom
""",
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "license": "AGPL-3",
    "depends": [
        "stock",
    ],
    "data": [
        "data/group.xml",
    ],
    "installable": True,
}
