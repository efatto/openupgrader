# Copyright 2018-2021 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Stock inventory valuation report py3o",
    "version": "12.0.1.0.0",
    "category": "other",
    "description": """
    Stock inventory ods report for valuation.
    """,
    "author": "Sergio Corato",
    "website": "https://github.com/sergiocorato/efatto",
    "license": "AGPL-3",
    "depends": [
        "stock_inventory_valuation",
        "report_py3o",
    ],
    "data": [
        "report/report.xml",
    ],
    "installable": True,
}
