# Copyright 2024 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
{
    "name": "MRP Subcontracting auto complete on IN validation",
    "version": "14.0.1.0.0",
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "summary": "Validate a IN picking with submanufactured products will "
    "complete the production, even if the qty production is the "
    "same of qty requested.",
    "license": "AGPL-3",
    "category": "Manufacture",
    "depends": [
        "mrp_subcontracting",
        "mrp_subcontracting_purchase_link",
        "purchase_stock",
    ],
    "data": [],
    "installable": True,
}
