# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

{
    "name": "Order point generator for range periods",
    "summary": "Mass configuration of stock order points",
    "version": "14.0.1.0.4",
    "author": "Sergio Corato",
    "category": "Warehouse",
    "license": "AGPL-3",
    "website": "https://github.com/efatto/efatto",
    "depends": [
        "mrp",
        "product_sellers_info",
        "product_supplierinfo_overtime_delay",
        "product_state",
        "purchase_stock",
        "stock_orderpoint_generator",
    ],
    "data": [
        "views/orderpoint_template_views.xml",
        "views/product.xml",
        "views/res_country.xml",
    ],
    "installable": True,
    "auto_install": False,
    "external_dependencies": {
        "python": ["scipy"],
    },
}
