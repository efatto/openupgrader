# Copyright 2025 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "OpenUpgrader extra method to migrate l10n_it_ddt",
    "version": "12.0.1.0.1",
    "category": "other",
    "author": "Sergio Corato",
    "license": "AGPL-3",
    "summary": "Add method to migrate l10n_it_ddt to l10n_it_delivery_note",
    "website": "https://github.com/efatto/openupgrader",
    "depends": ["openupgrader"],
    "data": ["views/openupgrader_migration_view.xml"],
    "installable": True,
    "auto_install": False,
}
