# Copyright 2021 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "CRM Lead Warehouse Orderpoint",
    "version": "14.0.1.0.0",
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "category": "Tools",
    "license": "AGPL-3",
    "depends": [
        "sale_crm",
        "stock",
    ],
    "summary": "Add crm lead for products, with reflex on stock "
    "warehouse orderpoint.",
    "data": [
        "data/ir_config_parameter.xml",
        "views/product_category.xml",
        "views/crm.xml",
        "views/stock_warehouse_orderpoint.xml",
    ],
    "installable": True,
}
