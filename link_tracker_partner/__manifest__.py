# Copyright 2025 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
{
    "name": "Link tracker show partners",
    "summary": "Add ability to open partners created with a link tracker",
    "version": "14.0.1.0.0",
    "category": "Sales",
    "website": "https://github.com/efatto/efatto",
    "author": "Sergio Corato",
    "maintainers": ["sergiocorato"],
    "license": "AGPL-3",
    "installable": True,
    "depends": [
        "link_tracker",
        "marketing_crm_partner",
        "mass_mailing",
    ],
    "data": [
        "views/link_tracker.xml",
    ],
}
