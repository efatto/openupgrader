import base64
import configparser
import glob
import importlib.util
import json
import logging
import os
import re
import shutil
from subprocess import PIPE, Popen, run

import yaml

from odoo import _, api, fields, models, release
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

from .tools import (
    _check_oca_authorship,
    _get_migration_retry_number_from_migration_state_file,
    _get_retry_number_from_migration_state_file,
    _set_odoorc,
    _update_migration_state_file,
)

logger = logging.getLogger(__name__)


def _get_pip_extra_index_url():
    """Return the private pip index URL from the env or from pip.conf."""
    pip_extra_index_url = os.environ.get("PIP_EXTRA_INDEX_URL")
    if not pip_extra_index_url:
        pip_conf_path = os.path.join(os.path.expanduser("~"), ".pip", "pip.conf")
        if os.path.isfile(pip_conf_path):
            config = configparser.ConfigParser()
            config.read(pip_conf_path)
            if config.has_option("global", "extra-index-url"):
                pip_extra_index_url = config.get("global", "extra-index-url")
    return pip_extra_index_url


def _get_env_for_subprocess(folder, py_version):
    env_for_subprocess = os.environ.copy()
    env_folder = os.path.join(folder, ".venv")
    env_for_subprocess["VIRTUAL_ENV"] = env_folder
    env_for_subprocess["UV_PROJECT_ENVIRONMENT"] = env_folder
    env_for_subprocess["PYTHONPATH"] = os.path.join(env_folder, "bin", "python")
    # If there is a PIP_EXTRA_INDEX_URL in local env, put in the pyenv
    pip_extra_index_url = _get_pip_extra_index_url()
    if pip_extra_index_url:
        env_for_subprocess["UV_INDEX"] = pip_extra_index_url
        env_for_subprocess["PIP_EXTRA_INDEX_URL"] = pip_extra_index_url
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


def _is_legacy_python_version(py_version):
    """Return True for Python versions that uv can not manage (<= 3.7).

    Odoo 12 runs on Python 3.7 and uv does not provide managed Python 3.7
    interpreters, so the environment of that version must be created with
    pyenv/venv and installed with pip instead.
    """
    try:
        major, minor = [int(part) for part in str(py_version).split(".")[:2]]
    except (TypeError, ValueError):
        return False
    return (major, minor) < (3, 8)


def _create_legacy_python_venv(venv_path, py_version):
    """Create a Python <= 3.7 virtualenv without uv.

    ``uv`` only supports managed Python 3.8+.  The Odoo 12 environment is
    created with ``pyenv`` (installing the requested interpreter when it is
    missing) and the standard ``venv`` module, and it is installed with pip.
    """
    subprocess_env = _get_env_for_subprocess(venv_path, py_version)
    env_folder = os.path.join(venv_path, ".venv")
    python_bin = os.path.join(env_folder, "bin", "python")
    if os.path.isfile(python_bin):
        return subprocess_env
    os.makedirs(venv_path, exist_ok=True)
    short_version = ".".join(str(py_version).split(".")[:2])
    pyenv_root = os.path.join(os.path.expanduser("~"), ".pyenv")
    pyenv_bin = os.path.join(pyenv_root, "bin", "pyenv")
    pyenv_python = os.path.join(pyenv_root, "versions", py_version, "bin", "python")
    python_executable = next(
        (
            executable
            for executable in (
                pyenv_python,
                shutil.which(f"python{py_version}"),
                shutil.which(f"python{short_version}"),
            )
            if executable and os.path.isfile(executable)
        ),
        None,
    )
    if not python_executable:
        if not os.path.isfile(pyenv_bin):
            run(
                "curl -fsSL https://pyenv.run | bash",
                shell=True,
                env=subprocess_env,
            )
        run(
            f"{pyenv_bin} install -s {py_version}",
            shell=True,
            env=subprocess_env,
        )
        python_executable = pyenv_python
    if not python_executable:
        raise ValidationError(
            _(
                "Python %s is required for Odoo 12 but it is not available. "
                "Install it with pyenv or make it available in PATH."
            )
            % py_version
        )
    process = run(
        f"{python_executable} -m venv --clear {env_folder}",
        shell=True,
        env=subprocess_env,
    )
    if process.returncode != 0 or not os.path.isfile(python_bin):
        raise ValidationError(
            _("Unable to create the Python %(version)s virtualenv in %(path)s.")
            % {"version": py_version, "path": venv_path}
        )
    # pip >= 24.1 dropped Python 3.7 support and setuptools >= 70 can break
    # Odoo 12 requirements, so keep compatible versions in that environment.
    run(
        f"{python_bin} -m pip install --upgrade 'pip<24.1' 'setuptools<70' wheel",
        shell=True,
        env=subprocess_env,
    )
    # Recompute the environment now that ``.venv`` exists, so that
    # LIBRARY_ROOTS and the venv paths point to the actual interpreter.
    return _get_env_for_subprocess(venv_path, py_version)


