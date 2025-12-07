import base64
import logging
import os
import shutil
import subprocess

import yaml
from odoo import _, api, fields, models
from odoo.addons.python_venv.python_venv import _create_python_venv
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)


class AutoInstallModule(models.Model):
    _name = "auto.install.module"
    _description = "AutoInstall Module"

    name = fields.Text(string="Technical Name of Installed Module", required=True)
    sequence = fields.Integer(string="SQL Sequence")
    openupgrade_config_id = fields.Many2one(
        comodel_name="openupgrader.config",
        ondelete="cascade",
    )
    module_to_install_name = fields.Text(
        string="Technical Name of Module To Install",
        required=True,
    )


class ModuleName(models.Model):
    _name = "module.name"
    _description = "Module name"
    _order = "name"

    name = fields.Text(string="Module Technical Name", required=True)

    _sql_constraints = [
        (
            "name_unique",
            "unique(name)",
            "This module already exists!",
        ),
    ]


class PipRequirement(models.Model):
    _name = "pip.requirement"
    _description = "Pip requirement"

    name = fields.Text(string="Pip requirement", required=True)

    _sql_constraints = [
        (
            "name_unique",
            "unique(name)",
            "This module already exists!",
        ),
    ]


class SqlUpdateCommand(models.Model):
    _name = "sql.update.command"
    _description = "SQL Update Command"
    _order = "sequence, id"

    name = fields.Text(string="SQL Command", required=True)
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

    name = fields.Selection(
        string="Odoo Version name",
        selection=[
            ("7.0", "7.0"),
            ("8.0", "8.0"),
            ("9.0", "9.0"),
            ("10.0", "10.0"),
            ("11.0", "11.0"),
            ("12.0", "12.0"),
            ("13.0", "13.0"),
            ("14.0", "14.0"),
            ("15.0", "15.0"),
            ("16.0", "16.0"),
            ("17.0", "17.0"),
            ("18.0", "18.0"),
        ],
        required=True,
    )
    openupgrader_migration_id = fields.Many2one(
        comodel_name="openupgrader.migration",
        string="Odoo Migration",
        required=True,
    )
    python_version = fields.Char(
        string="Python Version", required=True, default="3.7.16"
    )
    odoo_is_openupgrade = fields.Boolean(
        string="Odoo is Openupgrade",
        compute="_compute_odoo_is_openupgrade",
        store=True,
    )
    module_installed_ids = fields.Many2many(
        comodel_name="module.name",
        relation="installed_module_rel",
        column1="config_id",
        column2="installed_module_id",
        string="Modules installed in current instance",
        compute="_compute_module_installed_ids",
        copy=False,
        store=True,
        readonly=False,
    )
    odoo_repo_id = fields.Many2one(
        comodel_name="remote.repo",
        string="Odoo Repository",
        domain=[("is_odoo", "=", True)],
    )
    pip_requirement_ids = fields.Many2many(
        comodel_name="pip.requirement",
        relation="pip_requirement_rel",
        column1="config_id",
        column2="pip_requirement_id",
        string="Pip requirements",
    )
    odoo_pip_requirement_ids = fields.Many2many(
        comodel_name="pip.requirement",
        relation="odoo_pip_requirement_rel",
        column1="config_id",
        column2="odoo_pip_requirement_id",
        string="Odoo Pip requirements",
        help="Extra Odoo modules to be installed via pip",
        ondelete="cascade",
        copy=False,
    )
    db_backup_id = fields.Many2one(
        comodel_name="db.backup",
        string="Database Backup",
        domain=[("is_migration_backup", "=", True)],
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
        ondelete="cascade",
        copy=False,
    )
    module_to_uninstall_after_migration_ids = fields.Many2many(
        comodel_name="module.name",
        relation="uninstall_after_module_rel",
        column1="uninstall_after_current_module_id",
        column2="uninstall_after_module_id",
        string="Module to uninstall after migration",
        ondelete="cascade",
        copy=False,
    )
    module_to_uninstall_before_migration_ids = fields.Many2many(
        comodel_name="module.name",
        relation="uninstall_before_module_rel",
        column1="uninstall_before_current_module_id",
        column2="uninstall_before_module_id",
        string="Module to uninstall before migration",
        ondelete="cascade",
        copy=False,
    )

    def _create_db_backup(self, folder):
        self.ensure_one()
        if not self.db_backup_id:
            db_backup_id = self.env["db.backup"].search(
                [
                    ("openupgrader_config_id", "=", self.id),
                    ("is_migration_backup", "=", True),
                ]
            )
            if not db_backup_id:
                db_backup_id = self.env["db.backup"].create(
                    {
                        "openupgrader_config_id": self.id,
                        "is_migration_backup": True,
                        "folder": os.path.join(folder, f"openupgrade{self.name}"),
                        "days_to_keep": 1,
                        "method": "local",
                        "backup_format": "zip",
                    }
                )
            self.db_backup_id = db_backup_id

    _sql_constraints = [
        (
            "version_unique",
            "unique(name)",
            "This Odoo version already exists!",
        ),
        (
            "db_backup_unique",
            "unique(db_backup_id)",
            "This Odoo version already has a backup!",
        ),
    ]

    @api.depends("name")
    def _compute_odoo_is_openupgrade(self):
        for record in self:
            if float(record.name) < 14:
                record.odoo_is_openupgrade = True
            else:
                record.odoo_is_openupgrade = False

    # todo get pip requirements from installed modules
    @api.depends("name", "openupgrader_migration_id.from_version_id")
    def _compute_module_installed_ids(self):
        for record in self:
            if record.name and record.openupgrader_migration_id.from_version_id:
                installed_modules = self.env["ir.module.module"].search(
                    [
                        ("state", "in", ["installed", "to upgrade"]),
                    ]
                )
                module_installed_ids = self.env["module.name"]
                for module in installed_modules:
                    module_names = module.mapped("name")
                    if module.dependencies_id:
                        module_names += module.dependencies_id.mapped("depend_id.name")
                    for module_name in module_names:
                        module_id = self.env["module.name"].search(
                            [
                                ("name", "=", module_name),
                            ]
                        )
                        if not module_id:
                            module_id = self.env["module.name"].create(
                                {"name": module_name}
                            )
                            # ensure data is committed to db to avoid duplication
                            self.env.cr.commit()  # noqa
                        module_installed_ids |= module_id
                record.module_installed_ids = module_installed_ids
            else:
                record.module_installed_ids = False

    def button_recreate_venv(self):
        # Remove the folder and re-create a clean virtual environment
        self.ensure_one()
        self._compute_module_installed_ids()
        openupgrader_migration_id = self.env["openupgrader.migration"].search([])
        openupgrader_migration_id.ensure_one()
        venv_folder = os.path.join(
            openupgrader_migration_id.folder, f"openupgrade{self.name}"
        )
        if os.path.isdir(venv_folder):
            shutil.rmtree(venv_folder, ignore_errors=True)
        self.button_create_venv()

    def button_create_venv(self):
        self.ensure_one()
        self._compute_module_installed_ids()
        openupgrader_migration_id = self.env["openupgrader.migration"].search([])
        openupgrader_migration_id.ensure_one()
        odoo_is_openupgrade = self.odoo_is_openupgrade
        # Odoo is OpenUpgrade until v. 13.0, from v. 14.0 Odoo is in ./<version/odoo
        # install odoo Openupgrade repo, from v. 14.0 it contains only migration script
        if openupgrader_migration_id:
            venv_path = os.path.join(
                openupgrader_migration_id.folder, f"openupgrade{self.name}"
            )
            subprocess_env = _create_python_venv(venv_path, self.python_version)
            openupgrade_path = os.path.join(venv_path, "odoo")
            odoo_path = (
                openupgrade_path
                if odoo_is_openupgrade
                else (os.path.join(venv_path, "repos", "odoo"))
            )
            if not os.path.isdir(openupgrade_path):
                subprocess.Popen(
                    [
                        f"git clone --single-branch "
                        f"{openupgrader_migration_id.openupgrade_repo} "
                        f"-b {self.name} --depth 1 odoo "
                    ],
                    cwd=venv_path,
                    env=subprocess_env,  # forse qui non serve
                    shell=True,
                ).wait()
            else:
                subprocess.Popen(
                    [
                        "git pull --rebase",
                    ],
                    cwd=openupgrade_path,
                    env=subprocess_env,  # forse qui non serve
                    shell=True,
                ).wait()

            if not odoo_is_openupgrade:
                # install odoo repo separately
                openupgrader_migration_id.install_repo(
                    self.odoo_repo_id,
                    self.odoo_repo_id.remote_branch or self.name,
                    odoo_path,
                )
            if self.name == "16.0":  # ugly and temp fix for mismatch with py3.10.6
                for command in [
                    "sed -i 's/gevent==21.8.0/gevent==22.10.2/g' "
                    f"{odoo_path}/requirements.txt",
                    "sed -i 's/greenlet==1.1.2/greenlet==2.0.2/g' "
                    f"{odoo_path}/requirements.txt",
                ]:
                    subprocess.Popen(
                        command,
                        cwd=venv_path,
                        env=subprocess_env,
                        shell=True,
                    )
            commands = [
                "pip install --no-cache-dir '%s'" % name
                for name in self.pip_requirement_ids.mapped("name")
            ]
            if odoo_is_openupgrade:
                for c in [
                    f"cd {openupgrade_path} && pip install -e . ",
                    f"pip install --no-cache-dir -r {odoo_path}/requirements.txt",
                ]:
                    commands.append(c)
            else:
                for c in [
                    f"cd {odoo_path} && pip install -e . ",
                    f"pip install --no-cache-dir -r {openupgrade_path}/requirements.txt",
                    f"pip install --no-cache-dir -r {odoo_path}/requirements.txt",
                ]:
                    commands.append(c)
            # exclude odoo core modules
            odoo_addons_path = os.path.join(
                odoo_path,
                "addons",
            )
            for command in commands:
                subprocess.Popen(
                    command,
                    cwd=venv_path,
                    env=subprocess_env,
                    shell=True,
                ).wait()
            odoo_modules_to_install_via_pip = [
                name
                for name in self.module_installed_ids.filtered(
                    lambda x: not os.path.isdir(os.path.join(odoo_addons_path, x.name))
                    and not x.name == "base"
                ).mapped("name")
            ]
            if self.odoo_pip_requirement_ids:
                odoo_modules_to_install_via_pip += [
                    name for name in self.odoo_pip_requirement_ids.mapped("name")
                ]
            if self.module_auto_install_ids:
                odoo_modules_to_install_via_pip += [
                    auto_install.module_to_install_name
                    for auto_install in self.module_auto_install_ids
                    if auto_install.name in self.module_installed_ids.mapped("name")
                ]
            openupgrader_migration_id.install_pip_modules(
                self, odoo_modules_to_install_via_pip
            )

    def button_load_config(self):
        self._compute_module_installed_ids()
        version_name = self.name
        recipes = self.load_config_file()
        recipe_data = recipes[version_name]
        for recipe in recipe_data:
            if recipe.get("python_version"):
                self.python_version = recipe.get("python_version")
            if recipe.get("pip_requirements"):
                self.pip_requirement_ids = False
                pip_requirements = recipe.get("pip_requirements")
                for pip_name in pip_requirements:
                    pip_id = self.env["pip.requirement"].search(
                        [("name", "=", pip_name)]
                    )
                    if not pip_id:
                        pip_id = self.env["pip.requirement"].create({"name": pip_name})
                        self.env.cr.commit()
                    self.pip_requirement_ids |= pip_id
            if recipe.get("odoo_pip_requirements"):
                self.odoo_pip_requirement_ids = False
                odoo_pip_requirements = recipe.get("odoo_pip_requirements")
                for pip_name in odoo_pip_requirements:
                    pip_id = self.env["pip.requirement"].search(
                        [("name", "=", pip_name)]
                    )
                    if not pip_id:
                        pip_id = self.env["pip.requirement"].create({"name": pip_name})
                        self.env.cr.commit()
                    self.odoo_pip_requirement_ids |= pip_id
            if recipe.get("odoo"):
                odoo_id = self.env["remote.repo"].search(
                    [
                        ("name", "=", "odoo"),
                        (
                            "remote_branch",
                            "=",
                            recipe.get("odoo").split(" ")[1] or version_name,
                        ),
                    ]
                )
                if not odoo_id:
                    odoo_id = self.env["remote.repo"].create(
                        {
                            "name": "odoo",
                            "remote_url": recipe.get("odoo").split(" ")[0],
                            "remote_branch": recipe.get("odoo").split(" ")[1]
                            or version_name,
                            "is_odoo": True,
                        }
                    )
                    self.env.cr.commit()
                self.odoo_repo_id = odoo_id
            if recipe.get("after_migration_to_this_version_sql_command"):
                self.sql_after_migration_command_ids = False
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
                self.sql_before_migration_command_ids = False
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
                self.module_auto_install_ids = False
                auto_install = recipe.get("auto_install")
                for i, module in enumerate(auto_install):
                    module_name = module.split()[0]
                    module_to_install_name = module.split()[1]
                    if not module_name or not module_to_install_name:
                        continue
                    if all(
                        m.name != module_name
                        and m.module_to_install_name != module_to_install_name
                        for m in self.module_auto_install_ids
                    ):
                        self.module_auto_install_ids = [
                            (
                                0,
                                0,
                                {
                                    "name": module_name,
                                    "sequence": i,
                                    "module_to_install_name": module_to_install_name,
                                },
                            )
                        ]
            if recipe.get("delete"):
                self.module_to_delete_after_migration_ids = False
                delete = recipe.get("delete")
                for module in delete:
                    module_id = self.env["module.name"].search(
                        [
                            ("name", "=", module),
                        ]
                    )
                    if not module_id:
                        module_id = self.env["module.name"].create({"name": module})
                        self.env.cr.commit()
                    self.module_to_delete_after_migration_ids |= module_id
            if recipe.get("uninstall_after_migration_to_this_version"):
                self.module_to_uninstall_after_migration_ids = False
                uninstall_after = recipe.get(
                    "uninstall_after_migration_to_this_version"
                )
                for module in uninstall_after:
                    module_id = self.env["module.name"].search(
                        [
                            ("name", "=", module),
                        ]
                    )
                    if not module_id:
                        module_id = self.env["module.name"].create({"name": module})
                        self.env.cr.commit()
                    self.module_to_uninstall_after_migration_ids |= module_id
            if recipe.get("uninstall_before_migration_to_next_version"):
                self.module_to_uninstall_before_migration_ids = False
                uninstall_before = recipe.get(
                    "uninstall_before_migration_to_next_version"
                )
                for module in uninstall_before:
                    module_id = self.env["module.name"].search(
                        [
                            ("name", "=", module),
                        ]
                    )
                    if not module_id:
                        module_id = self.env["module.name"].create({"name": module})
                        self.env.cr.commit()
                    self.module_to_uninstall_before_migration_ids |= module_id

    def load_config_file(self):
        if not self.config_file:
            raise UserError(_("Missing configuration file!"))
        self._compute_module_installed_ids()
        file_content = base64.decodebytes(self.config_file)  # noqa
        repos = {}
        try:
            repos = yaml.safe_load(file_content) or {}
        except yaml.YAMLError as exc:
            logger.info(exc)
        return repos
