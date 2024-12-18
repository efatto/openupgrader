# Copyright 2019 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
{
    "name": "Module to configure sale",
    "version": "14.0.1.0.0",
    "category": "other",
    "summary": """
Module to configure sale.
Add the next groups to base user:
* sale.group_discount_per_so_line
* sale.group_delivery_invoice_address
* sale.group_warning_sale
Also set tax calculation rouding method as 'globally' and enable group sale
delivery address.
""",
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "license": "AGPL-3",
    "depends": [
        "sale",
    ],
    "data": [
        "data/group.xml",
    ],
    "installable": True,
}
