# Copyright 2021-2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Sale stock mrp produce delay",
    "version": "14.0.1.0.1",
    "category": "other",
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "license": "AGPL-3",
    "depends": [
        "product_is_kit",
        "sale_order_archive",
        "stock_reserve_date_check",
    ],
    "data": [
        "views/product_view.xml",
        "views/sale_order_view.xml",
    ],
    "qweb": [
        "static/src/xml/qty_at_date.xml",
    ],
    "installable": True,
}
