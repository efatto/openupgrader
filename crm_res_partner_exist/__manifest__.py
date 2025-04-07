# Copyright 2025 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
{
    "name": "CRM lead with existing partner",
    "summary": "Add ability to filter crm lead with existing but not linked partner",
    "version": "14.0.1.0.0",
    "category": "Sales",
    "website": "https://github.com/efatto/efatto",
    "author": "Sergio Corato",
    "maintainers": ["sergiocorato"],
    "license": "AGPL-3",
    "installable": True,
    "depends": [
        "crm",
    ],
    "data": [
        "views/crm_lead.xml",
    ],
}
