import base64
import configparser
import logging
import os
import shutil
import subprocess

import yaml

from odoo import _, api, fields, models, release
from odoo.exceptions import UserError, ValidationError

logger = logging.getLogger(__name__)


def _get_env_for_subprocess(folder, py_version):
    env_for_subprocess = os.environ.copy()
    env_folder = os.path.join(folder, ".venv")
    env_for_subprocess["VIRTUAL_ENV"] = env_folder
    env_for_subprocess["PYTHONPATH"] = os.path.join(env_folder, "bin", "python")
    # If there is a PIP_EXTRA_INDEX_URL in local env, put in the pyenv
    pip_extra_index_url = os.environ.get("PIP_EXTRA_INDEX_URL")
    if not pip_extra_index_url:
        pip_conf_path = os.path.join(os.path.expanduser("~"), ".pip", "pip.conf")
        if os.path.isfile(pip_conf_path):
            config = configparser.ConfigParser()
            config.read(pip_conf_path)
            pip_extra_index_url = config.get("global", "extra-index-url")
    if pip_extra_index_url:
        env_for_subprocess["UV_INDEX"] = pip_extra_index_url
    env_for_subprocess["PATH"] = ":".join(
        [
            env_folder,
            os.path.join(env_folder, "bin"),
            "/bin",
            "/usr/bin",
            os.path.join(os.path.expanduser("~"), ".local", "bin"),
        ]
    )
    env_for_subprocess["PWD"] = env_folder
    python_root = os.path.join(
        env_folder, "lib", f"python{'.'.join(py_version.split('.')[:2])}"
    )
    if os.path.isdir(python_root):
        env_for_subprocess["LIBRARY_ROOTS"] = python_root
    return env_for_subprocess


def _create_python_venv(venv_path, py_version):
    subprocess_env = _get_env_for_subprocess(venv_path, py_version)
    # Copy some pip configuration files that could exist in local to the python venv
    if not os.path.isdir(venv_path):
        subprocess.Popen([f"mkdir -p {venv_path}"], shell=True).wait()
    uv_path = os.path.join(os.path.expanduser("~"), ".local", "bin", "uv")
    if not os.path.isfile(uv_path):
        subprocess.Popen(
            "curl -LsSf https://astral.sh/uv/install.sh | sh",
            shell=True,
        ).wait()
    if not os.path.isfile(uv_path):
        raise ValidationError(_("uv is not installed, please install it first!"))
    if not os.path.isfile(os.path.join(venv_path, "pyproject.toml")):
        for command in [
            f"uv init --directory {venv_path} --python 'python=={py_version}'",
            f"uv venv --python {py_version}",
        ]:
            subprocess.Popen(
                command,
                shell=True,
                cwd=venv_path,
                env=subprocess_env,
            ).wait()
    return subprocess_env


class AutoInstallModule(models.Model):
    _name = "auto.install.module"
    _description = "AutoInstall Module"
    _order = "no_pip_found desc, name"

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
    no_pip_found = fields.Boolean()
    is_core_module = fields.Boolean()


