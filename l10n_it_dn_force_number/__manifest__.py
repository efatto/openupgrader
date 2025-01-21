# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Force DDT Number",
    "version": "14.0.1.0.1",
    "category": "Stock",
    "summary": """
This module allows to force the ddt numbering.\n
It add the ddt name field to ddt wizard creation. If user fills that field, the typed
value will be used as ddt number.\n
Otherwise, the next sequence number will be retrieved and saved.\n
So, the new field has to be used when user doesn't want to use the default
ddt numbering for a specific ddt.
    """,
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "license": "AGPL-3",
    "depends": [
        "l10n_it_delivery_note",
    ],
    "data": [
        "wizard/delivery_note_create.xml",
    ],
    "installable": True,
}
