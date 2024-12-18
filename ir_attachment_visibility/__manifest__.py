# Copyright 2020 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
{
    "name": "Attachment visibility",
    "version": "14.0.1.0.0",
    "category": "other",
    "author": "Sergio Corato",
    "summary": "Limit visibility of attachment to creator user, excluding erp manager.",
    "website": "https://github.com/efatto/efatto",
    "license": "AGPL-3",
    "depends": [
        "base",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/ir_attachment_security.xml",
    ],
    "installable": True,
}
