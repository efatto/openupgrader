# Copyright 2021-2023 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Account Invoice update purchase",
    "version": "14.0.1.0.0",
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "license": "AGPL-3",
    "category": "Accounting",
    "depends": [
        "account_invoice_triple_discount",
        "purchase_stock_price_unit_sync",
        "purchase_triple_discount",
    ],
    "data": [
        "security/security.xml",
        "views/account_view.xml",
    ],
    "installable": True,
}
