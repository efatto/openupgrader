# Copyright 2024 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
{
    "name": "Purchase Propagate Cancel Visibility",
    "version": "14.0.1.0.0",
    "category": "other",
    "summary": """
    When MTO route is removed from a product which has draft RfQ and OUT from Sale
    Orders are done, the propagation cancel option must be removed manually from
    purchase order line to proceed.
    """,
    "author": "Sergio Corato",
    "website": "https://github.com/sergiocorato/efatto",
    "license": "AGPL-3",
    "depends": [
        "purchase_stock",
    ],
    "data": [
        "views/purchase.xml",
    ],
    "installable": True,
}
