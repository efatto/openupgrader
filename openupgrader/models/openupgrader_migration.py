
from odoo import fields, models, api, _
from odoo.exceptions import UserError

import odoorpc
import shutil
import subprocess
import time
import os
import signal
import sys
import ssl
from urllib.request import HTTPSHandler

from odoorpc.rpc import (
    build_opener, CookieJar, HTTPCookieProcessor
)
# from main import openupgrade_fixes
import logging
logger = logging.getLogger(__name__)


class OpenupgraderMigration(models.Model):
    _name = "openupgrader.migration"
    _description = "Openupgrader Migration"
    _rec_name = 'from_version'

    """
    :param db_port: la porta del database su cui la funzione andra a
    cancellare e ripristinare il database da migrare
    xmlrpc_port: la porta su cui è accessibile il servizio - viene
    impostata come 80 + i due numeri finali della porta del db
    n.b. non deve essere in uso da altre istanze
    in mancanza va con il postgres di default nella porta indicata
    """

    db_name = fields.Char(string="Database name",
                          default=lambda self: self.env.cr.dbname)
    db_user = fields.Char(string="Database user")
    db_password = fields.Char(string="Database password")
    pg_user = fields.Char(string="Postgres user", default="odoo")
    verify_ssl = fields.Boolean()
    address = fields.Char("Odoo URL")
    local = fields.Boolean("Odoo is in local network")
    disabled_cron_ids = fields.Many2many(
        comodel_name="ir.cron",
        string="Disabled ir crons",
    )
    odoo_pid = fields.Integer(string="Odoo migrated process PID")
    db_port = fields.Char(string="Database port", default='5432')
    xmlrpc_port = fields.Char(string="XmlRpc port", default='8032')
    folder = fields.Char(
        default=lambda self: self._default_folder(),
        help="Absolute path for migrated Odoo instance",
        required=True,
    )
    # self.fixes = openupgrade_fixes.Fixes()
    from_version = fields.Many2one(
        comodel_name="odoo.version",
        string="From Odoo Version",
    )
    to_version = fields.Many2one(
        comodel_name="odoo.version",
        string="To Odoo Version",
    )
    filestore = fields.Boolean()
    migrate_ddt = fields.Boolean()
    repo_ids = fields.Many2many(
        comodel_name="openupgrader.repo",
        relation="openupgrader_migration_repo_rel",
        column1="migration_id",
        column2="repo_id",
        string="Repositories",
    )
    config_ids = fields.Many2many(
        comodel_name="openupgrader.config",
        relation="openupgrader_migration_config_rel",
        column1="migration_id",
        column2="config_id",
        string="Openupgrader config",
    )
    openupgrade_repo = fields.Char(
        string="OpenUpgrade Repository",
    )
    odoo_repo = fields.Char(
        string="Odoo Repository",
    )
    odoo_migrated_state = fields.Selection(
        selection=[
            ('running', 'Running'),
            ('stopped', 'Stopped'),
        ],
        string="Migrated Odoo state",
        help="Migrated Odoo is running or stopped",
        default="stopped",
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('created_venv', 'Created Venv'),
            ('restoring_db', 'Restoring DB'),
            ('restore_failed', 'Restore failed'),
            ('db_restored', 'DB restored'),
            ('after_prepare_migration', 'After prepare migration'),
            ('migrating', 'Migrating'),
            ('failed', 'Failed'),
            ('migrated', 'Migrated'),
            ('after_migration', 'After migration'),
        ],
        string="Migration state",
        readonly=True,
        default='draft',
    )

    @api.model
    def _default_folder(self):
        """Default to ``backups`` folder inside current server datadir."""
        return os.path.join(
            os.path.expanduser("~"),
            "odoo_migration",
            self.env.cr.dbname,
        )

    def odoo_connect(self):
        if self.db_name and self.db_password:
            client = odoorpc.ODOO(
                host="localhost",
                protocol='jsonrpc',
                port=self.xmlrpc_port,
                opener=self._get_opener(verify_ssl=False)
            )
            client.login(
                db=f"{self.db_name}_migrate",
                login=self.db_user,
                password=self.db_password
            )
            time.sleep(5)
            return client

    @staticmethod
    def _get_opener(verify_ssl=True, sessions=True):
        handlers = []
        if not verify_ssl:
            if (
                (sys.version_info[0] == 2 and sys.version_info >= (2, 7, 9)) or
                (sys.version_info[0] == 3 and sys.version_info >= (3, 2, 0))
            ):
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                handlers.append(HTTPSHandler(context=context))
            else:
                logger.info(_(
                    ('verify_ssl could not be established for this '
                     'python version: %s') % sys.version
                ))
        if sessions:
            handlers.append(HTTPCookieProcessor(CookieJar()))
        opener = build_opener(*handlers)
        return opener

    def button_start_odoo(self):
        self.start_odoo(version=self.from_version)

    def start_odoo(
        self, version, update=False, extra_command='', save=False, wait=False
    ):
        """
        :param version: odoo version to start (8.0, 9.0, 10.0, ...)
        :param update: if True odoo will be updated with -u all and stopped
        :param extra_command: command that will be passed after executable
        :param save: if True save .odoorc and stop Odoo
        :param wait: if True process will wait for execution
        :return: Odoo instance in "self.odoo_client" if not updated, else nothing
        """
        if self.odoo_migrated_state == "running":
            self.button_stop_odoo()
        version_name = version.name
        folder = os.path.join(self.folder, f"openupgrade{version_name}")
        load = 'web'
        if version_name == '10.0':
            load = 'web,web_kanban'
        if version_name not in ['7.0', '8.0', '9.0', '10.0', '11.0']:
            load = 'base,web'
        if version_name not in [
            '7.0', '8.0', '9.0', '10.0', '11.0', '12.0', '13.0'
        ]:
            load += ',openupgrade_framework,module_change_auto_install'
        executable = 'openerp-server' if version_name in ['7.0', '8.0', '9.0']\
            else 'odoo-bin'
        odoorc_exist = bool(
            os.path.isfile(
                os.path.join(folder, '.odoorc')
            )
            and not self.migrate_ddt
            and not save
        )
        addons_path = f'{folder}/repos/odoo/addons,'
        if version_name in ['8.0', '9.0', '10.0', '11.0', '12.0', '13.0']:
            addons_path = f"{folder}/openupgrade/addons,"
        extra_addons_path = \
            f',{folder}/repos/odoo/odoo/addons,{folder}/odoo'
        if version_name in ['10.0', '11.0', '12.0', '13.0']:
            extra_addons_path = f',{folder}/openupgrade/odoo/addons'
        bash_command = \
            "deactivate && " \
            f"{folder}/openupgrade/{executable} " \
            f"{f'-c {folder}/.odoorc' if odoorc_exist else ''} " \
            f"{not odoorc_exist and f'--addons-path={addons_path}' or ''}" \
            f"{not odoorc_exist and f'{folder}/addons-extra' or ''}" \
            f"{not odoorc_exist and extra_addons_path or ''}" \
            f" {extra_command} " \
            f"{not odoorc_exist and f'--db_port={self.db_port}' or ''} " \
            f"--xmlrpc-port={self.xmlrpc_port} " \
            f"{not odoorc_exist and f'--logfile={folder}/migration.log' or ''} " \
            f"{not odoorc_exist and '--limit-time-cpu=16000' or ''} " \
            f"{not odoorc_exist and '--limit-time-real=32000' or ''} " \
            f"{not odoorc_exist and '--limit-memory-soft=4147483648' or ''} " \
            f"{not odoorc_exist and '--limit-memory-hard=4679107584' or ''} " \
            f"{not odoorc_exist and f'--load={load}' or ''} " \
            f"-d {self.env.cr.dbname}_migrate "
        if not odoorc_exist and version_name != '7.0':
            bash_command += f"--data-dir={folder}/data_dir "
        if update:
            bash_command += "-u all --stop "
        if save:
            bash_command += "-s --stop"
        process = subprocess.Popen(
            bash_command.split(), cwd=folder, stdout=subprocess.PIPE)
        self.odoo_pid = process.pid
        if wait:
            process.wait()
        if not update and not extra_command:
            time.sleep(15)
            if not save:
                self.odoo_migrated_state = "running"
            else:
                not_auto_install_list = [
                    "partner_autocomplete", "iap", "mail_bot", "account_edi",
                    "account_edi_facturx", "account_edi_ubl", "l10n_it_stock_ddt"
                ]
                mod_not_install = \
                    f"modules_auto_install_disabled = {','.join(not_auto_install_list)}"
                if not os.path.isfile(os.path.join(os.path.expanduser("~"), ".odoorc")):
                    raise UserError(_("Missing .odoorc file in home path!"))
                subprocess.Popen(
                    [f'echo {mod_not_install} >> .odoorc'],
                    shell=True
                ).wait()
                shutil.move(
                    os.path.join(os.path.expanduser("~"), ".odoorc"),
                    os.path.join(self.folder, f"openupgrade{version_name}", ".odoorc"),
                )
        time.sleep(5)

    def button_stop_odoo(self):
        if self.odoo_pid:
            try:
                os.kill(self.odoo_pid, signal.SIGTERM)
            except OSError:
                time.sleep(10)
                try:
                    os.kill(self.odoo_pid, signal.SIGKILL)
                except OSError:
                    pass
        self.odoo_pid = False
        self.odoo_migrated_state = "stopped"

    def disable_mail(self, disable=False):
        state = 'draft' if disable else 'done'
        active = False if disable else True
        fetchmail_server_ids = self.env["fetchmail.server"].search([
            ("state", "=", state),
        ])
        if fetchmail_server_ids:
            fetchmail_server_ids.write({"state": state})
        ir_mail_server_ids = self.env["ir.mail_server"].with_context(
            active_test=False,
        ).search([
            ("active", "=", active),
        ])
        if ir_mail_server_ids:
            ir_mail_server_ids.write({"active": active})

    def move_filestore(self, from_folder=False, from_version=False, to_version=False):
        if not from_folder:
            from_folder = (
                f'{self.folder}/{from_version}/data_dir'
                f'/filestore/{self.env.cr.dbname}')
        to_version_filestore = (
            f'{self.folder}/{to_version}/data_dir'
            f'/filestore/{self.env.cr.dbname}')
        if os.path.isdir(to_version_filestore):  # todo check: and not restore_db_only:
            shutil.rmtree(to_version_filestore, ignore_errors=True)
        # todo check: if not restore_db_only:
        os.rename(from_folder, to_version_filestore)

    def restore_filestore(self, from_version, to_version):
        filestore_path = os.path.join(
            self.folder, to_version, 'data_dir', 'filestore'
        )
        if not os.path.isdir(filestore_path):
            os.makedirs(filestore_path, exist_ok=True)
        dump_folder = os.path.join(self.path, 'filestore')
        dump_file = os.path.join(self.path, 'filestore.tar')
        if os.path.isdir(dump_folder):
            self.move_filestore(from_folder=dump_folder, to_version=to_version)
            return
        elif os.path.isfile(dump_file):
            os.rename(dump_file, f'{self.folder}/filestore.{from_version}.tar')
        dump_file = os.path.join(
            self.folder, f'filestore.{from_version}.tar')
        filestore_db_path = os.path.join(
            filestore_path, self.env.cr.dbname
        )
        if not os.path.isdir(filestore_db_path):
            os.mkdir(filestore_db_path)
        process = subprocess.Popen([
            f'tar -zxvf {dump_file} --strip-components=1 -C {filestore_db_path}/'
        ], shell=True)
        process.wait()

    def dump_filestore(self, version):
        process = subprocess.Popen([
            'cd %s/%s/data_dir/filestore && tar -zcvf %s/filestore.%s.tar %s' % (
                self.folder, version, self.folder, version, self.env.cr.dbname)
        ], shell=True)
        process.wait()

    def dump_database(self, version):
        process = subprocess.Popen(
            [
                f"pg_dump -O -p {self.db_port} -d {self.env.cr.dbname} > "
                f"{os.path.join(self.folder, 'database.%s.sql' % version)}"
            ], shell=True)
        process.wait()

    def restore_db(self):
        subprocess.Popen(
            [f'dropdb -p {self.db_port} {self.env.cr.dbname}_migrate'],
            shell=True).wait()
        subprocess.Popen(
            [f'createdb -p {self.db_port} {self.env.cr.dbname}_migrate'],
            shell=True).wait()
        dump_file_sql = os.path.join(
            self.folder, f"database.{self.from_version.name}.sql")
        if not os.path.isfile(dump_file_sql):
            raise UserError(_("Dump sql file %s not found!") % dump_file_sql)

        subprocess.Popen(
            [
                f'cat {dump_file_sql} | psql -U {self.pg_user} -p {self.db_port} '
                f'-d {self.env.cr.dbname}_migrate'
            ],
            shell=True).wait()
        os.unlink(dump_file_sql)

    def button_prepare_migration_restore_db_update(self):
        self.ensure_one()
        from_version = self.from_version
        self.dump_database(from_version.name)
        if self.filestore:
            self.restore_filestore(from_version, from_version)
        self.restore_db()
        self.disable_mail(disable=True)
        # n.b. when updating, at the end odoo service is stopped
        self.start_odoo(from_version, save=True, wait=True)
        self.start_odoo(from_version, update=True)

    def button_prepare_migration_restore_db_only(self):
        self.ensure_one()
        # restore db if not restored before, not needed if migration for more version
        self.restore_db()
        self.state = 'db_restored'

    def button_prepare_migration_update_only(self):
        self.ensure_one()
        from_version = self.from_version
        self.disable_mail(disable=True)
        # n.b. when updating, at the end odoo service is stopped
        self.start_odoo(from_version, save=True, wait=True)
        self.start_odoo(from_version, update=True)

    def button_after_prepare_migration(self):
        from_version = self.from_version
        to_version = self.to_version
        if self.filestore:
            self.move_filestore(
                from_version=from_version, to_version=to_version)
        self.disable_mail(disable=True)
        # self.sql_fixes(self.env["openupgrader.config"].search([
        # ("odoo_version_id", "=", from_version)]))
        self.uninstall_modules(from_version, before_migration=True)
        self.delete_old_modules(from_version)
        self.state = 'after_prepare_migration'

    def disable_cron(self, disable=False):
        # disable cron on current running istance, to be re-enabled in the migrated one
        if disable:
            ir_cron_ids = self.env["ir.cron"].search([])
            if ir_cron_ids:
                ir_cron_ids.write({"active": False})
                self.disabled_cron_ids = ir_cron_ids
        if not disable and self.disabled_cron_ids:
            sql = (f"UPDATE ir_cron SET active = true WHERE id in "
                   f"{(_id for _id in self.disabled_cron_ids.ids)};")
            subprocess.Popen(
                [f'psql -p {self.db_port} -d '
                 f'{self.env.cr.dbname}_migrate -c "{sql}"'],
                shell=True
            )

    def button_do_migration(self):
        self.disable_cron(True)
        to_version = self.to_version
        # from_version = self.from_version
        # if to_version == '11.0':
        #     self.fix_taxes(from_version)
        # if to_version == '12.0' and self.fix_banks:
        #     self.fixes.migrate_bank_riba_id_bank_ids(from_version)
        #     self.fixes.migrate_bank_riba_id_bank_ids_invoice(from_version)
        # if from_version == '12.0' and self.migrate_ddt:
        #     self.migrate_l10n_it_ddt_to_l10n_it_delivery_note(from_version)
        odoorc_exist = bool(
            os.path.isfile(
                os.path.join(self.folder, f"openupgrade{to_version.name}", '.odoorc')
            )
        )
        if not odoorc_exist:
            self.start_odoo(to_version, save=True, wait=True)
        self.start_odoo(to_version, update=True)

    def button_after_migration(self):
        to_version = self.to_version
        from_version = self.from_version
        self.uninstall_modules(to_version, after_migration=True)
        self.auto_install_modules(to_version)
        self.sql_fixes(self.env["openupgrader.config"].search([
            ("odoo_version_id", "=", to_version.name)]))
        if to_version.name == '10.0':
            self.start_odoo(to_version)
            self.remove_modules('upgrade')
            self.remove_modules()
            self.install_uninstall_module('l10n_it_intrastat')
            self.button_stop_odoo()
        self.dump_database(to_version.name)
        # if self.filestore:
        #     self.dump_filestore(to_version.name)
        logger.info(_(f"Migration done from version {from_version.name} "
                      f"to version {to_version.name}"))
        # self.disable_cron() # to be re-enabled manually after all is gone ok
        self.from_version = self.to_version
        self.to_version = str(float(self.from_version.name) + 1)
        logger.info(_(f"Set next version to {self.to_version}"))
        self.state = 'after_migration'

    def button_refresh_state(self):
        for version in [self.from_version, self.to_version]:
            venv_path = os.path.join(self.folder, f"openupgrade{version.name}")
            migration_log_path = os.path.join(venv_path, 'migration.log')
            if os.path.isfile(migration_log_path):
                with open(migration_log_path) as file:
                    contents = file.read()
                    if version == self.from_version:
                        self.state = 'restoring_db'
                        if "CRITICAL" in contents:
                            self.state = 'restore_failed'
                        elif "Initiating shutdown" in contents:
                            self.state = 'db_restored'
                    if version == self.to_version:
                        self.state = 'migrating'
                        if "CRITICAL" in contents:
                            self.state = 'failed'
                        elif "Initiating shutdown" in contents:
                            self.state = 'migrated'

    def sql_fixes(self, receipt):
        for part in receipt:
            bash_commands = part.get('sql_commands', [])
            for bash_command in bash_commands:
                command = [
                    "psql -p %s -d %s -c \'%s\'" % (
                        self.db_port,
                        f"{self.env.cr.dbname}_migrate",
                        bash_command,
                    )
                ]
                subprocess.Popen(command, shell=True).wait()
            bash_update_commands = part.get('sql_update_commands', [])
            if bash_update_commands:
                for bash_update_command in bash_update_commands:
                    upd_command = [
                        'psql -p %s -d %s -c "%s"' % (
                            self.db_port,
                            f"{self.env.cr.dbname}_migrate",
                            bash_update_command,
                        )
                    ]
                    subprocess.Popen(upd_command, shell=True).wait()

    def post_migration(self, version):
        # re-enable mail servers and clean db
        self.disable_mail(disable=False)
        # self.database_cleanup(version)

    @api.multi
    def button_create_venv(self):
        for version in (self.from_version, self.to_version):
            self.create_venv_git_version(version.name)

    def install_repo(self, repo_name, repo_url, repo_version, version_name, venv_path):
        repo_path = os.path.join(
            self.folder, f"openupgrade{version_name}", 'repos', repo_name)
        if not os.path.isdir(repo_path):
            process = subprocess.Popen([
                f'git clone --single-branch -b {repo_version} {repo_url} --depth 1 '
                f'{repo_path}'
            ], shell=True)
            process.wait()
        process = subprocess.Popen([
            f'cd {repo_path} && git fetch --all && git reset --hard '
            f'origin/{repo_version}'
        ], shell=True)
        process.wait()
        # copy modules to create a unique addons path
        for root, dirs, files in os.walk(repo_path):
            for d in dirs:
                if d not in ['.git', 'setup']:
                    process = subprocess.Popen([
                        f"cp -rf {repo_path}/{d} "
                        f"{os.path.join(venv_path, 'addons-extra')}"
                    ], shell=True)
                    process.wait()
            break

    def create_venv_git_version(self, version_name):
        openupgrader_repo_obj = self.env["openupgrader.repo"]
        version_repos = openupgrader_repo_obj.search([
            ("odoo_version_id", "=", version_name),
        ])
        if len(version_repos) != 1:
            raise UserError(_("Version repositories not found!"))
        self.write({
            "repo_ids": [(6, 0, version_repos.ids)],
        })
        openupgrader_config_obj = self.env["openupgrader.config"]
        config_ids = openupgrader_config_obj.search([
            ('odoo_version_id', '=', version_name),
        ])
        if len(config_ids) != 1:
            raise UserError(_("OpenUpgrader config not found!"))
        self.write({
            "config_ids": [(6, 0, config_ids.ids)],
        })
        odoo_is_openupgrade = config_ids.odoo_version_id.odoo_is_openupgrade
        # Install OpenUpgrade repository always in ./openupgrade/<version>/openupgrade/
        # Odoo is OpenUpgrade until v. 13.0, from v. 14.0 Odoo is in ./<version/odoo

        venv_path = os.path.join(self.folder, f"openupgrade{version_name}")
        openupgrade_path = os.path.join(venv_path, "openupgrade")
        odoo_path = openupgrade_path if odoo_is_openupgrade else (
            os.path.join(openupgrade_path, "repos", 'odoo'))
        py_version = config_ids.odoo_version_id.python_version
        # create virtualenv
        if not os.path.isdir(venv_path):
            subprocess.Popen(['mkdir -p %s' % venv_path], shell=True).wait()
            # do not recreate virtualenv as it regenerate file with bug in split()
            # ../openupgrade10.0/lib/python2.7/site-packages/pip/_internal/vcs/git.py
            subprocess.Popen([
                'virtualenv -p python%s %s' % (py_version, venv_path)],
                shell=True).wait()
        # install odoo Openupgrade repo, from v. 14.0 it contains only migration script
        if not os.path.isdir(openupgrade_path):
            subprocess.Popen([
                'cd %s && git clone --single-branch %s -b %s --depth 1 openupgrade' % (
                    venv_path, self.openupgrade_repo, version_name)
            ], shell=True).wait()
        else:
            subprocess.Popen([
                'cd %s && git reset --hard origin/%s && git pull '
                '&& git reset --hard origin/%s' % (
                    openupgrade_path, version_name, version_name
                )
            ], shell=True).wait()

        if not odoo_is_openupgrade:
            # install odoo repo
            self.install_repo(
                "odoo", self.odoo_repo, version_name, version_name, venv_path)
        commands = [
            'bin/pip install "setuptools<58.0.0"',
            'bin/pip install -r '
            f'{openupgrade_path if odoo_is_openupgrade else odoo_path}'
            '/requirements.txt',
            f'cd {openupgrade_path} && ../bin/pip install -e . ' if odoo_is_openupgrade
            else f'cd {odoo_path} && ../../bin/pip install -e . ',
        ]
        for command in commands:
            subprocess.Popen(command, cwd=venv_path, shell=True).wait()
        extra_paths = ['%s/addons-extra' % venv_path, '%s/repos' % venv_path]
        for path in extra_paths:
            if not os.path.isdir(path):
                process = subprocess.Popen('mkdir %s' % path, cwd=venv_path, shell=True)
                process.wait()
        migration_log_path = os.path.join(venv_path, 'migration.log')
        if os.path.isfile(migration_log_path):
            os.remove(migration_log_path)

        for remote_repo in version_repos.remote_repo_ids:
            self.install_repo(remote_repo.name, remote_repo.remote_url,
                              remote_repo.remote_branch, version_name, venv_path)
        self.state = 'created_venv'

    def auto_install_modules(self, version):
        self.start_odoo(version)
        odoo_client = self.odoo_connect()
        module_obj = odoo_client.env['ir.module.module']
        if version.name == '12.0':
            self.remove_modules('upgrade')
        openupgrader_config = self.env["openupgrader.config"].search([
            ("odoo_version_id.id", "=", version.id)
        ])
        for module in openupgrader_config.module_auto_install_ids:
            module_to_check = module.name
            module_to_install = module.module_to_install_name
            if module_obj.search([
                ('name', '=', module_to_check),
                ('state', '=', 'installed')]
            ):
                odoo_client.env.install(module_to_install)
        self.button_stop_odoo()

    def uninstall_modules(
        self, version, before_migration=False, after_migration=False
    ):
        self.start_odoo(version)
        if version.name == '12.0':
            self.remove_modules('upgrade')
        openupgrader_config = self.env["openupgrader.config"].search(
            [("odoo_version_id.id", "=", version.id)])
        if after_migration:
            for module in openupgrader_config.module_to_uninstall_after_migration_ids:
                self.install_uninstall_module(module, install=False)
        if before_migration:
            for module in openupgrader_config.module_to_uninstall_before_migration_ids:
                self.install_uninstall_module(module, install=False)
        self.button_stop_odoo()

    def delete_old_modules(self, version):
        openupgrader_config = self.env["openupgrader.config"].search([
            ("odoo_version_id.id", "=", version.id)])
        if openupgrader_config.module_to_delete_after_migration_ids:
            odoo_client = self.odoo_connect()
            module_obj = odoo_client.env['ir.module.module']
            for module in openupgrader_config.module_to_delete_after_migration_ids:
                module = module_obj.search([('name', '=', module)])
                if module:
                    module.unlink()
            self.button_stop_odoo()

    def remove_modules(self, module_state=''):
        if module_state == 'upgrade':
            state = ['to upgrade', ]
        else:
            state = ['to remove', 'to install']
        odoo_client = self.odoo_connect()
        module_obj = odoo_client.env['ir.module.module']
        modules = module_obj.search([('state', 'in', state)])
        msg_modules = ''
        msg_modules_after = ''
        if modules:
            msg_modules = str([x.name for x in modules])
        for module in modules:
            module.button_uninstall_cancel()
        modules_after = module_obj.search(
            [('state', '=', 'to upgrade')])
        if modules_after:
            msg_modules_after = str([x.name for x in modules_after])
        logger.info(_('Modules: %s' % msg_modules))
        logger.info(_('Modules after: %s' % msg_modules_after))

    @staticmethod
    def uninst(module_to_uninstall, module_to_unistall_id, success):
        try:
            module_to_unistall_id.button_immediate_uninstall()
            module_to_unistall_id.unlink()
            logger.info(_('Module %s uninstalled' % module_to_uninstall))
            success = 5
        except Exception as e:
            logger.info(_(
                'Module %s not uninstalled for %s, trying %s/%s times.' % (
                    module_to_uninstall, str(e).replace('\n', ''), success + 1, 5)
            ))
            time.sleep(10)
            success += 1
        return success

    def install_uninstall_module(self, module, install=True):
        odoo_client = self.odoo_connect()
        module_obj = odoo_client.env['ir.module.module']
        to_remove_modules = module_obj.search([('state', '=', 'to remove')])
        for module_to_remove in to_remove_modules:
            module_to_remove.button_uninstall_cancel()
        state = odoo_client.env.modules(module)
        if state:
            if install:
                odoo_client.env.install(module)
            elif state.get('installed', False) or state.get('to upgrade', False)\
                    or state.get('uninstallable'):
                module_id = module_obj.search([('name', '=', module)])
                if module_id:
                    res = 0
                    while res < 5:
                        res = self.uninst(module, module_id, res)
                else:
                    logger.info(_('Module %s not found' % module))