def _create_python_venv(venv_path, py_version):
    if _is_legacy_python_version(py_version):
        return _create_legacy_python_venv(venv_path, py_version)
    subprocess_env = _get_env_for_subprocess(venv_path, py_version)
    # Copy some pip configuration files that could exist in local to the python venv
    if not os.path.isdir(venv_path):
        run([f"mkdir -p {venv_path}"], shell=True)
    uv_path = os.path.join(os.path.expanduser("~"), ".local", "bin", "uv")
    if not os.path.isfile(uv_path):
        run(
            "curl -LsSf https://astral.sh/uv/install.sh | sh",
            shell=True,
        )
    if not os.path.isfile(uv_path):
        raise ValidationError(_("uv is not installed, please install it first!"))
    commands = []
    pyprojecttoml_path = os.path.join(venv_path, "pyproject.toml")
    if not os.path.isfile(pyprojecttoml_path):
        commands.append(
            f"uv init --directory {venv_path} --python 'python=={py_version}'"
        )
    if not os.path.isfile(os.path.join(venv_path, ".venv", "bin")):
        commands.append(f"uv venv --clear --python {py_version}")
    if commands:
        for command in commands:
            run(
                command,
                shell=True,
                cwd=venv_path,
                env=subprocess_env,
            )
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
    core_modules = fields.Text()
    not_autoinstallable_modules = fields.Text()
    env_state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("updating", "Updating"),
            ("updated", "Updated"),
        ],
        default="draft",
    )
    env_update_date = fields.Datetime()

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

    @staticmethod
    def _toml_string(value):
        """Encode a value as a TOML basic string (JSON escaping is valid)."""
        return json.dumps(value)

    @staticmethod
    def _read_requirements_file(requirements_path):
        """Return the package requirement lines of a requirements file."""
        requirements = []
        if not requirements_path or not os.path.isfile(requirements_path):
            return requirements
        with open(requirements_path, "r") as req_file:
            for line in req_file:
                line = line.split("#")[0].strip()
                # keep package requirements only, skip pip options (--index-url,
                # -r, ...)
                if not line or line.startswith("-"):
                    continue
                requirements.append(line)
        return requirements

    def _get_addon_requirements_path(self, venv_path):
        return os.path.join(venv_path, ".openupgrader_addons.txt")

    def _get_addon_requirements(self, venv_path):
        """Return the pip requirements of the addons installed in the env."""
        path = self._get_addon_requirements_path(venv_path)
        if not os.path.isfile(path):
            return []
        with open(path, "r") as addon_file:
            return [line.strip() for line in addon_file if line.strip()]

    def _record_addon_requirements(self, venv_path, requirements):
        """Persist addon requirements so they survive a plain ``uv sync``."""
        existing = set(self._get_addon_requirements(venv_path))
        new_requirements = [
            requirement
            for requirement in requirements
            if requirement and requirement not in existing
        ]
        if not new_requirements:
            return
        with open(self._get_addon_requirements_path(venv_path), "a") as addon_file:
            addon_file.write("\n".join(new_requirements) + "\n")

    def _install_legacy_environment(
        self, venv_path, openupgrade_path, odoo_path, subprocess_env
    ):
        """Install a Python <= 3.7 environment with pip instead of uv."""
        pip = os.path.join(venv_path, ".venv", "bin", "pip")
        commands = []
        for requirement in self.pip_requirement_ids.mapped("name"):
            commands.append(
                f"{pip} install --no-cache-dir --pre --upgrade {requirement}"
            )
        requirement_paths = {
            requirement_path
            for requirement_path in (
                os.path.join(openupgrade_path, "requirements.txt"),
                os.path.join(odoo_path, "requirements.txt"),
            )
            if os.path.isfile(requirement_path)
        }
        for requirement_path in sorted(requirement_paths):
            commands.append(f"{pip} install --no-cache-dir -r {requirement_path}")
        if self.odoo_is_openupgrade:
            commands.append(
                f"cd {openupgrade_path} && {pip} install --no-cache-dir -e ."
            )
        else:
            commands.append(
                f"cd {odoo_path} && {pip} install --no-cache-dir -e ."
            )
            openupgrade_requirements = os.path.join(
                openupgrade_path, "requirements.txt"
            )
            if os.path.isfile(openupgrade_requirements):
                commands.append(
                    f"{pip} install --no-cache-dir -r {openupgrade_requirements}"
                )
        for command in commands:
            logger.info("Running legacy environment command: %s", command)
            process = run(command, cwd=venv_path, shell=True, env=subprocess_env)
            if process.returncode != 0:
                logger.warning("Command failed in legacy environment: %s", command)

    def _write_uv_pyproject(self, venv_path, odoo_path):
        """Write a complete ``pyproject.toml`` so ``uv sync`` can manage the env.

        ``uv init`` only creates a stub project without dependencies, so a
        plain ``uv sync`` would remove every installed package. The project is
        made self contained by declaring the OpenUpgrade/Odoo requirements, the
        configured pip requirements, the addons installed in the environment
        and Odoo itself (from its local checkout) as dependencies.
        """
        openupgrade_path = os.path.join(venv_path, "odoo")
        openupgrade_requirements = self._read_requirements_file(
            os.path.join(openupgrade_path, "requirements.txt")
        )
        odoo_requirements = self._read_requirements_file(
            os.path.join(odoo_path, "requirements.txt")
        )
        pip_requirements = self.pip_requirement_ids.mapped("name") or []
        # keep the first occurrence of a requirement and let the explicitly
        # configured pip requirements win over the requirements files
        seen_requirements = set()
        dependencies = []
        for requirement in (
            pip_requirements
            + openupgrade_requirements
            + odoo_requirements
            + self._get_installed_addon_requirements(venv_path)
            + self._get_addon_requirements(venv_path)
        ):
            package_name = self._extract_package_name(requirement)
            if package_name and package_name not in seen_requirements:
                seen_requirements.add(package_name)
                dependencies.append(requirement)
        # Odoo is installed in editable mode from its local checkout: it is not
        # published on PyPI, so its version can not be resolved from an index
        dependencies.append("odoo")
        # Override the versions coming from the requirements files to avoid
        # libraries mismatch, the same way the old uv pip install did.
        pip_requirements_names = {
            self._extract_package_name(requirement) for requirement in pip_requirements
        }
        override_dependencies = [
            requirement
            for requirement in odoo_requirements
            if self._extract_package_name(requirement) not in pip_requirements_names
        ]
        override_dependencies.extend(pip_requirements)
        lines = [
            "[project]",
            "name = %s" % self._toml_string("openupgrade%s" % self.name),
            'version = "0.1.0"',
            "requires-python = %s" % self._toml_string(">=%s" % self.python_version),
            "dependencies = [",
        ]
        for dependency in dependencies:
            lines.append("    %s," % self._toml_string(dependency))
        lines.append("]")
        lines.append("")
        lines.append("[tool.uv]")
        # the project itself is not a package, only its dependencies matter
        lines.append("package = false")
        if override_dependencies:
            lines.append("override-dependencies = [")
            for dependency in override_dependencies:
                lines.append("    %s," % self._toml_string(dependency))
            lines.append("]")
        if _get_pip_extra_index_url():
            # combine the private index and PyPI in a single resolution, as
            # done by uv pip compile
            lines.append('index-strategy = "unsafe-best-match"')
        lines.append("")
        lines.append("[tool.uv.sources]")
        lines.append(
            "odoo = { path = %s, editable = true }"
            % self._toml_string(os.path.relpath(odoo_path, venv_path))
        )
        with open(os.path.join(venv_path, "pyproject.toml"), "w") as pyproject_file:
            pyproject_file.write("\n".join(lines) + "\n")
        return dependencies

    def _sync_uv_env(self, venv_path, subprocess_env=None):
        """Create/update the virtualenv from the project ``pyproject.toml``."""
        if subprocess_env is None:
            subprocess_env = _get_env_for_subprocess(venv_path, self.python_version)
        process = run(
            "uv sync",
            cwd=venv_path,
            shell=True,
            env=subprocess_env,
        )
        if process.returncode != 0:
            logger.warning("uv sync failed in %s", venv_path)
        return process

    def get_migration_state_from_file(self):
        return self.openupgrader_migration_id.get_migration_state_from_file([self.name])

    def update_migration_state_file(
        self,
        state=None,
        env_state=None,
        env_update_date=None,
        date_started=None,
        uninstall_modules_retry_number=None,
        migration_retry_number=None,
    ):
        file_path = self.openupgrader_migration_id._default_migration_state_path()
        _update_migration_state_file(
            file_path,
            self.name,
            state=state,
            date_started=date_started,
            env_state=env_state,
            env_update_date=env_update_date,
            uninstall_modules_retry_number=uninstall_modules_retry_number,
            migration_retry_number=migration_retry_number,
        )

    def get_uninstall_modules_retry_number(self):
        """Get the number of retries done to uninstall pending modules."""
        file_path = self.openupgrader_migration_id._default_migration_state_path()
        return _get_retry_number_from_migration_state_file(file_path, self.name)

    def increment_uninstall_modules_retry_number(self):
        """Increment and persist the retry number for pending modules removal."""
        uninstall_modules_retry_number = self.get_uninstall_modules_retry_number() + 1
        self.update_migration_state_file(
            uninstall_modules_retry_number=uninstall_modules_retry_number
        )
        return uninstall_modules_retry_number

    def reset_uninstall_modules_retry_number(self):
        """Reset the retry number for pending modules removal."""
        self.update_migration_state_file(uninstall_modules_retry_number=0)

    def get_migration_retry_number(self):
        """Get the number of retries done for a failed migration step."""
        file_path = self.openupgrader_migration_id._default_migration_state_path()
        return _get_migration_retry_number_from_migration_state_file(
            file_path, self.name
        )

    def increment_migration_retry_number(self):
        """Increment and persist the retry number of a failed migration step.

        The counter is bounded by the auto-migration cron so that a
        deterministic failure does not loop forever.
        """
        migration_retry_number = self.get_migration_retry_number() + 1
        self.update_migration_state_file(migration_retry_number=migration_retry_number)
        return migration_retry_number

    def reset_migration_retry_number(self):
        """Reset the retry number of a failed migration step."""
        self.update_migration_state_file(migration_retry_number=0)

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
            module_id.env.cr.commit()
        return module_id

    def _get_core_modules(self, odoo_path=None):
        """Return the modules shipped with the target Odoo version.

        When ``odoo_path`` is not provided, resolve the path of the target
        Odoo checkout. If it is not available, fall back to the stored
        ``core_modules`` field, which is filled when the environment is
        created.
        """
        self.ensure_one()
        if odoo_path is None:
            venv_path = os.path.join(
                self.openupgrader_migration_id.folder, f"openupgrade{self.name}"
            )
            odoo_path = (
                os.path.join(venv_path, "odoo")
                if self.odoo_is_openupgrade
                else os.path.join(venv_path, "repos", "odoo")
            )
            if not os.path.isdir(os.path.join(odoo_path, "addons")):
                if self.core_modules:
                    return set(safe_eval(self.core_modules))
                return set()
        odoo_addons_path = os.path.join(odoo_path, "addons")
        res = {"base"}
        if os.path.isdir(odoo_addons_path):
            for module in os.listdir(odoo_addons_path):
                if os.path.isfile(
                    os.path.join(odoo_addons_path, module, "__manifest__.py")
                ):
                    res.add(module)
        return res

    @api.depends(
        "name",
        "module_auto_install_ids",
        "renamed_module_ids",
        "module_to_uninstall_before_migration_ids",
        "module_to_uninstall_after_migration_ids",
        "module_to_delete_after_migration_ids",
    )
    def _compute_module_installed_ids(self):  # noqa: C901
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
                    # add new modules to install
                    for module_auto_install in config.module_auto_install_ids:
                        if module_auto_install.name in new_module_installed_ids.mapped(
                            "name"
                        ):
                            new_module_installed.append(
                                module_auto_install.module_to_install_name
                            )
                    # add new modules to install
                    for renamed_module in config.renamed_module_ids:
                        if renamed_module.name in new_module_installed_ids.mapped(
                            "name"
                        ):
                            new_module_installed.append(
                                renamed_module.module_to_install_name
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

    def button_update_env(self):
        self.ensure_one()
        self.env_state = "updating"
        self.update_migration_state_file(
            env_state="updating",
        )
        self.button_create_env()

    def button_recreate_env(self):
        self.ensure_one()
        self.button_delete_env()
        self.button_create_env()

    def button_delete_env(self):
        # Remove the folder and re-create a clean virtual environment
        self.ensure_one()
        venv_folder = os.path.join(
            self.openupgrader_migration_id.folder, f"openupgrade{self.name}"
        )
        if os.path.isdir(venv_folder):
            shutil.rmtree(venv_folder, ignore_errors=True)
            self.env_state = "draft"
            self.update_migration_state_file(
                env_state="draft",
            )

    def _get_odoo_executable(self, venv_path):
        """Return the path of the Odoo server executable for this version.

        It mirrors the path used when starting the environment, so that an
        environment can be validated before it is marked as ``updated``.
        """
        version_float = float(self.name)
        if version_float < 10:
            return os.path.join(venv_path, "odoo", "openerp-server")
        if version_float < 14:
            return os.path.join(venv_path, "odoo", "odoo-bin")
        return os.path.join(venv_path, "repos", "odoo", "odoo-bin")

    def button_create_env(self):  # noqa: C901
        self.ensure_one()
        _migration_state, env_state, _version = self.get_migration_state_from_file()
        if env_state == "updated" or self.env_state == "updated":
            return
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
                run(
                    [
                        f"git clone --single-branch --depth 1 -b {self.name} "
                        f"{openupgrader_migration.openupgrade_repo} odoo"
                    ],
                    cwd=venv_path,
                    shell=True,
                )
            else:
                run(
                    [f"git pull origin {self.name} --rebase"],
                    cwd=openupgrade_path,
                    shell=True,
                )

            if not odoo_is_openupgrade:
                # install odoo repo separately
                openupgrader_migration.install_repo(
                    self.odoo_repo_id,
                    self.odoo_repo_id.remote_branch or self.name,
                    odoo_path,
                )
            legacy_python = _is_legacy_python_version(self.python_version)
            if legacy_python:
                # uv does not support Python 3.7: Odoo 12 environments are
                # created and installed with pyenv/venv and pip.
                self._install_legacy_environment(
                    venv_path, openupgrade_path, odoo_path, subprocess_env
                )
            else:
                # Turn the environment into a proper uv project (dependencies,
                # local Odoo source and version overrides) and install it with
                # ``uv sync`` so that running ``uv sync`` again keeps it coherent.
                self._write_uv_pyproject(venv_path, odoo_path)
                self._sync_uv_env(venv_path, subprocess_env)

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
            if self.renamed_module_ids:
                odoo_modules_to_install_via_pip += [
                    renamed_module.module_to_install_name
                    for renamed_module in self.renamed_module_ids
                    if renamed_module.name in self.module_installed_ids.mapped("name")
                ]
            self.install_pip_modules(odoo_modules_to_install_via_pip)
            if legacy_python:
                sync_process = None
            else:
                # Include the addons installed with uv pip in the project and
                # refresh the environment so a plain ``uv sync`` keeps them.
                sync_process = self._sync_uv_env(venv_path, subprocess_env)
            # Never mark a broken environment as updated: a missing Odoo
            # executable or a failed ``uv sync`` must surface immediately
            # instead of failing later when the migration is started.
            odoo_executable = self._get_odoo_executable(venv_path)
            if (
                (sync_process and sync_process.returncode != 0)
                or not os.path.isfile(odoo_executable)
            ):
                if sync_process:
                    error_message = _(
                        "Environment for version %(version)s is not usable: "
                        "'uv sync' exited with code %(returncode)s and the Odoo "
                        "executable '%(executable)s' %(state)s."
                    ) % {
                        "version": self.name,
                        "returncode": sync_process.returncode,
                        "executable": odoo_executable,
                        "state": _("is missing")
                        if not os.path.isfile(odoo_executable)
                        else _("exists"),
                    }
                else:
                    error_message = _(
                        "Environment for version %(version)s is not usable: "
                        "the Odoo executable '%(executable)s' is missing."
                    ) % {
                        "version": self.name,
                        "executable": odoo_executable,
                    }
                raise ValidationError(error_message)
            env_update_date = fields.Datetime.now()
            self.write(
                {
                    "env_state": "updated",
                    "env_update_date": env_update_date,
                }
            )
            self.update_migration_state_file(
                env_state="updated",
                env_update_date=env_update_date.strftime("%Y-%m-%d %H:%M:%S"),
            )

    def button_sync_env(self):
        """Synchronize the virtualenv with the project dependencies.

        Runs ``uv sync`` against the environment created by
        :meth:`button_create_env`, so that the installed packages match the
        project ``pyproject.toml``.
        """
        self.ensure_one()
        venv_path = os.path.join(
            self.openupgrader_migration_id.folder, f"openupgrade{self.name}"
        )
        if not os.path.isdir(os.path.join(venv_path, ".venv", "bin")):
            # no environment yet, create it from scratch
            self.button_create_env()
            return
        subprocess_env = _get_env_for_subprocess(venv_path, self.python_version)
        openupgrade_path = os.path.join(venv_path, "odoo")
        odoo_path = (
            openupgrade_path
            if self.odoo_is_openupgrade
            else os.path.join(venv_path, "repos", "odoo")
        )
        if _is_legacy_python_version(self.python_version):
            self._install_legacy_environment(
                venv_path, openupgrade_path, odoo_path, subprocess_env
            )
            return
        self._write_uv_pyproject(venv_path, odoo_path)
        self._sync_uv_env(venv_path, subprocess_env)

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
                                if not renamed_modules_found:
                                    recipe_data.append(
                                        {
                                            "renamed_modules": [
                                                f"{renamed_module} {target_module}"
                                            ],
                                        }
                                    )
                            else:
                                recipe_data.append(
                                    {
                                        "renamed_modules": [
                                            f"{renamed_module} {target_module}"
                                        ],
                                    }
                                )
                    merged_modules = getattr(apriori_mod, "merged_modules", {})
                    if merged_modules:
                        for merged_module, target_module in merged_modules.items():
                            if isinstance(recipe_data, list):
                                auto_install_found = False
                                for recipe in recipe_data:
                                    if "auto_install" in recipe.keys():
                                        if not isinstance(recipe["auto_install"], list):
                                            recipe["auto_install"] = []
                                        recipe["auto_install"].append(
                                            f"{merged_module} {target_module}"
                                        )
                                        auto_install_found = True
                                if not auto_install_found:
                                    recipe_data.append(
                                        {
                                            "auto_install": [
                                                f"{merged_module} {target_module}"
                                            ]
                                        }
                                    )
                            else:
                                recipe_data.append(
                                    {
                                        "auto_install": [
                                            f"{merged_module} {target_module}"
                                        ],
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
                        pip_id.env.cr.commit()
                    self.pip_requirement_ids |= pip_id
            if recipe.get("odoo_pip_requirements"):
                odoo_pip_requirements = recipe.get("odoo_pip_requirements")
                for pip_name in odoo_pip_requirements:
                    pip_id = self.env["pip.requirement"].search(
                        [("name", "=", pip_name)]
                    )
                    if not pip_id:
                        pip_id = self.env["pip.requirement"].create({"name": pip_name})
                        pip_id.env.cr.commit()
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
                    odoo_id.env.cr.commit()
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
                        or m.module_to_install_name != module_to_install_name
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
                        or m.module_to_install_name != module_to_install_name
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
                        module_id.env.cr.commit()
                    self.module_to_delete_after_migration_ids |= module_id
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
                        module_id.env.cr.commit()
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
                        module_id.env.cr.commit()
                    self.module_to_uninstall_before_migration_ids |= module_id
        self._compute_module_installed_ids()

    def load_config_file(self):
        migration = self.openupgrader_migration_id
        file_content = False
        # A path takes precedence over the stored binary: it allows the yml to
        # be edited on disk and reloaded without uploading it again.
        if migration.config_file_path and os.path.isfile(migration.config_file_path):
            with open(migration.config_file_path, "rb") as config_file:
                file_content = config_file.read()
        elif migration.config_file:
            file_content = base64.decodebytes(migration.config_file)  # noqa
        if file_content:
            repos = {}
            try:
                repos = yaml.safe_load(file_content) or {}
            except yaml.YAMLError as exc:
                logger.info(exc)
            return repos
        return None

    @staticmethod
    def _get_venv_site_packages(venv_path, py_version):
        env_folder = os.path.join(venv_path, ".venv")
        short_version = ".".join(py_version.split(".")[:2])
        candidates = [
            os.path.join(env_folder, "lib", f"python{short_version}", "site-packages"),
            os.path.join(
                env_folder, "lib64", f"python{short_version}", "site-packages"
            ),
        ]
        for candidate in candidates:
            if os.path.isdir(candidate):
                return candidate
        matches = glob.glob(
            os.path.join(env_folder, "lib*", "python*", "site-packages")
        )
        return matches[0] if matches else None

    @staticmethod
    def _canonicalize_package_name(name):
        return re.sub(r"[-_.]+", "-", name).lower()

    @staticmethod
    def _iter_distribution_metadata(site_packages):
        try:
            # py3.8
            from importlib import metadata as importlib_metadata
        except ImportError:  # pragma: no cover
            try:
                # py3.6 with backport
                import importlib_metadata
            except ImportError:
                # py3.6 without backport
                importlib_metadata = None

        if importlib_metadata:
            for dist in importlib_metadata.distributions(path=[site_packages]):
                try:
                    name = dist.metadata["Name"]
                    version = dist.metadata["Version"]
                except Exception:  # noqa: BLE001
                    continue
                yield name, version, getattr(dist, "requires", None) or []
            return

        import email.parser

        metadata_paths = glob.glob(
            os.path.join(site_packages, "*.dist-info", "METADATA")
        )
        metadata_paths += glob.glob(
            os.path.join(site_packages, "*.egg-info", "PKG-INFO")
        )
        for metadata_path in metadata_paths:
            try:
                with open(metadata_path, "r", errors="replace") as metadata_file:
                    metadata = email.parser.Parser().parse(metadata_file)
            except OSError:
                continue
            yield (
                metadata.get("Name"),
                metadata.get("Version"),
                metadata.get_all("Requires-Dist") or [],
            )

    def _get_installed_versions(self, site_packages):
        versions = {}
        for name, version, _requires in self._iter_distribution_metadata(
            site_packages
        ):
            if name and version:
                versions[self._canonicalize_package_name(name)] = version
        return versions

    @staticmethod
    def _get_addon_module_from_package_name(package_name):
        """Return the module name of an ``odoo*-addon-*`` package."""
        match = re.match(r"^odoo(?:[0-9]+)?-addon-(.+)$", package_name)
        return match.group(1) if match else None

    @staticmethod
    def _extract_requirement_package_name(requirement):
        """Extract the package name from a ``Requires-Dist`` entry."""
        requirement = requirement.split(";", 1)[0].strip()
        match = re.match(r"^([A-Za-z0-9_.-]+)", requirement)
        return match.group(1) if match else ""

    def _get_installed_addons(self, venv_path):
        """Return the Odoo addon distributions installed in the environment."""
        site_packages = self._get_venv_site_packages(venv_path, self.python_version)
        if not site_packages:
            return {}
        addons = {}
        for name, version, requires in self._iter_distribution_metadata(site_packages):
            if not name or not version:
                continue
            normalized_name = self._canonicalize_package_name(name)
            module_name = self._get_addon_module_from_package_name(normalized_name)
            if not module_name:
                continue
            addons[normalized_name] = {
                "name": name,
                "version": version,
                "module": module_name,
                "requires": requires,
            }
        return addons

    def _get_installed_addon_requirements(self, venv_path):
        """Return pinned requirements for every Odoo addon installed with pip."""
        return [
            f"{addon['name']}=={addon['version']}"
            for addon in self._get_installed_addons(venv_path).values()
        ]

    def _get_missing_addon_dependencies(self, venv_path):
        """Return same-repository addon dependencies still missing in the env.

        Odoo addon wheels declare their module dependencies as
        ``odoo*-addon-*`` ``Requires-Dist`` entries. They are normally pulled
        in by the pip/uv resolver, but this check makes the migration install
        them explicitly when a dependency was skipped or was not installed by
        the first resolution.
        """
        addons = self._get_installed_addons(venv_path)
        missing_modules = set()
        for addon in addons.values():
            for requirement in addon["requires"]:
                if "extra" in requirement.replace(" ", "").lower():
                    # optional/test dependencies are not module dependencies
                    continue
                package_name = self._extract_requirement_package_name(requirement)
                normalized_name = self._canonicalize_package_name(package_name)
                module_name = self._get_addon_module_from_package_name(normalized_name)
                if module_name and normalized_name not in addons:
                    missing_modules.add(module_name)
        return sorted(missing_modules)

    @staticmethod
    def _get_pip_compile_index_options(extra_index_url):
        """Return the index options used to resolve dependencies.

        When a private index is configured it is used together with PyPI in a
        single resolution so that newer versions or modules available only on
        the private index (and their own private dependencies) are resolved,
        while PyPI still provides public dependencies the private index can
        not satisfy (for example ``pymssql<=2.2.5``). ``unsafe-best-match``
        selects the highest version satisfying the requirements across all
        indexes. PyPI only is kept as a fallback for when the private index is
        not reachable.
        """
        if extra_index_url:
            return [
                f"--default-index {extra_index_url} "
                "--index https://pypi.org/simple "
                "--index-strategy unsafe-best-match",
                "--default-index https://pypi.org/simple",
            ]
        return ["--default-index https://pypi.org/simple"]

    def _get_odoo_stub_override(self, venv_path):
        """Create a local stub ``odoo`` package used as a compile override.

        Odoo is installed from source in editable mode and is not published on
        PyPI, so ``uv pip compile`` can not resolve the ``odoo`` requirement of
        the OCA addons. A minimal local package named ``odoo`` is referenced as
        an override and excluded from the compiled output, so the real Odoo is
        never installed nor replaced.
        """
        stub_path = os.path.join(venv_path, ".openupgrader_odoo_stub")
        os.makedirs(stub_path, exist_ok=True)
        with open(os.path.join(stub_path, "pyproject.toml"), "w") as stub_file:
            stub_file.write('[project]\nname = "odoo"\nversion = "0.0.0"\n')
        overrides_path = os.path.join(venv_path, ".openupgrader_overrides.txt")
        with open(overrides_path, "w") as overrides_file:
            overrides_file.write(f"odoo @ file://{stub_path}\n")
        return overrides_path

    def _get_odoo_requirements_file(self, venv_path):
        """Return the Odoo requirements used as compile constraints if any."""
        if self.odoo_is_openupgrade:
            odoo_path = os.path.join(venv_path, "odoo")
        else:
            odoo_path = os.path.join(venv_path, "repos", "odoo")
        requirements_path = os.path.join(odoo_path, "requirements.txt")
        return requirements_path if os.path.isfile(requirements_path) else None

    def _compile_requirements(self, seed_lines, venv_path, extra_index_url, pip_env):
        """Resolve ``seed_lines`` into a pinned requirements file.

        The resolution is first attempted with the Odoo requirements as
        constraints, in order to keep the dependency set consistent with the
        installed environment, and without constraints as a fallback.
        """
        in_path = os.path.join(venv_path, ".openupgrader_dependencies.in")
        pins_path = os.path.join(venv_path, ".openupgrader_dependencies.txt")
        with open(in_path, "w") as seed_file:
            seed_file.write("\n".join(seed_lines) + "\n")
        overrides_path = self._get_odoo_stub_override(venv_path)
        constraints = self._get_odoo_requirements_file(venv_path)
        if constraints:
            constraint_options = [f"--constraints {constraints}", ""]
        else:
            constraint_options = [""]
        short_version = ".".join(self.python_version.split(".")[:2])
        for index_options in self._get_pip_compile_index_options(extra_index_url):
            for constraint_option in constraint_options:
                parts = [
                    "uv pip compile --quiet",
                    f"--python-version {short_version}",
                    f"--overrides {overrides_path}",
                    "--no-emit-package odoo",
                ]
                if constraint_option:
                    parts.append(constraint_option)
                parts.append(index_options)
                parts.append(f"-o {pins_path} {in_path}")
                command = " ".join(parts)
                if os.path.isfile(pins_path):
                    os.remove(pins_path)
                process = Popen(
                    command,
                    cwd=venv_path,
                    shell=True,
                    stdout=PIPE,
                    stderr=PIPE,
                    env=pip_env,
                )
                _stdout, stderr = process.communicate()
                if process.returncode == 0:
                    return pins_path, index_options
                logger.info(
                    "Unable to resolve dependencies with '%s': %s",
                    command,
                    stderr.decode().strip() if stderr else "",
                )
        logger.warning("Unable to resolve dependencies for %s", seed_lines)
        return None, None

    def _install_missing_dependencies(
        self, root_names, venv_path, extra_index_url, pip_env
    ):
        """Resolve and install the Python dependencies of ``root_names``.

        The dependency closure of the installed addons is resolved with
        ``uv pip compile`` and the resulting pinned set is installed with
        ``uv pip install --no-deps``. Resolving first yields a consistent set
        of versions, while installing with ``--no-deps`` ensures the private
        index can not shadow a dependency such as ``pymssql<=2.2.5``.
        """
        site_packages = self._get_venv_site_packages(venv_path, self.python_version)
        if not site_packages:
            logger.warning(
                "Unable to find site-packages in %s, skipping dependencies",
                venv_path,
            )
            return False
        installed_versions = self._get_installed_versions(site_packages)
        seed_lines = []
        for name in root_names:
            version = installed_versions.get(self._canonicalize_package_name(name))
            seed_lines.append(f"{name}=={version}" if version else name)
        pins_path, index_options = self._compile_requirements(
            seed_lines, venv_path, extra_index_url, pip_env
        )
        if not pins_path:
            return False
        command = f"uv pip install --no-deps {index_options} -r {pins_path}"
        process = Popen(command, cwd=venv_path, shell=True, env=pip_env)
        process.wait()
        return process.returncode == 0

    @staticmethod
    def _normalize_package_name(name):
        """Normalize a distribution name the same way uv/PyPI do."""
        return name.lower().replace("_", "-")

    def _install_modules_batch(
        self,
        modules_to_install,
        requirement_by_module,
        venv_path,
        extra_index_url,
        pip_env,
    ):
        """Install as many modules as possible from the extra index at once.

        ``modules_to_install`` is updated in place: the modules installed by
        the batch are marked ``installed_from_extra``, while the modules that
        are not available on the extra index are left as ``not_installed`` so
        that the per-module fallback (including the OCA PyPI fallback) can
        handle them. A single batch avoids one install (and one dependency
        compile) per module, which dominates the migration time on large
        module sets.
        """
        if not extra_index_url or not modules_to_install:
            return set()
        module_by_normalized_pkg = {
            self._normalize_package_name(
                requirement_by_module[module].split("==")[0]
            ): module
            for module in modules_to_install
        }
        pending = {
            module: requirement_by_module[module] for module in modules_to_install
        }
        while pending:
            requirements = list(pending.values())
            reinstall_options = " ".join(
                f"--reinstall-package {requirement.split('==')[0]}"
                for requirement in requirements
            )
            command = (
                f"uv pip install --default-index {extra_index_url} "
                "--no-deps --upgrade --prerelease=allow "
                f"{reinstall_options} " + " ".join(requirements)
            )
            logger.info(
                "Installing %s modules from the extra index in one batch",
                len(pending),
            )
            process = Popen(
                command,
                cwd=venv_path,
                shell=True,
                stderr=PIPE,
                env=pip_env,
            )
            stderr = process.communicate()[1] or b""
            if process.returncode == 0:
                for module in pending:
                    modules_to_install[module] = "installed_from_extra"
                return set(pending)
            error_text = stderr.decode(errors="replace").lower()
            missing_normalized = set(
                re.findall(r"odoo(?:1[0-9])?-addon-[a-z0-9][a-z0-9-]*", error_text)
            )
            removed = False
            for normalized in missing_normalized:
                module = module_by_normalized_pkg.get(normalized)
                if module and module in pending:
                    logger.info(
                        "Module %s is not available on the extra index, "
                        "leaving it to the per-module fallback",
                        module,
                    )
                    pending.pop(module)
                    removed = True
            if not removed:
                logger.info(
                    "Unable to install the extra-index batch (%s modules), "
                    "falling back to per-module installation. uv output: %s",
                    len(pending),
                    error_text.strip()[:2000],
                )
                break
        return set()

    def _install_pip_modules_legacy(self, module_names, venv_path):
        """Pip-install Odoo addons in a Python <= 3.7 environment.

        The uv resolver and ``uv pip compile`` do not support Python 3.7, so
        the Odoo 12 environment uses the pip command of its virtualenv.
        """
        subprocess_env = _get_env_for_subprocess(venv_path, self.python_version)
        pip = os.path.join(venv_path, ".venv", "bin", "pip")
        odoo_version_int = int(self.name.split(".")[0])
        release_val = odoo_version_int if odoo_version_int < 15 else ""
        version_val = f"=={self.name}.*" if odoo_version_int >= 15 else ""
        core_modules = safe_eval(self.core_modules) if self.core_modules else []
        uninstall_before_names = self.module_to_uninstall_before_migration_ids.mapped(
            "name"
        )
        uninstall_after_names = self.module_to_uninstall_after_migration_ids.mapped(
            "name"
        )
        modules_to_install = {
            name: "not_installed"
            for name in module_names
            if name not in core_modules
            and name not in uninstall_before_names
            and name not in uninstall_after_names
        }
        for name in modules_to_install:
            package = f"odoo{release_val}-addon-{name}{version_val}"
            command = (
                f"{pip} install --no-cache-dir --pre --upgrade {package}"
            )
            logger.info("Installing module %s with pip: %s", name, command)
            process = run(
                command,
                cwd=venv_path,
                shell=True,
                env=subprocess_env,
            )
            if process.returncode == 0:
                modules_to_install[name] = "installed_from_pip"
            else:
                logger.error(
                    "Module %s not installable for %s (pip)", name, self.name
                )
        return modules_to_install

    def install_pip_modules(
        self, module_names, _install_dependencies=True
    ):  # noqa C901
        self.ensure_one()
        if not isinstance(module_names, list):
            module_names = [module_names]
        logger.info("Installing Odoo modules with pip: %s" % str(module_names))
        odoo_version_int = int(self.name.split(".")[0])
        venv_path = os.path.join(
            self.openupgrader_migration_id.folder, f"openupgrade{self.name}"
        )
        if _is_legacy_python_version(self.python_version):
            legacy_status = self._install_pip_modules_legacy(module_names, venv_path)
            if _install_dependencies:
                missing_addons = self._get_missing_addon_dependencies(venv_path)
                if missing_addons:
                    logger.info(
                        "Installing addon dependencies from the same "
                        "repositories: %s",
                        missing_addons,
                    )
                    legacy_status.update(
                        self.install_pip_modules(
                            missing_addons, _install_dependencies=False
                        )
                    )
            return legacy_status
        subprocess_env = _get_env_for_subprocess(venv_path, self.python_version)
        # try to install with pip and log error if it fails
        not_installable_modules = []
        if not self.core_modules:
            core_modules = []
        else:
            core_modules = safe_eval(self.core_modules)
        uninstall_before = self.module_to_uninstall_before_migration_ids
        uninstall_before_names = uninstall_before.mapped("name")
        uninstall_after_names = self.module_to_uninstall_after_migration_ids.mapped(
            "name"
        )
        extra_index_url = subprocess_env.get("UV_INDEX")
        pip_env = subprocess_env.copy()
        # Avoid conflicts between UV_INDEX and --default-index.
        pip_env.pop("UV_INDEX", None)
        release_val = odoo_version_int if odoo_version_int < 15 else ""
        version_val = f"=={self.name}.*" if odoo_version_int >= 15 else ""
        # exclude core modules and modules to be uninstalled
        # before/after migration
        modules_to_install = {
            name: "not_installed"
            for name in module_names
            if name not in core_modules
            and name not in uninstall_before_names
            and name not in uninstall_after_names
        }
        requirement_by_module = {
            name: f"odoo{release_val}-addon-{name}{version_val}"
            for name in modules_to_install
        }
        # Install the modules available on the extra index in a single batch,
        # then resolve their dependencies in one pass. This is much faster than
        # one install + one compile per module on large module sets.
        batch_installed = self._install_modules_batch(
            modules_to_install,
            requirement_by_module,
            venv_path,
            extra_index_url,
            pip_env,
        )
        if batch_installed and not self._install_missing_dependencies(
            [f"odoo{release_val}-addon-{name}" for name in batch_installed],
            venv_path,
            extra_index_url,
            pip_env,
        ):
            logger.warning(
                "Batch dependency resolution failed for %s modules, "
                "retrying them one by one",
                len(batch_installed),
            )
            for name in batch_installed:
                modules_to_install[name] = "not_installed"
            batch_installed = set()
        for name in modules_to_install:
            if modules_to_install[name] != "not_installed":
                continue
            # pypi standard server is not safe, get from it only OCA's author packages
            base_pkg_name = f"odoo{release_val}-addon-{name}"
            pkg_name = requirement_by_module[name]
            # Always install the base OCA version when it is available.
            oca_package_found = _check_oca_authorship(
                base_pkg_name,
                self.name,
            )

            installed = False

            # Try the extra index first for every module, installing it
            # without dependencies. --reinstall-package forces a reinstall so
            # an already installed or cached version can never shadow the one
            # provided by the extra index.
            if extra_index_url:
                command = (
                    f"uv pip install --default-index {extra_index_url} "
                    "--no-deps --upgrade "
                    f"--reinstall-package {base_pkg_name} "
                    f"--prerelease=allow {pkg_name}"
                )
                logger.info(
                    "Installing module %s from extra index: %s",
                    name,
                    command,
                )
                process = Popen(
                    command,
                    cwd=venv_path,
                    shell=True,
                    env=pip_env,
                )
                process.wait()
                if process.returncode != 0:
                    logger.info(
                        "Module %s not available on the extra index",
                        name,
                    )
                else:
                    installed = True
                    modules_to_install[name] = "installed_from_extra"
                    logger.info(
                        "Odoo module %s installed successfully from the extra index",
                        name,
                    )

            # Fall back to the standard index only for OCA authored packages,
            # since the standard server is not considered safe. Install the
            # base version without dependencies first as well.
            if oca_package_found and not installed:
                command = (
                    "uv pip install --default-index "
                    "https://pypi.org/simple --upgrade "
                    "--no-deps --reinstall-package {base} "
                    "--prerelease=allow {pkg}"
                ).format(base=base_pkg_name, pkg=pkg_name)
                logger.info(
                    "Installing Odoo OCA module from standard index: %s",
                    command,
                )
                process = Popen(
                    command,
                    cwd=venv_path,
                    shell=True,
                    stderr=PIPE,
                    env=pip_env,
                )
                stderr = process.communicate()[1]
                log_texts = []
                if stderr:
                    for log_line in stderr.splitlines():
                        try:
                            log_l = log_line.decode().lower()
                            log_texts.append(log_l)
                        except UnicodeDecodeError:
                            continue

                if process.returncode != 0:
                    if any("no solution found" in log_text for log_text in log_texts):
                        logger.info(
                            "Module %s not found with uv pip installer: %s"
                            % (
                                name,
                                "\n".join(log_text for log_text in log_texts),
                            )
                        )
                    elif any("pkg_resources" in log_text for log_text in log_texts):
                        err_log = "\n".join(log_text for log_text in log_texts)
                        logger.info(
                            "Module %s not installable for setuptools error: %s",
                            name,
                            err_log,
                        )
                    else:
                        logger.warning(
                            "Failed to install module %s from standard index: %s",
                            name,
                            "\n".join(log_text for log_text in log_texts),
                        )
                else:
                    installed = True
                    modules_to_install[name] = "installed_oca"
                    logger.info(
                        "Odoo module %s installed successfully from "
                        "standard index: %s",
                        name,
                        "\n".join(log_text for log_text in log_texts),
                    )

            # Resolve the dependencies of the just installed distribution with
            # uv pip compile and install the pinned result with --no-deps. This
            # avoids the private index shadowing a dependency (e.g.
            # pymssql<=2.2.5) and gives a consistent set of versions.
            if installed:
                if self._install_missing_dependencies(
                    [base_pkg_name], venv_path, extra_index_url, pip_env
                ):
                    logger.info(
                        "Odoo module %s installed with its dependencies",
                        name,
                    )
                else:
                    modules_to_install[name] = "not_installed"
                    logger.warning(
                        "Failed to install dependencies for module %s",
                        name,
                    )

            if modules_to_install[name] == "not_installed":
                logger.error(
                    "Module %s not installable for %s",
                    name,
                    self.name,
                )
                not_installable_modules.append(name)
        # Install addon dependencies from the same repository explicitly: a
        # module wheel declares them as ``odoo*-addon-*`` requirements, but
        # they can be skipped by a partial resolution or by an older wheel.
        if _install_dependencies:
            missing_addons = self._get_missing_addon_dependencies(venv_path)
            if missing_addons:
                logger.info(
                    "Installing addon dependencies from the same repositories: %s",
                    missing_addons,
                )
                modules_to_install.update(
                    self.install_pip_modules(
                        missing_addons, _install_dependencies=False
                    )
                )
        # Persist every installed addon (including same-repository
        # dependencies) so they become part of the project and are not
        # removed by a plain ``uv sync``.
        self._record_addon_requirements(
            venv_path,
            self._get_installed_addon_requirements(venv_path),
        )
        openupgrade_path = os.path.join(venv_path, "odoo")
        odoo_path = (
            openupgrade_path
            if self.odoo_is_openupgrade
            else os.path.join(venv_path, "repos", "odoo")
        )
        self._write_uv_pyproject(venv_path, odoo_path)
        return modules_to_install