class ModuleName(models.Model):
    _name = "module.name"
    _description = "Module name"
    _order = "no_pip_found desc, name"

    name = fields.Text(string="Module Technical Name", required=True)
    no_pip_found = fields.Boolean()
    is_core_module = fields.Boolean()

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
    _order = "no_pip_found desc, name"

    name = fields.Text(string="Pip requirement", required=True)
    no_pip_found = fields.Boolean()
    is_core_module = fields.Boolean()

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
    _description = "OpenUpgrader config"

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
        translate=False,
    )
    openupgrader_migration_id = fields.Many2one(
        comodel_name="openupgrader.migration",
        string="Odoo Migration",
        required=True,
    )
    python_version = fields.Char(required=True, default="3.7.16")
    odoo_is_openupgrade = fields.Boolean(
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
        string="Pip Requirements",
    )
    odoo_pip_requirement_ids = fields.Many2many(
        comodel_name="pip.requirement",
        relation="odoo_pip_requirement_rel",
        column1="config_id",
        column2="odoo_pip_requirement_id",
        string="Odoo Pip Requirements",
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
    config_file_name = fields.Char()
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

    def _set_modules_installability_via_pip(self, names):
        # set module not installable in all the possible origins:
        self.ensure_one()
        self.module_installed_ids.no_pip_found = False
        self.module_auto_install_ids.no_pip_found = False
        self.pip_requirement_ids.no_pip_found = False
        self.odoo_pip_requirement_ids.no_pip_found = False
        if names:
            self.module_installed_ids.filtered(
                lambda x: x.name in names
            ).no_pip_found = True
            self.module_auto_install_ids.filtered(
                lambda x: x.module_to_install_name in names
            ).no_pip_found = True
            self.pip_requirement_ids.filtered(
                lambda x: x.name in names
            ).no_pip_found = True
            self.odoo_pip_requirement_ids.filtered(
                lambda x: x.name in names
            ).no_pip_found = True

    def _set_core_modules(self, names):
        self.ensure_one()
        self.module_installed_ids.is_core_module = False
        self.module_installed_ids.filtered(
            lambda x: x.name in names
        ).is_core_module = True
        self.module_auto_install_ids.is_core_module = False
        self.module_auto_install_ids.filtered(
            lambda x: x.module_to_install_name in names
        ).is_core_module = True
        self.pip_requirement_ids.is_core_module = False
        self.pip_requirement_ids.filtered(
            lambda x: x.name in names
        ).is_core_module = True
        self.odoo_pip_requirement_ids.is_core_module = False
        self.odoo_pip_requirement_ids.filtered(
            lambda x: x.name in names
        ).is_core_module = True

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

    def _search_create_module(self, module_name):
        module_id = self.env["module.name"].search(
            [
                ("name", "=", module_name),
            ]
        )
        if not module_id:
            module_id = self.env["module.name"].create({"name": module_name})
            # ensure data is committed to db to avoid duplication
            module_id.flush_model()
        return module_id

    @api.depends(
        "name", "module_auto_install_ids", "module_to_uninstall_before_migration_ids"
    )
    def _compute_module_installed_ids(self):
        for record in self:
            if record.name:
                # get current installed modules
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
                        module_id = self._search_create_module(module_name)
                        module_installed_ids |= module_id
                if record.name == release.version:
                    new_module_installed_ids = module_installed_ids
                else:
                    new_module_installed = []
                    new_module_installed_ids = self.env["module.name"]
                    # add new modules to install
                    for module_auto_install in record.module_auto_install_ids:
                        if module_auto_install.name in module_installed_ids.mapped(
                            "name"
                        ):
                            new_module_installed.append(
                                module_auto_install.module_to_install_name
                            )
                    # add current modules except modules to uninstall before migration
                    for module in module_installed_ids.mapped("name"):
                        if (
                            module
                            not in record.module_to_uninstall_before_migration_ids.mapped(
                                "name"
                            )
                        ):
                            new_module_installed.append(module)
                    # todo exclude modules uninstalled in the previous version
                    for module_name in new_module_installed:
                        module_id = self._search_create_module(module_name)
                        new_module_installed_ids |= module_id
                record.module_installed_ids = new_module_installed_ids
            else:
                record.module_installed_ids = False

    def button_recreate_venv(self):
        # Remove the folder and re-create a clean virtual environment
        self.ensure_one()
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
                    shell=True,
                ).wait()
            else:
                subprocess.Popen(
                    [
                        f"git fetch origin {self.name} "
                        f"&& git reset --hard origin/{self.name}",
                    ],
                    cwd=openupgrade_path,
                    shell=True,
                ).wait()

            if not odoo_is_openupgrade:
                # install odoo repo separately
                openupgrader_migration_id.install_repo(
                    self.odoo_repo_id,
                    self.odoo_repo_id.remote_branch or self.name,
                    odoo_path,
                )
            subprocess.Popen(
                "uv pip install packaging",
                cwd=venv_path,
                shell=True,
                env=subprocess_env,
            ).wait()
            uv_override_deps = []
            if self.name in ["14.0", "15.0", "16.0"]:
                uv_override_deps.append("XlsxWriter==3.2.9")
            if self.name in ["16.0", "17.0", "18.0"]:
                uv_override_deps.extend(
                    [
                        "Werkzeug==2.0.2",
                        "lxml==4.9.3",
                        "gevent==22.10.2",
                        "greenlet==2.0.2",
                        "docutils==0.18.1",
                    ]
                )
            if uv_override_deps:
                logger.info("Fixing libraries mismatch in Odoo requirements.txt")
                with open(os.path.join(venv_path, "pyproject.toml"), "a") as f:
                    f.write("[tool.uv]\n")
                    f.write(f"override-dependencies = {str(uv_override_deps)} ")
                    f.close()
            commands = [
                "uv pip install '%s'" % name
                for name in self.pip_requirement_ids.mapped("name")
            ]
            if odoo_is_openupgrade:
                for c in [
                    f"uv pip install -r {odoo_path}/requirements.txt",
                    f"cd {openupgrade_path} && uv pip install -e . ",
                ]:
                    commands.append(c)
            else:
                for c in [
                    f"uv pip install -r {openupgrade_path}/requirements.txt",
                    f"uv pip install -r {odoo_path}/requirements.txt",
                    f"cd {odoo_path} && uv pip install -e . ",
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
                    shell=True,
                    env=subprocess_env,
                ).wait()

            odoo_modules_to_install_via_pip = [
                name
                for name in self.module_installed_ids.filtered(
                    lambda x: not os.path.isdir(os.path.join(odoo_addons_path, x.name))
                    and not x.name == "base"
                    and not x.name.startswith("test_")
                ).mapped("name")
            ]
            names = [
                name
                for name in self.module_installed_ids.mapped("name")
                if name not in odoo_modules_to_install_via_pip
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
            self._set_core_modules(names)
            openupgrader_migration_id.install_pip_modules(
                self, odoo_modules_to_install_via_pip
            )

    def button_load_config(self):  # noqa: C901
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
                        pip_id.flush_model()
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
                        pip_id.flush_model()
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
                    odoo_id.flush_model()
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
                        module_id.flush_model()
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
                        module_id.flush_model()
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
                        module_id.flush_model()
                    self.module_to_uninstall_before_migration_ids |= module_id

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
