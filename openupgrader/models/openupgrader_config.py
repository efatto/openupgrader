import base64

import yaml
from odoo import fields, models, _, api
from odoo.exceptions import UserError
import logging
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
        related="module_installed_id.name",
        string="Module Installed Name"
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


class SqlUpdateCommand(models.Model):
    _name = "sql.update.command"
    _description = "SQL Update Command"

    name = fields.Text(string="SQL Command")
    sequence = fields.Integer(string="SQL Sequence")
    openupgrade_config_id = fields.Many2one(
        comodel_name="openupgrader.config",
    )


class OdooVersion(models.Model):
    _name = "odoo.version"
    _description = "Odoo Version"

    name = fields.Char(
        string="Odoo Version name",
        help="Like 12.0",
        required=True,
    )
    python_version = fields.Char(
        string="Python Version",
        help="Like 3.7",
        required=True,
    )
    odoo_is_openupgrade = fields.Boolean(
        string="Odoo is Openupgrade",
        compute="_compute_odoo_is_openupgrade",
        store=True,
    )

    @api.depends("name")
    def _compute_odoo_is_openupgrade(self):
        for record in self:
            if record.name in [
                '7.0', '8.0', '9.0', '10.0', '11.0', '12.0', '13.0'
            ]:
                record.odoo_is_openupgrade = True
            else:
                record.odoo_is_openupgrade = False


class OpenupgraderConfig(models.Model):
    _name = "openupgrader.config"
    _description = "Openupgrader config"
    _rec_name = "odoo_version_id"

    odoo_version_id = fields.Many2one(
        comodel_name="odoo.version",
        string="Odoo version",
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
        string="SQL update commands")
    module_auto_install_ids = fields.One2many(
        comodel_name="auto.install.module",
        inverse_name="openupgrade_config_id",
        string="Auto install modules",
        help="List of modules to install if there is another module installed",
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

    _sql_constraints = [(
        'version_unique',
        'unique(odoo_version_id)',
        _('This odoo version already exists!')
    )]

    def button_load_repos(self):
        op_repo_obj = self.env["openupgrader.repo"]
        odoo_version_obj = self.env["odoo.version"]
        version = self.odoo_version_id.name
        remotes, python_version = self.load_repos_file(version)
        odoo_version_id = odoo_version_obj.search([("name", "=", version)])
        if not odoo_version_id:
            odoo_version_obj.create([{
                "name": version,
                "python_version": python_version,
            }])
        op_repo = op_repo_obj.search([
            ("odoo_version_id", "=", odoo_version_id.id),
        ])
        if op_repo:
            remote_repo_names = op_repo.remote_repo_ids.mapped("name")
            op_repo.write({
                "remote_repo_ids": [
                    (0, 0, {
                        "name": remote,
                        "remote_url": remotes[remote].split(" ")[0],
                        "remote_branch": remotes[remote].split(" ")[1] or version,
                    }) for remote in remotes if remote not in remote_repo_names
                ]
            })
        else:
            op_repo_obj.create([{
                "odoo_version_id": odoo_version_id.id,
                "remote_repo_ids": [
                    (0, 0, {
                        "name": remote,
                        "remote_url": remotes[remote].split(" ")[0],
                        "remote_branch": remotes[remote].split(" ")[1] or version,
                    }) for remote in remotes
                ]
            }])

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
        python_version = False
        for repo in repos.get('repositories'):
            if repo.get('version') == version:
                remotes = repo.get('remotes')
                python_version = repo.get('python_version')
        return remotes, python_version

    def button_load_config(self):
        version = self.odoo_version_id.name
        receipts = self.load_config_file()
        receipt_data = receipts[version]
        for receipt in receipt_data:
            if receipt.get("sql_update_commands"):
                sql_update_commands = receipt.get("sql_update_commands")
                self.sql_update_command_ids = [
                    (0, 0, {
                        "name": sql_update_command,
                        "sequence": i,
                    })
                    for i, sql_update_command in enumerate(sql_update_commands)
                    if sql_update_command not in self.sql_update_command_ids
                    .mapped("name")
                ]
            if receipt.get("auto_install"):
                auto_install = receipt.get("auto_install")
                self.module_auto_install_ids = [
                    (0, 0, {
                        "name": module.split(" ")[0],
                        "sequence": i,
                        "module_to_install_name": module.split(" ")[1],
                    })
                    for i, module in enumerate(auto_install)
                    if module not in self.module_auto_install_ids.mapped("name")
                ]
            if receipt.get("delete"):
                delete = receipt.get("delete")
                self.module_to_delete_after_migration_ids = [
                    (0, 0, {
                        "name": module,
                    })
                    for module in delete
                    if module not in self.module_to_delete_after_migration_ids
                    .mapped("name")
                ]
            if receipt.get("uninstall_after_migration_to_this_version"):
                uninstall_after = receipt.get(
                    "uninstall_after_migration_to_this_version")
                self.module_to_uninstall_after_migration_ids = [
                    (0, 0, {
                        "name": module,
                    })
                    for module in uninstall_after
                    if module not in self.module_to_uninstall_after_migration_ids
                    .mapped("name")
                ]
            if receipt.get("uninstall_before_migration_to_next_version"):
                uninstall_before = receipt.get(
                    "uninstall_before_migration_to_next_version")
                self.module_to_uninstall_before_migration_ids = [
                    (0, 0, {
                        "name": module,
                    })
                    for module in uninstall_before
                    if module not in self.module_to_uninstall_before_migration_ids
                    .mapped("name")
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
