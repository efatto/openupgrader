# Copyright 2024 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Openupgrader",
    "version": "14.0.1.0.2",
    "category": "Odoo Management",
    "author": "Sergio Corato",
    "license": "AGPL-3",
    "summary": "Migrate Odoo.",
    "website": "https://github.com/efatto/openupgrader",
    "depends": [
        "auto_backup",
        "mail",
        "python_venv",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/openupgrader_migration_view.xml",
        "views/openupgrader_config_view.xml",
        "views/db_backup_view.xml",
    ],
    "installable": True,
    "external_dependencies": {
        "python": [
            "odoorpc",
            "PyYAML",
            "pysftp",
        ],
        "deb": [
            "libbz2-dev",
            "libcairo2-dev",
            "libgirepository1.0-dev",
            "liblzma-dev",
            "libncurses5-dev",
            "libreadline-dev",
            "libsqlite3-dev",
            "libzip-dev",
            "libzstd-dev",
            "lzma",
            "zstd",
        ],
    },
}
