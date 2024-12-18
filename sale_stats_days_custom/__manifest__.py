# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Custom days for sale button stats",
    "summary": "Show stats with custom days period",
    "version": "14.0.1.0.0",
    "development_status": "Alpha",
    "category": "Sales",
    "website": "https://github.com/efatto/efatto",
    "author": "Sergio Corato",
    "maintainers": ["sergiocorato"],
    "license": "AGPL-3",
    "application": False,
    "installable": True,
    "depends": [
        "sale",
        "sales_team",
    ],
    "data": [
        "views/res_config_settings_view.xml",
    ],
}
