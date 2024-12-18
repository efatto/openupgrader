# Copyright 2018 Alex Comba - Agile Business Group
# Copyright 2016-2018 Akretion
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

{
    "name": "Sale MRP info Link",
    "summary": "Show info on manufacturing orders generated from sales order",
    "version": "14.0.1.0.0",
    "category": "Sales Management",
    "website": "https://github.com/efatto/efatto",
    "author": "Sergio Corato",
    "license": "AGPL-3",
    "application": False,
    "installable": True,
    "depends": [
        "mrp_sale_info",
    ],
    "data": [
        "views/mrp_production.xml",
    ],
}
