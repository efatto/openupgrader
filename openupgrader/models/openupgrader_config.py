import base64
import logging

import yaml

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.release import version_info

logger = logging.getLogger(__name__)


class AutoInstallModule(models.Model):
    _name = "auto.install.module"
    _description = "AutoInstall Module"

    name = fields.Text(string="Technical Name of Installed Module")
    sequence = fields.Integer(string="SQL Sequence")
    openupgrade_config_id = fields.Many2one(
        comodel_name="openupgrader.config",
    )
    module_installed_id = fields.Many2one(
        comodel_name="ir.module.module",
        string="Module Installed (alternative of name)",
    )
    module_installed_name = fields.Char(
        related="module_installed_id.name", string="Module Installed Name"
    )
    module_to_install_name = fields.Text(
        string="Technical Name of Module To Install",
        required=True,
    )
    # todo if module_installed_id is set, compute name


class ModuleName(models.Model):
    _name = "module.name"
    _description = "Module name"

    name = fields.Text(string="Module Technical Name")


class PipRequirement(models.Model):
    _name = "pip.requirement"
    _description = "Pip requirement"

    name = fields.Text(string="Pip requirement")


class SqlUpdateCommand(models.Model):
    _name = "sql.update.command"
    _description = "SQL Update Command"
    _order = "sequence, id"

    name = fields.Text(string="SQL Command")
    sequence = fields.Integer(string="SQL Sequence")
    openupgrade_after_config_id = fields.Many2one(
        comodel_name="openupgrader.config",
    )
    openupgrade_before_config_id = fields.Many2one(
        comodel_name="openupgrader.config",
    )


