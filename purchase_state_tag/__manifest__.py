# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Purchase tag on state",
    "version": "14.0.1.0.0",
    "category": "other",
    "author": "Sergio Corato",
    "summary": "Add purchase tag linked to order state.",
    "website": "https://github.com/efatto/efatto",
    "license": "AGPL-3",
    "depends": [
        "purchase",
        "web",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/purchase.xml",
    ],
    "installable": True,
}
