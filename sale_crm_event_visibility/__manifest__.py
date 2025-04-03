# Copyright 2025 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
{
    "name": "Sale order event link",
    "summary": "Show event in sale order",
    "version": "14.0.1.0.0",
    "category": "Sales",
    "website": "https://github.com/efatto/efatto",
    "author": "Sergio Corato",
    "maintainers": ["sergiocorato"],
    "license": "AGPL-3",
    "installable": True,
    "depends": [
        "event",
        "sale_crm",
    ],
    "data": [
        "views/sale.xml",
    ],
}
