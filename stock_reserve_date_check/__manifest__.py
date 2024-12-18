# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Stock reserve date check",
    "version": "14.0.1.0.1",
    "category": "Stock Management",
    "author": "Sergio Corato",
    "license": "AGPL-3",
    "summary": "Add logic to block confirmation of sale order on date not possible "
    "on product stock or predicted arrival and manufacturing time.",
    "website": "https://github.com/efatto/efatto",
    "depends": [
        "mrp_production_demo",
        "product_sellers_info",
        "sale_order_line_date",
        "sale_stock",
        "stock_move_available_date_expected",
    ],
    "data": [
        "views/sale.xml",
    ],
    "installable": True,
}
