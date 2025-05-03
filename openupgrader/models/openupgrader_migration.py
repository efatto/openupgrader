# from main import openupgrade_fixes
import logging
import os
import shutil
import signal
import ssl
import sys
import time
from subprocess import PIPE, Popen
from urllib.request import HTTPSHandler

import odoorpc
from odoorpc.rpc import CookieJar, HTTPCookieProcessor, build_opener

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.modules import get_module_resource
from odoo.tools import config

from odoo.addons.python_venv.python_venv import _get_env_for_subprocess

logger = logging.getLogger(__name__)


class OpenupgraderMigration(models.Model):
    _name = "openupgrader.migration"
    _description = "Openupgrader Migration"
    _rec_name = "from_version_id"

    """
    :param db_port: la porta del database su cui la funzione andra a
    cancellare e ripristinare il database da migrare
    xmlrpc_port: la porta su cui è accessibile il servizio - viene
    impostata come 80 + i due numeri finali della porta del db
    n.b. non deve essere in uso da altre istanze
    in mancanza va con il postgres di default nella porta indicata
    """

    db_name = fields.Char(
        string="Database name", default=lambda self: self.env.cr.dbname
    )
    db_user = fields.Char(string="Odoo user", default="admin")
    db_password = fields.Char(string="Odoo password", default="admin")
    pg_user = fields.Char(
        string="Postgres user",
        default=lambda self: config.get("db_user", "odoo"),
        help="Set the user or a environment variable (like $POSTGRES_USER)",
    )
    pg_password = fields.Char(
        string="Postgres password",
        help="Set the password directly, alternative to pg_password_var",
    )
    pg_password_var = fields.Char(
        string="Postgres password environment variable",
        help="Set the environment variable (like $POSTGRES_PASSWORD), alternative to "
             "setting the password directly",
    )
    pg_host = fields.Char(
        string="Postgres Host",
        default=lambda self: config.get("db_host", "db"))
    pg_options = fields.Char(
        string="Postgres options",
        help="Custom options for the postgres connection, like '--cluster 14/main'"
    )
    verify_ssl = fields.Boolean()
    address = fields.Char("Odoo URL")
    local = fields.Boolean("Odoo is in local network")
    disabled_cron_ids = fields.Many2many(
        comodel_name="ir.cron",
        string="Disabled ir crons",
    )
    odoo_pid = fields.Integer(string="Odoo migrated process PID")
    db_port = fields.Char(
        string="Database port", default=lambda self: config.get("db_port", "5432")
    )
    xmlrpc_port = fields.Char(
        string="XmlRpc port",
        help="Set a different port from the current one used, as this would block the "
        "instance.",
        default=lambda self: str(int(config.get("http_port", "8032") + 1)),
    )
    folder = fields.Char(
        default=lambda self: self._default_folder(),
        help="Absolute path for migrated Odoo instance",
        required=True,
    )
    # self.fixes = openupgrade_fixes.Fixes()
    from_version_id = fields.Many2one(
        comodel_name="odoo.version",
        string="From version",
    )
    to_version_id = fields.Many2one(
        comodel_name="odoo.version",
        string="To version",
    )
    current_version_id = fields.Many2one(
        comodel_name="odoo.version",
        string="Current migrated version",
    )
    next_version_id = fields.Many2one(
        comodel_name="odoo.version",
        string="Next version to be migrated",
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
        default="git@github.com:efatto/OpenUpgrade.git",
    )
    odoo_repo = fields.Char(
        string="Odoo Repository",
        default="git@github.com:OCA/OCB.git",
    )
    odoo_migrated_state = fields.Selection(
        selection=[
            ("running", "Running"),
            ("stopped", "Stopped"),
        ],
        string="Migrated state",
        help="Migrated Odoo is running or stopped",
        default="stopped",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("created_venv", "Created Venv"),
            ("restoring_db", "Restoring DB"),
            ("restore_failed", "Restore failed"),
            ("db_restored", "DB restored"),
            ("ready_for_migration", "Ready for migration"),
            ("migrating", "Migrating"),
            ("failed", "Failed"),
            ("db_migrated", "Database migrated"),
            ("done", "Done"),
        ],
        string="Migration state",
        readonly=True,
        default="draft",
    )

    @api.model
    def _default_folder(self):
        """Default to ``odoo_migration`` folder inside current home folder."""
        folder = os.path.join(
            os.path.expanduser("~"),
            "odoo_migration",
            self.env.cr.dbname,
        )
        if not os.path.isdir(folder):
            os.makedirs(folder)
        return folder

    def odoo_connect(self):
        if self.db_name and self.db_password:
            client = odoorpc.ODOO(
                host="localhost",
                protocol="jsonrpc",
                port=self.xmlrpc_port,
                opener=self._get_opener(verify_ssl=False),
            )
            client.login(
                db=f"{self.db_name}_migrate",
                login=self.db_user,
                password=self.db_password,
            )
            time.sleep(5)
            return client
        return None

    @staticmethod
    def _get_opener(verify_ssl=True, sessions=True):
        handlers = []
        if not verify_ssl:
            if (sys.version_info[0] == 2 and sys.version_info >= (2, 7, 9)) or (
                sys.version_info[0] == 3 and sys.version_info >= (3, 2, 0)
            ):
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                handlers.append(HTTPSHandler(context=context))
            else:
                logger.info(
                    _(
                        (
                            "verify_ssl could not be established for this "
                            "python version: %s"
                        )
                        % sys.version
                    )
                )
        if sessions:
            handlers.append(HTTPCookieProcessor(CookieJar()))
        opener = build_opener(*handlers)
        return opener

    @staticmethod
    def _set_odoorc(folder):
        odoorc_path = os.path.join(folder, ".odoorc")
        if not os.path.isfile(odoorc_path):
            odoorc_basic_path = get_module_resource("openupgrader", "data", ".odoorc")
            shutil.copyfile(odoorc_basic_path, odoorc_path)
            not_auto_install_list = [
                "partner_autocomplete",
                "iap",
                "mail_bot",
                "account_edi",
                "account_edi_facturx",
                "account_edi_ubl",
                "l10n_it_stock_ddt",
            ]
            mod_not_install = (
                f"modules_auto_install_disabled = {','.join(not_auto_install_list)}"
            )
            Popen(
                [f"echo {mod_not_install} >> {odoorc_path}"],
                shell=True,
            ).wait()

    def button_start_odoo(self):
        self.start_odoo(version=self.current_version_id)

    def check_venv(self, version_name):
        folder = os.path.join(self.folder, f"openupgrade{version_name}")
        if os.path.isdir(os.path.join(folder, "bin")):
            return folder
        return False

    def start_odoo(self, version, update=False, extra_command=""):
        """
        :param version: odoo version to start (8.0, 9.0, 10.0, ...)
        :param update: if True odoo will be updated with -u all and stopped
        :param extra_command: command that will be passed after executable
        :return: Odoo instance in "self.odoo_client" if not updated, else nothing
        """
        version_name = version.name
        if self.odoo_migrated_state == "running":
            self.button_stop_odoo()
        folder = self.check_venv(version_name)
        if not folder:
            raise UserError(
                _("Missing env for version %s! Create in Odoo Version menu.")
                % version_name
            )
        load = "web"
        if version_name == "10.0":
            load = "web,web_kanban"
        if float(version_name) > 11:
            load = "base,web"
        if float(version_name) > 13:
            load += ",openupgrade_framework,module_change_auto_install"
        executable = (
            f"{folder}/odoo/openerp-server"
            if float(version_name) < 10
            else f"{folder}/odoo/odoo-bin"
            if float(version_name) < 14
            else f"{folder}/repos/odoo/odoo-bin"
        )
        self._set_odoorc(folder)
        addons_path = f"{folder}/repos/odoo/addons,"
        if float(version_name) < 14:
            addons_path = f"{folder}/odoo/addons,"
        extra_addons_path = f",{folder}/repos/odoo/odoo/addons,{folder}/odoo"
        if 9 < float(version_name) < 14:
            extra_addons_path = f",{folder}/odoo/odoo/addons"
        bash_command = (
            f"{executable} "
            f"-c {folder}/.odoorc "
            f"--addons-path={addons_path}"
            f"{folder}/addons-extra"
            f"{extra_addons_path}"
            f" {extra_command} "
            f"--db_user={self.pg_user} "
            f"--db_password={self.pg_password_var or self.pg_password or ''} "
            f"--db_port={self.db_port} "
            f"--xmlrpc-port={self.xmlrpc_port} "
            f"--logfile={folder}/migration.log "
            f"--limit-time-cpu=16000 "
            f"--limit-time-real=32000 "
            f"--limit-memory-soft=4147483648 "
            f"--limit-memory-hard=4679107584 "
            f"--load={load} "
            f"-d {self.env.cr.dbname}_migrate "
        )
        if version_name != "7.0":
            data_dir = os.path.join(folder, "data_dir")
            if not os.path.isdir(data_dir):
                os.makedirs(data_dir)
            bash_command += f"--data-dir={data_dir} "
        if update:
            bash_command += "-u all --stop "
        subprocess_env = _get_env_for_subprocess(folder, version.python_version)
        logger.info(bash_command)
        process = Popen(
            bash_command, cwd=folder, stdout=PIPE, env=subprocess_env, shell=True
        )
        self.odoo_pid = process.pid
        if update:
            # only updating the process will end automatically
            process.wait()
        if not update and not extra_command:
            time.sleep(1)
            self.odoo_migrated_state = "running"
        time.sleep(1)

    def _stop_pid(self, pid=False):
        if not pid:
            pid = self.odoo_pid
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(5)
            except OSError:
                time.sleep(10)
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        self.odoo_pid = False
        self.odoo_migrated_state = "stopped"

    def button_stop_odoo(self):
        process = Popen(
            [f"pgrep -a python | grep {self.env.cr.dbname}_migrate"],
            shell=True,
            stdout=PIPE,
        )
        has_stdout = True
        pids = []
        while has_stdout:
            one_line_output = process.stdout.readline()
            if one_line_output:
                pids.append(int(one_line_output.split()[0]))
            else:
                has_stdout = False
        for pid in pids:
            self._stop_pid(pid)

    def disable_mail(self, disable=False):
        state = "draft" if disable else "done"
        active = False if disable else True
        fetchmail_server_ids = self.env["fetchmail.server"].search(
            [
                ("state", "=", state),
            ]
        )
        if fetchmail_server_ids:
            fetchmail_server_ids.write({"state": state})
        ir_mail_server_ids = (
            self.env["ir.mail_server"]
            .with_context(
                active_test=False,
            )
            .search(
                [
                    ("active", "=", active),
                ]
            )
        )
        if ir_mail_server_ids:
            ir_mail_server_ids.write({"active": active})

    def move_filestore(
        self, from_folder=False, from_version_id=False, to_version_id=False
    ):
        if not from_folder:
            from_folder = (
                f"{self.folder}/{from_version_id.name}/data_dir"
                f"/filestore/{self.env.cr.dbname}"
            )
        to_version_filestore = (
            f"{self.folder}/{to_version_id.name}/data_dir"
            f"/filestore/{self.env.cr.dbname}"
        )
        if os.path.isdir(to_version_filestore):  # todo check: and not restore_db_only:
            shutil.rmtree(to_version_filestore, ignore_errors=True)
        # todo check: if not restore_db_only:
        os.rename(from_folder, to_version_filestore)

    def restore_filestore(self, from_version_id, to_version_id):
        filestore_path = os.path.join(
            self.folder, to_version_id.name, "data_dir", "filestore"
        )
        if not os.path.isdir(filestore_path):
            os.makedirs(filestore_path, exist_ok=True)
        dump_folder = os.path.join(self.folder, "filestore")
        dump_file = os.path.join(self.folder, "filestore.tar")
        if os.path.isdir(dump_folder):
            self.move_filestore(from_folder=dump_folder, to_version_id=to_version_id)
            return
        elif os.path.isfile(dump_file):
            os.rename(dump_file, f"{self.folder}/filestore.{from_version_id.name}.tar")
        dump_file = os.path.join(self.folder, f"filestore.{from_version_id.name}.tar")
        filestore_db_path = os.path.join(filestore_path, self.env.cr.dbname)
        if not os.path.isdir(filestore_db_path):
            os.mkdir(filestore_db_path)
        process = Popen(
            [f"tar -zxvf {dump_file} --strip-components=1 -C {filestore_db_path}/"],
            shell=True,
        )
        process.wait()

    def dump_filestore(self, version):
        process = Popen(
            [
                "cd %s/%s/data_dir/filestore && tar -zcvf %s/filestore.%s.tar %s"
                % (self.folder, version, self.folder, version, self.env.cr.dbname)
            ],
            shell=True,
        )
        process.wait()

    def dump_database(self, version):
        connection_string = (
            f"postgresql://{self.pg_user}:"
            f"{self.pg_password_var or self.pg_password or ''}@"
            f"{self.pg_host or ''}:{self.db_port}/{self.env.cr.dbname}"
        )
        process = Popen(
            [
                f"pg_dump {self.pg_options or ''} -Fc -O {connection_string}"
                f"> {os.path.join(self.folder, f'database.{version}.sql')}"
            ],
            shell=True,
        )
        process.wait()

    def restore_db(self):
        Popen(
            [
                f"export PGPORT={self.db_port} && "
                f"export PGHOST={self.pg_host or ''} && "
                f"export PGPASSWORD={self.pg_password_var or self.pg_password or ''} &&"
                f" dropdb -U {self.pg_user} {self.env.cr.dbname}_migrate",
            ],
            shell=True
        ).wait()
        Popen(
            [
                f"export PGPORT={self.db_port} && "
                f"export PGHOST={self.pg_host or ''} && "
                f"export PGPASSWORD={self.pg_password_var or self.pg_password or ''} &&"
                f" createdb -U {self.pg_user} {self.env.cr.dbname}_migrate",
            ],
            shell=True
        ).wait()
        dump_file_sql = os.path.join(
            self.folder, f"database.{self.current_version_id.name}.sql"
        )
        if not os.path.isfile(dump_file_sql):
            raise UserError(_("Dump sql file %s not found!") % dump_file_sql)
        connection_string = (
            f"postgresql://{self.pg_user}:"
            f"{self.pg_password_var or self.pg_password or ''}@"
            f"{self.pg_host or ''}:{self.db_port}/{self.env.cr.dbname}_migrate"
        )
        logger.info("Connection string to pg: %s" % connection_string)
        Popen(
            [
                f"pg_restore {self.pg_options or ''} "
                f"-d {connection_string} {dump_file_sql}"
            ],
            shell=True,
        ).wait()
        os.unlink(dump_file_sql)

    def button_restore_db_update(self):
        self.button_restore_db()
        self.button_update_current_version()

    def button_restore_db(self):
        self.ensure_one()
        if not self.next_version_id:
            self.current_version_id = self.from_version_id
        self.next_version_id = self.env["odoo.version"].search(
            [("name", "=", str(float(self.current_version_id.name) + 1))]
        )
        base_module = self.env["ir.module.module"].search([("name", "=", "base")])
        if (
            self.current_version_id.name.split(".")[0]
            == base_module.installed_version.split(".")[0]
        ):
            # restore is needed only when we migrate the first version, then the db is
            # already present
            self.dump_database(self.current_version_id.name)
            if self.filestore:
                self.restore_filestore(self.current_version_id, self.current_version_id)
            self.restore_db()
        self.state = "db_restored"

    def button_update_current_version(self):
        self.ensure_one()
        self.disable_mail(disable=True)
        # n.b. when updating, at the end odoo service is stopped automatically
        self.start_odoo(self.current_version_id, update=True)

    def button_ready_for_migration(self):
        if self.filestore:
            self.move_filestore(
                from_version_id=self.current_version_id,
                to_version_id=self.next_version_id,
            )
        self.disable_mail(disable=True)
        # self.sql_fixes(self.env["openupgrader.config"].search([
        # ("odoo_version_id", "=", from_version_id)]))
        self.uninstall_modules(self.current_version_id, before_migration=True)
        self.delete_old_modules(self.current_version_id)
        self.state = "ready_for_migration"

    def disable_cron(self, disable=False):
        # disable cron on current running istance, to be re-enabled in the migrated one
        if disable:
            ir_cron_ids = self.env["ir.cron"].search([])
            if ir_cron_ids:
                ir_cron_ids.write({"active": False})
                self.disabled_cron_ids = ir_cron_ids
        if not disable and self.disabled_cron_ids:
            sql = (
                f"UPDATE ir_cron SET active = true WHERE id in "
                f"{(_id for _id in self.disabled_cron_ids.ids)};"
            )
            Popen(
                [
                    f"psql -p {self.db_port} -d "
                    f'{self.env.cr.dbname}_migrate -c "{sql}"'
                ],
                shell=True,
            )

    def button_draft(self):
        self.state = "draft"

    def button_do_migration(self):
        self.disable_cron(True)
        # to_version_id = self.to_version_id
        # from_version_id = self.from_version_id
        # if to_version_id == '11.0':
        #     self.fix_taxes(from_version_id)
        # if to_version_id == '12.0' and self.fix_banks:
        #     self.fixes.migrate_bank_riba_id_bank_ids(from_version_id)
        #     self.fixes.migrate_bank_riba_id_bank_ids_invoice(from_version_id)
        # if from_version_id == '12.0' and self.migrate_ddt:
        #     self.migrate_l10n_it_ddt_to_l10n_it_delivery_note(from_version_id)
        self.start_odoo(self.next_version_id, update=True)
        self._action_done()

    def _action_done(self):
        self.uninstall_modules(self.next_version_id, after_migration=True)
        self.auto_install_modules(self.next_version_id)
        # self.sql_fixes(self.env["openupgrader.config"].search([
        #     ("odoo_version_id", "=", to_version_id.name)]))
        if self.next_version_id.name == "10.0":
            self.start_odoo(self.next_version_id)
            self.remove_modules("upgrade")
            self.remove_modules()
            self.install_uninstall_module("l10n_it_intrastat")
            self.button_stop_odoo()
        self.dump_database(self.next_version_id.name)
        # if self.filestore:
        #     self.dump_filestore(to_version_id.name)
        logger.info(
            _(
                f"Migration done from version {self.current_version_id.name} "
                f"to version {self.next_version_id.name}"
            )
        )
        # self.disable_cron() # to be re-enabled manually after all is gone ok
        self.current_version_id = self.next_version_id
        self.next_version_id = self.env["odoo.version"].search(
            [("name", "=", str(float(self.current_version_id.name) + 1))]
        )
        logger.info(_(f"Set next version to {self.next_version_id}"))
        self.state = "done"

    def button_refresh_state(self):
        for version in [self.current_version_id, self.next_version_id]:
            venv_path = os.path.join(self.folder, f"openupgrade{version.name}")
            migration_log_path = os.path.join(venv_path, "migration.log")
            if os.path.isfile(migration_log_path):
                with open(migration_log_path) as file:
                    contents = file.read()
                    if version == self.current_version_id:
                        self.state = "restoring_db"
                        if "CRITICAL" in contents:
                            self.state = "restore_failed"
                        elif "Initiating shutdown" in contents:
                            self.state = "db_restored"
                    if version == self.next_version_id:
                        self.state = "migrating"
                        if "CRITICAL" in contents:
                            self.state = "failed"
                        elif "Initiating shutdown" in contents:
                            self.state = "db_migrated"

    def sql_fixes(self, recipe):
        for part in recipe:
            bash_commands = part.get("sql_commands", [])
            for bash_command in bash_commands:
                command = [
                    "psql -p %s -d %s -c '%s'"
                    % (
                        self.db_port,
                        f"{self.env.cr.dbname}_migrate",
                        bash_command,
                    )
                ]
                Popen(command, shell=True).wait()
            bash_update_commands = part.get("sql_update_commands", [])
            if bash_update_commands:
                for bash_update_command in bash_update_commands:
                    upd_command = [
                        'psql -p %s -d %s -c "%s"'
                        % (
                            self.db_port,
                            f"{self.env.cr.dbname}_migrate",
                            bash_update_command,
                        )
                    ]
                    Popen(upd_command, shell=True).wait()

    def post_migration(self, version):
        # re-enable mail servers and clean db
        self.disable_mail(disable=False)
        # self.database_cleanup(version)

    def install_repo(self, repo_name, repo_url, repo_version, version_name, venv_path):
        repo_path = os.path.join(
            self.folder, f"openupgrade{version_name}", "repos", repo_name
        )
        if not os.path.isdir(repo_path):
            # todo private repos need credentials
            Popen(
                [
                    f"git clone --single-branch -b {repo_version} {repo_url} --depth 1 "
                    f"{repo_path}"
                ],
                shell=True,
            ).wait()
        Popen(
            ["git pull --rebase"],
            cwd=repo_path,
            shell=True,
        ).wait()
        # copy modules to create a unique addons path, unless it's odoo repo
        if repo_name == "odoo":
            return
        for _root, dirs, _files in os.walk(repo_path):
            for d in dirs:
                if d not in [".git", "setup"]:
                    Popen(
                        [
                            f"cp -rf {repo_path}/{d} "
                            f"{os.path.join(venv_path, 'addons-extra')}"
                        ],
                        shell=True,
                    ).wait()
            break

    def auto_install_modules(self, version):
        self.start_odoo(version)
        odoo_client = self.odoo_connect()
        module_obj = odoo_client.env["ir.module.module"]
        if version.name == "12.0":
            self.remove_modules("upgrade")
        openupgrader_config = self.env["openupgrader.config"].search(
            [("odoo_version_id.id", "=", version.id)]
        )
        for module in openupgrader_config.module_auto_install_ids:
            module_to_check = module.name
            module_to_install = module.module_to_install_name
            if module_obj.search(
                [("name", "=", module_to_check), ("state", "=", "installed")]
            ):
                odoo_client.env.install(module_to_install)
        self.button_stop_odoo()

    def uninstall_modules(self, version, before_migration=False, after_migration=False):
        self.start_odoo(version)
        if version.name == "12.0":
            self.remove_modules("upgrade")
        openupgrader_config = self.env["openupgrader.config"].search(
            [("odoo_version_id.id", "=", version.id)]
        )
        if after_migration:
            for module in openupgrader_config.module_to_uninstall_after_migration_ids:
                self.install_uninstall_module(module.name, install=False)
        if before_migration:
            for module in openupgrader_config.module_to_uninstall_before_migration_ids:
                self.install_uninstall_module(module.name, install=False)
        self.button_stop_odoo()

    def delete_old_modules(self, version):
        openupgrader_config = self.env["openupgrader.config"].search(
            [("odoo_version_id.id", "=", version.id)]
        )
        if openupgrader_config.module_to_delete_after_migration_ids:
            odoo_client = self.odoo_connect()
            module_obj = odoo_client.env["ir.module.module"]
            for module in openupgrader_config.module_to_delete_after_migration_ids:
                module = module_obj.search([("name", "=", module)])
                if module:
                    module.unlink()
            self.button_stop_odoo()

    def remove_modules(self, module_state=""):
        if module_state == "upgrade":
            state = [
                "to upgrade",
            ]
        else:
            state = ["to remove", "to install"]
        odoo_client = self.odoo_connect()
        module_obj = odoo_client.env["ir.module.module"]
        modules = module_obj.browse(
            module_obj.search([("state", "in", state)])
        )
        msg_modules = ""
        msg_modules_after = ""
        if modules:
            msg_modules = str([x.name for x in modules])
        for module in modules:
            module.button_uninstall_cancel()
        modules_after = module_obj.browse(
            module_obj.search([("state", "=", "to upgrade")])
        )
        if modules_after:
            msg_modules_after = str([x.name for x in modules_after])
        logger.info(_("Modules: %s" % msg_modules))
        logger.info(_("Modules after: %s" % msg_modules_after))

    @staticmethod
    def uninst(module_to_unistall_id, success):
        try:
            module_to_unistall_id.button_immediate_uninstall()
            module_to_unistall_id.unlink()
            logger.info(_("Module %s uninstalled" % module_to_unistall_id.name))
            success = 5
        except Exception as e:
            logger.info(
                _(
                    "Module %s not uninstalled for %s, trying %s/%s times."
                    % (
                        module_to_unistall_id.name,
                        str(e).replace("\n", ""),
                        success + 1,
                        5,
                    )
                )
            )
            time.sleep(10)
            success += 1
        return success

    def install_uninstall_module(self, module_name, install=True):
        odoo_client = self.odoo_connect()
        module_obj = odoo_client.env["ir.module.module"]
        to_remove_modules = module_obj.search([("state", "=", "to remove")])
        for module_to_remove_id in to_remove_modules:
            module_obj.browse(module_to_remove_id).button_uninstall_cancel()
        module_ids = module_obj.search([("name", "=", module_name)])
        if module_ids:
            modules = module_obj.browse(module_ids)
            for module in modules:
                if install:
                    odoo_client.env.install(module_name)
                elif (
                    module.state in ["installed", "to upgrade", "uninstallable"]
                ):
                    res = 0
                    while res < 5:
                        res = self.uninst(module, res)
        else:
            logger.info(_("Module %s not found" % module_name))
