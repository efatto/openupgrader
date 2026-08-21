import base64
import configparser
import importlib.util
import logging
import os
import shutil
import subprocess

import yaml

from odoo import _, api, fields, models, release
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

from .tools import (
    _set_odoorc,
    _update_migration_state_file,
)

logger = logging.getLogger(__name__)


def _get_env_for_subprocess(folder, py_version):
    env_for_subprocess = os.environ.copy()
    env_folder = os.path.join(folder, ".venv")
    env_for_subprocess["VIRTUAL_ENV"] = env_folder
    env_for_subprocess["UV_PROJECT_ENVIRONMENT"] = env_folder
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
    commands = []
    if not os.path.isfile(os.path.join(venv_path, "pyproject.toml")):
        commands.append(
            f"uv init --directory {venv_path} --python 'python=={py_version}'"
        )
    if not os.path.isfile(os.path.join(venv_path, ".venv", "bin")):
        commands.append(f"uv venv --python {py_version}")
    if commands:
        for command in commands:
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
    _order = "name"

    name = fields.Text(string="Technical Name of Installed Module", required=True)
    sequence = fields.Integer(string="SQL Sequence")
    auto_openupgrade_config_id = fields.Many2one(
        comodel_name="openupgrader.config",
    )
    rename_openupgrade_config_id = fields.Many2one(
        comodel_name="openupgrader.config",
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
    _order = "name"

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


class PythonUpdateCommand(models.Model):
    _name = "python.update.command"
    _description = "Python Update Command"
    _order = "sequence, id"

    name = fields.Text(string="Python Command", required=True)
    sequence = fields.Integer(string="Python Sequence")
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
            ("19.0", "19.0"),
        ],
        required=True,
        translate=False,
    )
    openupgrader_migration_id = fields.Many2one(
        comodel_name="openupgrader.migration",
        string="Odoo Migration",
        required=True,
        ondelete="cascade",
        default=lambda self: self.env["openupgrader.migration"].search([], limit=1),
    )
    python_version = fields.Char(required=True, default="3.7.16")
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
        string="Modules to be installed",
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
    python_after_migration_command_ids = fields.One2many(
        comodel_name="python.update.command",
        inverse_name="openupgrade_after_config_id",
        string="Python after commands",
        copy=False,
    )
    python_before_migration_command_ids = fields.One2many(
        comodel_name="python.update.command",
        inverse_name="openupgrade_before_config_id",
        string="Python before commands",
        copy=False,
    )
    module_auto_install_ids = fields.One2many(
        comodel_name="auto.install.module",
        inverse_name="auto_openupgrade_config_id",
        string="Auto installed modules after migration",
        help="List of modules to install if there is another module installed.\n"
        "This list is auto filled with modules from openupgrade apriori.py and "
        "custom list in openupgrader yml configuration file if uploaded.",
        copy=False,
    )
    renamed_module_ids = fields.One2many(
        comodel_name="auto.install.module",
        inverse_name="rename_openupgrade_config_id",
        string="Renamed modules",
        help="List of modules to rename if there is another module installed.\n"
        "This list is auto filled with modules from openupgrade apriori.py and "
        "custom list in openupgrader yml configuration file if uploaded.",
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
    obsolete_modules = fields.Text()
    core_modules = fields.Text()
    not_autoinstallable_modules = fields.Text()

    @staticmethod
    def _extract_package_name(requirement):
        return (
            requirement.split("==")[0]
            .split(">")[0]
            .split("<")[0]
            .split("~")[0]
            .split("!")[0]
            .split(";")[0]
            .split("[")[0]
            .split("@")[0]
            .strip()
            .lower()
        )

    def get_migration_state_from_file(self):
        return self.openupgrader_migration_id.get_migration_state_from_file([self.name])

    def update_migration_state_file(self, state, date_started=None):
        file_path = self.openupgrader_migration_id._default_migration_state_path()
        _update_migration_state_file(
            file_path,
            self.name,
            state,
            date_started,
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

    def _search_create_module(self, module_name):
        module_id = self.env["module.name"].search(
            [
                ("name", "=", module_name),
            ]
        )
        if not module_id:
            module_id = self.env["module.name"].create({"name": module_name})
            # ensure data is committed to db to avoid duplication
            module_id.flush()
        return module_id

    def _get_core_modules(self, odoo_path):
        self.ensure_one()
        odoo_addons_path = os.path.join(
            odoo_path,
            "addons",
        )
        res = [
            "base",
        ]
        if os.path.isdir(odoo_addons_path):
            for module in os.listdir(odoo_addons_path):
                if os.path.isfile(
                    os.path.join(odoo_addons_path, module, "__manifest__.py")
                ):
                    res.append(module)
        return res

    @api.depends(
        "name", "module_auto_install_ids", "module_to_uninstall_before_migration_ids"
    )
    def _compute_module_installed_ids(self):
        for config in self:
            if config.name:
                if config.name == release.version:
                    # get current installed modules as this is the initial version
                    # n.b. we can go through all the dependencies of the installed
                    # modules as it is the working instance
                    installed_modules = self.env["ir.module.module"].search(
                        [
                            ("state", "in", ["installed", "to upgrade"]),
                        ]
                    )
                    new_module_installed_ids = self.env["module.name"]
                    for module in installed_modules:
                        module_names = module.mapped("name")
                        if module.dependencies_id:
                            module_names += module.dependencies_id.mapped(
                                "depend_id.name"
                            )
                        for module_name in module_names:
                            module_id = self._search_create_module(module_name)
                            new_module_installed_ids |= module_id
                else:
                    new_module_installed = []
                    previous_config = self.search(
                        [("name", "=", str(float(config.name) - 1))]
                    )
                    new_module_installed_ids = previous_config.module_installed_ids
                    # Add modules renamed or auto-installed in previous version
                    for renamed_module in previous_config.renamed_module_ids:
                        if renamed_module.name in new_module_installed_ids.mapped(
                            "name"
                        ):
                            new_module_installed_ids |= self._search_create_module(
                                renamed_module.module_to_install_name
                            )
                    for auto_install_module in previous_config.module_auto_install_ids:
                        if auto_install_module.name in new_module_installed_ids.mapped(
                            "name"
                        ):
                            new_module_installed_ids |= self._search_create_module(
                                auto_install_module.module_to_install_name
                            )
                    new_module_installed_ids -= (
                        previous_config.module_to_uninstall_after_migration_ids
                    )
                    new_module_installed_ids -= (
                        previous_config.module_to_delete_after_migration_ids
                    )
                    new_module_installed_ids -= (
                        previous_config.module_to_uninstall_before_migration_ids
                    )
                    new_module_installed_ids = new_module_installed_ids.filtered(
                        lambda m, pc=previous_config: m.name
                        not in safe_eval(pc.obsolete_modules)
                    )
                    # add new modules to install
                    for module_auto_install in config.module_auto_install_ids:
                        if module_auto_install.name in new_module_installed_ids.mapped(
                            "name"
                        ):
                            new_module_installed.append(
                                module_auto_install.module_to_install_name
                            )
                    # add current modules except modules to uninstall before migration
                    for module in new_module_installed_ids.mapped("name"):
                        if (
                            module
                            not in config.module_to_uninstall_before_migration_ids.mapped(  # noqa
                                "name"
                            )
                        ):
                            new_module_installed.append(module)
                    for module_name in new_module_installed:
                        module_id = self._search_create_module(module_name)
                        new_module_installed_ids |= module_id
                new_module_installed_ids -= (
                    config.module_to_uninstall_after_migration_ids
                )
                new_module_installed_ids -= config.module_to_delete_after_migration_ids
                new_module_installed_ids -= (
                    config.module_to_uninstall_before_migration_ids
                )
                config.module_installed_ids = new_module_installed_ids
            else:
                config.module_installed_ids = False

    def button_recreate_venv(self):
        # Remove the folder and re-create a clean virtual environment
        self.ensure_one()
        venv_folder = os.path.join(
            self.openupgrader_migration_id.folder, f"openupgrade{self.name}"
        )
        if os.path.isdir(venv_folder):
            shutil.rmtree(venv_folder, ignore_errors=True)
        self.button_create_venv()

    def button_create_venv(self):
        self.ensure_one()
        openupgrader_migration = self.openupgrader_migration_id
        odoo_is_openupgrade = self.odoo_is_openupgrade
        # Odoo is OpenUpgrade until v. 13.0, from v. 14.0 Odoo is in ./<version/odoo
        # install odoo Openupgrade repo, from v. 14.0 it contains only migration script
        if openupgrader_migration:
            venv_path = os.path.join(
                openupgrader_migration.folder, f"openupgrade{self.name}"
            )
            subprocess_env = _create_python_venv(venv_path, self.python_version)
            _set_odoorc(venv_path, self)
            openupgrade_path = os.path.join(venv_path, "odoo")
            odoo_path = (
                openupgrade_path
                if odoo_is_openupgrade
                else (os.path.join(venv_path, "repos", "odoo"))
            )
            if not os.path.isdir(openupgrade_path):
                subprocess.Popen(
                    [
                        f"git clone --single-branch --depth 1 -b {self.name} "
                        f"{openupgrader_migration.openupgrade_repo} odoo"
                    ],
                    cwd=venv_path,
                    shell=True,
                ).wait()
            else:
                subprocess.Popen(
                    [f"git pull origin {self.name} --rebase"],
                    cwd=openupgrade_path,
                    shell=True,
                ).wait()

            if not odoo_is_openupgrade:
                # install odoo repo separately
                openupgrader_migration.install_repo(
                    self.odoo_repo_id,
                    self.odoo_repo_id.remote_branch or self.name,
                    odoo_path,
                )
            pip_requirements = self.pip_requirement_ids.mapped("name") or []
            uv_override_deps = []
            # we need to keep the name of the package to avoid duplicates
            # from odoo_requirements_file
            pip_requirements_names = [
                self._extract_package_name(r) for r in pip_requirements
            ]
            odoo_requirements_file = os.path.join(odoo_path, "requirements.txt")
            if os.path.isfile(odoo_requirements_file):
                with open(odoo_requirements_file, "r") as req_file:
                    for line in req_file:
                        line = line.split("#")[0].strip()
                        if not line:
                            continue
                        # check if the package is already in pip_requirements
                        package_name = self._extract_package_name(line)
                        if package_name not in pip_requirements_names:
                            uv_override_deps.append(line)
            uv_override_deps.extend(pip_requirements)

            if uv_override_deps:
                if (
                    "tool.uv"
                    not in open(os.path.join(venv_path, "pyproject.toml")).read()
                ):
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
                    f"uv pip install -e {openupgrade_path}",
                ]:
                    commands.append(c)
            else:
                for c in [
                    f"uv pip install -r {openupgrade_path}/requirements.txt",
                    f"uv pip install -r {odoo_path}/requirements.txt",
                    f"uv pip install -e {odoo_path}",
                ]:
                    commands.append(c)

            # exclude odoo core modules
            for command in commands:
                subprocess.Popen(
                    command,
                    cwd=venv_path,
                    shell=True,
                    env=subprocess_env,
                ).wait()

            core_modules = self._get_core_modules(odoo_path)
            self.core_modules = str(set(core_modules))
            odoo_modules_to_install_via_pip = [
                name
                for name in self.module_installed_ids.mapped("name")
                if name not in core_modules
            ]
            if self.odoo_pip_requirement_ids:
                odoo_modules_to_install_via_pip.extend(
                    self.odoo_pip_requirement_ids.mapped("name")
                )
            if self.module_auto_install_ids:
                odoo_modules_to_install_via_pip += [
                    auto_install.module_to_install_name
                    for auto_install in self.module_auto_install_ids
                    if auto_install.name in self.module_installed_ids.mapped("name")
                ]
            openupgrader_migration.install_pip_modules(
                self, odoo_modules_to_install_via_pip
            )

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        res.action_load_config()
        return res

    def action_load_config(self):  # noqa: C901
        version_name = self.name
        recipes = self.load_config_file()
        if recipes:
            # reset all existing configurations
            self.pip_requirement_ids = False
            self.odoo_pip_requirement_ids = False
            self.sql_after_migration_command_ids = False
            self.sql_before_migration_command_ids = False
            self.python_after_migration_command_ids = False
            self.python_before_migration_command_ids = False
            self.module_auto_install_ids = False
            self.renamed_module_ids = False
            self.module_to_delete_after_migration_ids = False
            self.module_to_uninstall_after_migration_ids = False
            self.module_to_uninstall_before_migration_ids = False
            recipe_data = recipes[version_name]
        else:
            # add only apriori_py data to existing records
            recipe_data = [
                {
                    "auto_install": [],
                    "renamed_modules": [],
                }
            ]
        apriori_py_path = os.path.join(
            self.openupgrader_migration_id.folder,
            f"openupgrade{self.name}",
            "odoo",
            "openupgrade_scripts",
            "apriori.py",
        )
        if os.path.isfile(apriori_py_path):
            # append auto_install modules from OpenUpgrade apriori.py
            spec = importlib.util.spec_from_file_location("apriori", apriori_py_path)
            if spec and spec.loader:
                apriori_mod = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(apriori_mod)
                except Exception as e:
                    logger.error(
                        "Error importing apriori.py from %s: %s", apriori_py_path, e
                    )
                    apriori_mod = None

                if apriori_mod:
                    renamed_modules = getattr(apriori_mod, "renamed_modules", {})
                    if renamed_modules:
                        for renamed_module, target_module in renamed_modules.items():
                            if isinstance(recipe_data, list):
                                renamed_modules_found = False
                                obsolete_found = False
                                for recipe in recipe_data:
                                    if "renamed_modules" in recipe.keys():
                                        if not isinstance(
                                            recipe["renamed_modules"], list
                                        ):
                                            recipe["renamed_modules"] = []
                                        recipe["renamed_modules"].append(
                                            f"{renamed_module} {target_module}"
                                        )
                                        renamed_modules_found = True
                                    if "obsolete" in recipe.keys():
                                        if not isinstance(recipe["obsolete"], list):
                                            recipe["obsolete"] = []
                                        recipe["obsolete"].append(renamed_module)
                                        obsolete_found = True
                                if not renamed_modules_found:
                                    recipe_data.append(
                                        {
                                            "renamed_modules": [
                                                f"{renamed_module} {target_module}"
                                            ],
                                        }
                                    )
                                if not obsolete_found:
                                    recipe_data.append(
                                        {
                                            "obsolete": [renamed_module],
                                        }
                                    )
                            else:
                                recipe_data.append(
                                    {
                                        "renamed_modules": [
                                            f"{renamed_module} {target_module}"
                                        ],
                                        "obsolete": [renamed_module],
                                    }
                                )
                    merged_modules = getattr(apriori_mod, "merged_modules", {})
                    if merged_modules:
                        for merged_module, target_module in merged_modules.items():
                            if isinstance(recipe_data, list):
                                auto_install_found = False
                                obsolete_found = False
                                for recipe in recipe_data:
                                    if "auto_install" in recipe.keys():
                                        if not isinstance(recipe["auto_install"], list):
                                            recipe["auto_install"] = []
                                        recipe["auto_install"].append(
                                            f"{merged_module} {target_module}"
                                        )
                                        auto_install_found = True
                                    if "obsolete" in recipe.keys():
                                        if not isinstance(recipe["obsolete"], list):
                                            recipe["obsolete"] = []
                                        recipe["obsolete"].append(merged_module)
                                        obsolete_found = True
                                if not auto_install_found:
                                    recipe_data.append(
                                        {
                                            "auto_install": [
                                                f"{merged_module} {target_module}"
                                            ]
                                        }
                                    )
                                if not obsolete_found:
                                    recipe_data.append(
                                        {
                                            "obsolete": [merged_module],
                                        }
                                    )
                            else:
                                recipe_data.append(
                                    {
                                        "auto_install": [
                                            f"{merged_module} {target_module}"
                                        ],
                                        "obsolete": [merged_module],
                                    }
                                )
        if isinstance(recipe_data, dict):
            recipe_data = [recipe_data]
        for recipe in recipe_data:
            if recipe.get("python_version"):
                self.python_version = recipe.get("python_version")
            if recipe.get("pip_requirements"):
                pip_requirements = recipe.get("pip_requirements")
                for pip_name in pip_requirements:
                    pip_id = self.env["pip.requirement"].search(
                        [("name", "=", pip_name)]
                    )
                    if not pip_id:
                        pip_id = self.env["pip.requirement"].create({"name": pip_name})
                        pip_id.flush()
                    self.pip_requirement_ids |= pip_id
            if recipe.get("odoo_pip_requirements"):
                odoo_pip_requirements = recipe.get("odoo_pip_requirements")
                for pip_name in odoo_pip_requirements:
                    pip_id = self.env["pip.requirement"].search(
                        [("name", "=", pip_name)]
                    )
                    if not pip_id:
                        pip_id = self.env["pip.requirement"].create({"name": pip_name})
                        pip_id.flush()
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
                    odoo_id.flush()
                self.odoo_repo_id = odoo_id
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
            if recipe.get("after_migration_to_this_version_python_command"):
                after_migration_to_this_version_python_command = recipe.get(
                    "after_migration_to_this_version_python_command"
                )
                self.python_after_migration_command_ids = [
                    (
                        0,
                        0,
                        {
                            "name": command,
                            "sequence": i,
                        },
                    )
                    for i, command in enumerate(
                        after_migration_to_this_version_python_command
                    )
                    if command
                    not in self.python_after_migration_command_ids.mapped("name")
                ]
            if recipe.get("before_migration_to_next_version_python_command"):
                before_migration_to_next_version_python_command = recipe.get(
                    "before_migration_to_next_version_python_command"
                )
                self.python_before_migration_command_ids = [
                    (
                        0,
                        0,
                        {
                            "name": command,
                            "sequence": i,
                        },
                    )
                    for i, command in enumerate(
                        before_migration_to_next_version_python_command
                    )
                    if command
                    not in self.python_before_migration_command_ids.mapped("name")
                ]
            if recipe.get("renamed_modules"):
                renamed_modules = recipe.get("renamed_modules")
                for i, module in enumerate(renamed_modules):
                    module_name = module.split()[0]
                    module_to_install_name = module.split()[1]
                    if not module_name or not module_to_install_name:
                        continue
                    if all(
                        m.name != module_name
                        and m.module_to_install_name != module_to_install_name
                        for m in self.renamed_module_ids
                    ):
                        self.renamed_module_ids = [
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
            if recipe.get("auto_install"):
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
                modules_to_delete = recipe.get("delete")
                for module in modules_to_delete:
                    module_id = self.env["module.name"].search(
                        [
                            ("name", "=", module),
                        ]
                    )
                    if not module_id:
                        module_id = self.env["module.name"].create({"name": module})
                        module_id.flush()
                    self.module_to_delete_after_migration_ids |= module_id
            if recipe.get("obsolete"):
                obsolete = recipe.get("obsolete")
                self.obsolete_modules = str(set(obsolete))
            if recipe.get("not_autoinstallable_modules"):
                not_autoinstallable_modules = recipe.get("not_autoinstallable_modules")
                self.not_autoinstallable_modules = str(set(not_autoinstallable_modules))
            if recipe.get("uninstall_after_migration_to_this_version"):
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
                        module_id.flush()
                    self.module_to_uninstall_after_migration_ids |= module_id
            if recipe.get("uninstall_before_migration_to_next_version"):
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
                        module_id.flush()
                    self.module_to_uninstall_before_migration_ids |= module_id

    def load_config_file(self):
        if self.openupgrader_migration_id.config_file:
            file_content = base64.decodebytes(
                self.openupgrader_migration_id.config_file
            )  # noqa
            repos = {}
            try:
                repos = yaml.safe_load(file_content) or {}
            except yaml.YAMLError as exc:
                logger.info(exc)
            return repos
        return None
