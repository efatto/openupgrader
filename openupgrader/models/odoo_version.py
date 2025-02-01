import os
import subprocess

from odoo import api, fields, models


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
        string="Python Version",
        required=True,
        default="3.7.16"
    )
    odoo_is_openupgrade = fields.Boolean(
        string="Odoo is Openupgrade",
        compute="_compute_odoo_is_openupgrade",
        store=True,
    )

    @api.depends("name")
    def _compute_odoo_is_openupgrade(self):
        for record in self:
            if float(record.name) < 14:
                record.odoo_is_openupgrade = True
            else:
                record.odoo_is_openupgrade = False

    def button_create_venv(self):
        self.ensure_one()
        openupgrader_migration_id = self.env["openupgrader.migration"].search([])
        # if len(openupgrader_migration) != 1:
        #     raise UserError(_("Missing Openupgrader Migration record!"))
        # if len(openupgrader_migration) > 1:
        #     raise UserError(_("Only one Openupgrader Migration record can be created!"))
        self.create_venv_git_version(self.name, openupgrader_migration_id)

    def _get_env_for_subprocess(self, folder):
        env_for_subprocess = os.environ.copy()
        env_for_subprocess["VIRTUAL_ENV"] = folder
        env_for_subprocess["PYTHONPATH"] = folder
        env_for_subprocess["PATH"] = ":".join([
            folder,
            os.path.join(folder, "bin"),
            "/bin/pip",
        ])
        env_for_subprocess["PWD"] = folder
        python_root = os.path.join(
            folder,
            "lib",
            f"python{'.'.join(self.python_version.split('.')[:2])}")
        if os.path.isdir(python_root):
            env_for_subprocess["LIBRARY_ROOTS"] = python_root
        return env_for_subprocess

    def _create_python_venv(self, venv_path, py_version):
        # create virtualenv
        subprocess_env = self._get_env_for_subprocess(venv_path)
        if not os.path.isdir(venv_path):
            subprocess.Popen([f'mkdir -p {venv_path}'], shell=True).wait()
            # do not recreate virtualenv as it regenerate file with bug in split()
            # ../openupgrade10.0/lib/python2.7/site-packages/pip/_internal/vcs/git.py
        subprocess.Popen([f'pyenv install -s {py_version}'], shell=True).wait()
        subprocess.Popen(
            [f'pip install virtualenv'], shell=True).wait()
        subprocess.Popen([f'ln -s /usr/bin/git'], cwd=venv_path, shell=True).wait()
        subprocess.Popen(
            [f'''virtualenv -p {
                os.path.join(
                    os.path.expanduser("~"),
                    ".pyenv",
                    "versions",
                    py_version,
                    "bin",
                    "python"
                )} {venv_path}'''],
            cwd=venv_path,
            shell=True).wait()
        return subprocess_env

    def create_venv_git_version(self, version_name, openupgrader_migration_id):
        openupgrader_repo_obj = self.env["openupgrader.repo"]
        version_repos = openupgrader_repo_obj.search([
            ("odoo_version_id", "=", version_name),
        ])
        # if len(version_repos) != 1:
        #     raise UserError(_("Version repositories not found!"))
        if openupgrader_migration_id:
            openupgrader_migration_id.write({
                "repo_ids": [(6, 0, version_repos.ids)],
            })
        openupgrader_config_obj = self.env["openupgrader.config"]
        config_ids = openupgrader_config_obj.search([
            ('odoo_version_id', '=', version_name),
        ])
        # if len(config_ids) != 1:
        #     raise UserError(_("OpenUpgrader config not found!"))
        if openupgrader_migration_id:
            openupgrader_migration_id.write({
                "config_ids": [(6, 0, config_ids.ids)],
            })
        odoo_is_openupgrade = self.odoo_is_openupgrade
        # Odoo is OpenUpgrade until v. 13.0, from v. 14.0 Odoo is in ./<version/odoo
        # install odoo Openupgrade repo, from v. 14.0 it contains only migration script
        if openupgrader_migration_id:
            venv_path = os.path.join(
                openupgrader_migration_id.folder, f"openupgrade{version_name}")
            subprocess_env = self._create_python_venv(venv_path, self.python_version)
            openupgrade_path = os.path.join(venv_path, "odoo")
            odoo_path = openupgrade_path if odoo_is_openupgrade else (
                os.path.join(venv_path, "repos", 'odoo'))
            if not os.path.isdir(openupgrade_path):
                subprocess.Popen([
                    f"git clone --single-branch "
                    f"{openupgrader_migration_id.openupgrade_repo} -b {version_name} "
                    "--depth 1 odoo "
                ],
                    cwd=venv_path,
                    env=subprocess_env,  # forse qui non serve
                    shell=True
                ).wait()
            else:
                subprocess.Popen([
                    f'git reset --hard origin/{version_name}',
                    'git pull',
                    f'git reset --hard origin/{version_name}',
                ],
                    cwd=openupgrade_path,
                    env=subprocess_env,  # forse qui non serve
                    shell=True,
                ).wait()

            if not odoo_is_openupgrade:
                # install odoo repo
                openupgrader_migration_id.install_repo(
                    "odoo", openupgrader_migration_id.odoo_repo,
                    version_name, version_name,
                    os.path.join(venv_path, "repos", "odoo"))
            commands = [
                'bin/pip install "setuptools<58.0.0"',
                'bin/pip install -r '
                f'{openupgrade_path if odoo_is_openupgrade else odoo_path}'
                '/requirements.txt',
                f'cd {openupgrade_path} && ../bin/pip install -e . '
                if odoo_is_openupgrade
                else f'cd {odoo_path} && ../../bin/pip install -e . ',
            ]
            for command in commands:
                subprocess.Popen(
                    command,
                    cwd=venv_path,
                    env=subprocess_env,
                    shell=True,
                ).wait()
            extra_paths = ['%s/addons-extra' % venv_path, '%s/repos' % venv_path]
            for path in extra_paths:
                if not os.path.isdir(path):
                    process = subprocess.Popen('mkdir %s' % path, cwd=venv_path,
                                               shell=True)
                    process.wait()
            migration_log_path = os.path.join(venv_path, 'migration.log')
            if os.path.isfile(migration_log_path):
                os.remove(migration_log_path)

            for remote_repo in version_repos.remote_repo_ids:
                openupgrader_migration_id.install_repo(
                    remote_repo.name, remote_repo.remote_url,
                    remote_repo.remote_branch, version_name, venv_path)
            openupgrader_migration_id.state = 'created_venv'
