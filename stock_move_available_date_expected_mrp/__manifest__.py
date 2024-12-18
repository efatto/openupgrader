# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Stock move available date expected with MRP",
    "version": "14.0.1.0.1",
    "category": "Stock",
    "summary": """
    Add facility to view and change sale reserved on stock moves for MRP components.
    """,
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "license": "AGPL-3",
    "depends": [
        "mrp",
        "stock_move_available_date_expected",
    ],
    "data": [
        "views/mrp.xml",
    ],
    "installable": True,
}