class OpenupgraderConfig(models.Model):
    _name = "openupgrader.config"
    _description = "Openupgrader config"
    _rec_name = "odoo_version_id"

    odoo_version_id = fields.Many2one(
        comodel_name="odoo.version",
        string="Odoo version",
        default=lambda self: self.env["odoo.version"].search(
            [("name", "=", ".".join(str(v) for v in version_info[:2]))]
        ),
        copy=False,
    )
    config_file = fields.Binary(
        string="Config file (yml)",
    )
    config_file_name = fields.Char(
        string="Config file name",
    )
    sql_after_migration_command_ids = fields.One2many(
        comodel_name="sql.update.command",
        inverse_name="openupgrade_after_config_id",
        string="SQL after commands",
        copy=False,
    )
    sql_before_migration_command_ids = fields.One2many(
        comodel_name="sql.update.command",
        inverse_name="openupgrade_before_config_id",
        string="SQL before commands",
        copy=False,
    )
    module_auto_install_ids = fields.One2many(
        comodel_name="auto.install.module",
        inverse_name="openupgrade_config_id",
        string="Auto install modules",
        help="List of modules to install if there is another module installed",
        copy=False,
    )
    module_to_delete_after_migration_ids = fields.Many2many(
        comodel_name="module.name",
        relation="delete_module_rel",
        column1="delete_current_module_id",
        column2="delete_module_id",
        string="Modules to delete after migration",
        help="List of modules to delete",
        copy=False,
    )
    module_to_uninstall_after_migration_ids = fields.Many2many(
        comodel_name="module.name",
        relation="uninstall_after_module_rel",
        column1="uninstall_after_current_module_id",
        column2="uninstall_after_module_id",
        string="Module to uninstall after migration",
        copy=False,
    )
    module_to_uninstall_before_migration_ids = fields.Many2many(
        comodel_name="module.name",
        relation="uninstall_before_module_rel",
        column1="uninstall_before_current_module_id",
        column2="uninstall_before_module_id",
        string="Module to uninstall before migration",
        copy=False,
    )

    _sql_constraints = [
        (
            "version_unique",
            "unique(odoo_version_id)",
            _("This Odoo version already exists!"),
        )
    ]

    def button_load_config(self):
        version_name = self.odoo_version_id.name
        recipes = self.load_config_file()
        recipe_data = recipes[version_name]
        for recipe in recipe_data:
            if recipe.get("python_version"):
                self.odoo_version_id.python_version = recipe.get("python_version")
            if recipe.get("pip_requirements"):
                pip_requirements = recipe.get("pip_requirements")
                for pip_name in pip_requirements:
                    pip_id = self.env["pip.requirement"].search([
                        ("name", "=", pip_name)
                    ])
                    if not pip_id:
                        pip_id = self.env["pip.requirement"].create({"name": pip_name})
                    self.odoo_version_id.write({
                        "pip_requirement_ids": [
                            (4, pip_id.id)
                        ],
                    })
            if recipe.get("odoo"):
                odoo_id = self.env["remote.repo"].search([
                    ("name", "=", "odoo"),
                    ("remote_branch", "=",
                     recipe.get("odoo").split(" ")[1] or version_name),
                ])
                if not odoo_id:
                    odoo_id = self.env["remote.repo"].create({
                        "name": "odoo",
                        "remote_url": recipe.get("odoo").split(" ")[0],
                        "remote_branch": recipe.get("odoo").split(" ")[1] or version_name,
                        "is_odoo": True,
                    })
                self.odoo_version_id.odoo_repo_id = odoo_id
            if recipe.get("after_migration_to_this_version_sql_command"):
                after_migration_to_this_version_sql_command = recipe.get(
                    "after_migration_to_this_version_sql_command"
                )
                self.sql_after_migration_command_ids = [
                    (
                        0,
                        0,
                        {
                            "name": command,
                            "sequence": i,
                        },
                    )
                    for i, command in enumerate(
                        after_migration_to_this_version_sql_command
                    )
                    if command
                    not in self.sql_after_migration_command_ids.mapped("name")
                ]
            if recipe.get("before_migration_to_next_version_sql_command"):
                before_migration_to_next_version_sql_command = recipe.get(
                    "before_migration_to_next_version_sql_command"
                )
                self.sql_before_migration_command_ids = [
                    (
                        0,
                        0,
                        {
                            "name": command,
                            "sequence": i,
                        },
                    )
                    for i, command in enumerate(
                        before_migration_to_next_version_sql_command
                    )
                    if command
                    not in self.sql_before_migration_command_ids.mapped("name")
                ]
            if recipe.get("auto_install"):
                auto_install = recipe.get("auto_install")
                for i, module in enumerate(auto_install):
                    if all(
                        m.name != module.split()[0]
                        and m.module_to_install_name != module.split()[1]
                        for m in self.module_auto_install_ids
                    ):
                        self.module_auto_install_ids = [
                            (
                                0,
                                0,
                                {
                                    "name": module.split()[0],
                                    "sequence": i,
                                    "module_to_install_name": module.split()[1],
                                },
                            )
                        ]
            if recipe.get("delete"):
                delete = recipe.get("delete")
                self.module_to_delete_after_migration_ids = [
                    (
                        0,
                        0,
                        {
                            "name": module,
                        },
                    )
                    for module in delete
                    if module
                    not in self.module_to_delete_after_migration_ids.mapped("name")
                ]
            if recipe.get("uninstall_after_migration_to_this_version"):
                uninstall_after = recipe.get(
                    "uninstall_after_migration_to_this_version"
                )
                self.module_to_uninstall_after_migration_ids = [
                    (
                        0,
                        0,
                        {
                            "name": module,
                        },
                    )
                    for module in uninstall_after
                    if module
                    not in self.module_to_uninstall_after_migration_ids.mapped("name")
                ]
            if recipe.get("uninstall_before_migration_to_next_version"):
                uninstall_before = recipe.get(
                    "uninstall_before_migration_to_next_version"
                )
                self.module_to_uninstall_before_migration_ids = [
                    (
                        0,
                        0,
                        {
                            "name": module,
                        },
                    )
                    for module in uninstall_before
                    if module
                    not in self.module_to_uninstall_before_migration_ids.mapped("name")
                ]

    def load_config_file(self):
        if not self.config_file:
            raise UserError(_("Missing configuration file!"))
        file_content = base64.decodebytes(self.config_file)  # noqa
        repos = {}
        try:
            repos = yaml.safe_load(file_content) or {}
        except yaml.YAMLError as exc:
            logger.info(exc)
        return repos
