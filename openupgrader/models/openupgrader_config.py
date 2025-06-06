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
    openupgrade_config_id = fields.Many2one(
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
    repos_file = fields.Binary(
        string="Repos file (yml)",
    )
    repos_file_name = fields.Char(
        string="Repos file name",
    )
    sql_update_command_ids = fields.One2many(
        comodel_name="sql.update.command",
        inverse_name="openupgrade_config_id",
        string="SQL update commands",
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
    )
    module_to_uninstall_after_migration_ids = fields.Many2many(
        comodel_name="module.name",
        relation="uninstall_after_module_rel",
        column1="uninstall_after_current_module_id",
        column2="uninstall_after_module_id",
        string="Module to uninstall after migration",
    )
    module_to_uninstall_before_migration_ids = fields.Many2many(
        comodel_name="module.name",
        relation="uninstall_before_module_rel",
        column1="uninstall_before_current_module_id",
        column2="uninstall_before_module_id",
        string="Module to uninstall before migration",
    )

    _sql_constraints = [
        (
            "version_unique",
            "unique(odoo_version_id)",
            _("This odoo version already exists!"),
        )
    ]

    def button_load_repos(self):
        op_repo_obj = self.env["openupgrader.repo"]
        odoo_version_obj = self.env["odoo.version"]
        version = self.odoo_version_id.name
        remotes, pip_names, python_version = self.load_repos_file(version)
        odoo_version_id = odoo_version_obj.search([("name", "=", version)])
        if not odoo_version_id:
            odoo_version_obj.create(
                [
                    {
                        "name": version,
                        "python_version": python_version,
                    }
                ]
            )
        else:
            odoo_version_id.python_version = python_version
        op_repo = op_repo_obj.search(
            [
                ("odoo_version_id", "=", odoo_version_id.id),
            ]
        )
        if not op_repo:
            op_repo = op_repo_obj.create(
                [
                    {
                        "odoo_version_id": odoo_version_id.id,
                    }
                ]
            )
        remote_repo_names = op_repo.remote_repo_ids.mapped("name")
        pip_requirements = op_repo.pip_requirement_ids.mapped("name")
        for remote in remotes:
            if remote not in remote_repo_names:
                op_repo.write(
                    {
                        "remote_repo_ids": [
                            (
                                0,
                                0,
                                {
                                    "name": remote,
                                    "remote_url": remotes[remote].split(" ")[0],
                                    "remote_branch": remotes[remote].split(" ")[1]
                                    or version,
                                    "is_odoo": remote == "odoo",
                                },
                            )
                        ],
                    }
                )
        for pip_name in pip_names:
            if pip_name not in pip_requirements:
                op_repo.write(
                    {
                        "pip_requirement_ids": [
                            (
                                0,
                                0,
                                {
                                    "name": pip_name,
                                },
                            )
                        ],
                    }
                )

    def load_repos_file(self, version):
        if not self.repos_file:
            raise UserError(_("Missing repos file!"))
        file_content = base64.decodebytes(self.repos_file)  # noqa
        repos = {}
        try:
            repos = yaml.safe_load(file_content) or {}
        except yaml.YAMLError as exc:
            logger.info(exc)
        remotes = {}
        pip_names = []
        python_version = False
        for repo in repos.get("repositories"):
            if repo.get("version") == version:
                remotes = repo.get("remotes")
                pip_names = repo.get("pip_requirements")
                python_version = repo.get("python_version")
        return remotes, pip_names, python_version

    def button_load_config(self):
        version = self.odoo_version_id.name
        recipes = self.load_config_file()
        recipe_data = recipes[version]
        for recipe in recipe_data:
            if recipe.get("sql_update_commands"):
                sql_update_commands = recipe.get("sql_update_commands")
                self.sql_update_command_ids = [
                    (
                        0,
                        0,
                        {
                            "name": sql_update_command,
                            "sequence": i,
                        },
                    )
                    for i, sql_update_command in enumerate(sql_update_commands)
                    if sql_update_command
                    not in self.sql_update_command_ids.mapped("name")
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
