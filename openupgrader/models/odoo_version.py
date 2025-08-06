import os
import shutil
import subprocess

from odoo import api, fields, models

from odoo.addons.python_venv.python_venv import _create_python_venv


class OdooVersion(models.Model):
    _name = "odoo.version"
    _description = "Odoo Version"

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
    )
    openupgrader_repo_ids = fields.One2many(  # will be replaced by pip install
        comodel_name="openupgrader.repo",
        inverse_name="odoo_version_id",
        string="OpenUpgrader Repositories",
        copy=False,
    )
    openupgrader_config_ids = fields.One2many(
        comodel_name="openupgrader.config",
        inverse_name="odoo_version_id",
        string="OpenUpgrader Configurations",
        copy=False,
    )
    db_backup_id = fields.Many2one(
        comodel_name="db.backup",
        string="Database Backup",
        domain=[("is_migration_backup", "=", True)],
    )

    def _create_db_backup(self, folder):
        self.ensure_one()
        if not self.db_backup_id:
            db_backup_id = self.env["db.backup"].search(
                [
                    ("odoo_version_id", "=", self.id),
                    ("is_migration_backup", "=", True),
                ]
            )
            if not db_backup_id:
                db_backup_id = self.env["db.backup"].create(
                    {
                        "odoo_version_id": self.id,
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
    @api.depends("name")
    def _compute_module_installed_ids(self):
        for record in self:
            if record.name:
                module_installed_ids = self.env["ir.module.module"].search(
                    [
                        ("state", "=", "installed"),
                    ]
                )
                existing_module_names = (
                    self.env["module.name"].search([]).mapped("name")
                )
                missing_module_names = [
                    x
                    for x in module_installed_ids.mapped("name")
                    if x not in existing_module_names
                ]
                record.module_installed_ids = [
                    (0, 0, {"name": name}) for name in missing_module_names
                ]
            else:
                record.module_installed_ids = False

    def button_recreate_venv(self):
        # Remove folder and re-create a clean virtual environment
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
        openupgrader_repo_obj = self.env["openupgrader.repo"]
        version_repos = openupgrader_repo_obj.search(
            [
                ("odoo_version_id", "=", self.name),
            ]
        )
        version_repos.ensure_one()
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
                # install odoo repo
                odoo_repo = version_repos.remote_repo_ids.filtered("is_odoo")
                if odoo_repo:
                    openupgrader_migration_id.install_repo(
                        odoo_repo,
                        self.name,
                        odoo_path,
                    )
            commands = [
                'bin/pip install "setuptools<58.0.0"',
            ]
            commands += [
                "bin/pip install '%s'" % name
                for name in version_repos.pip_requirement_ids.mapped("name")
            ]
            if odoo_is_openupgrade:
                for c in [
                    f"cd {openupgrade_path} && ../bin/pip install -e . ",
                    f"bin/pip install -r {odoo_path}/requirements.txt",
                ]:
                    commands.append(c)
            else:
                for c in [
                    f"cd {odoo_path} && ../../bin/pip install -e . ",
                    f"bin/pip install -r {openupgrade_path}/requirements.txt",
                    f"bin/pip install -r {odoo_path}/requirements.txt",
                ]:
                    commands.append(c)
            odoo_version_int = int(self.name.split(".")[0])
            # todo exclude odoo core apps, anyway it's a non-blocking issue
            commands += [
                "bin/pip install odoo{version_name}-addon-{name}".format(
                    name=name,
                    version_name=odoo_version_int if odoo_version_int < 15 else "",
                )
                for name in self.module_installed_ids.mapped("name")
            ]
            for command in commands:
                subprocess.Popen(
                    command,
                    cwd=venv_path,
                    env=subprocess_env,
                    shell=True,
                ).wait()
            extra_path = os.path.join(venv_path, "repos")
            if not os.path.isdir(extra_path):
                subprocess.Popen(
                    "mkdir %s" % extra_path, cwd=venv_path, shell=True
                ).wait()

            for remote_repo in version_repos.remote_repo_ids.filtered(
                lambda x: not x.is_odoo
            ):
                # do not reinstall odoo repo
                openupgrader_migration_id.install_repo(
                    remote_repo,
                    self.name,
                )
            openupgrader_migration_id.state = "created_venv"
