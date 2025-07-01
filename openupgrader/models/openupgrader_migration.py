import io
import logging
import os
import re
import shutil
import signal
import ssl
import sys
import threading
import time
from pathlib import Path
from subprocess import PIPE, Popen
from urllib.request import HTTPSHandler

import odoorpc
import psutil
from odoorpc.rpc import CookieJar, HTTPCookieProcessor, build_opener

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.modules import get_module_resource
from odoo.tools import config
from odoo.tools.safe_eval import safe_eval

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
        string="Postgres Host", default=lambda self: config.get("db_host", "db")
    )
    pg_options = fields.Char(
        string="Postgres options",
        help="Custom options for the postgres connection, like '--cluster 14/main'",
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
        string="Database port",
        default=lambda self: config.get("db_port") != "False"
        and config.get("db_port")
        or "5432",
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
    migrate_filestore = fields.Boolean(string="Migrate Filestore", default=True)
    openupgrade_repo = fields.Char(
        string="OpenUpgrade Repository",
        default="https://github.com/OCA/OpenUpgrade.git",
    )
    odoo_migrated_state = fields.Selection(
        selection=[
            ("running", "Running"),
            ("stopped", "Stopped"),
        ],
        string="Odoo migrating instance state",
        help="Migrated Odo instance is running or stopped",
        default="stopped",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("created_venv", "Venv created"),
            ("restoring", "Restoring"),
            ("restore_failed", "Restore failed"),
            ("restored", "Restored"),
            ("updating", "Updating"),
            ("updated", "Updated"),
            ("ready_for_migration", "Ready for migration"),
            ("migrating", "Migrating"),
            ("failed", "Failed"),
            ("migrated", "Migrated"),
            ("done", "Done"),
        ],
        string="Migration state",
        readonly=True,
        default="draft",
    )
    odoo_error_log = fields.Text(string="Odoo error log")
    migration_error_log = fields.Text(string="Migration error log")

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
            try:
                client.login(
                    db=f"{self.db_name}_migrate",
                    login=self.db_user,
                    password=self.db_password,
                )
                time.sleep(5)
                return client
            except Exception as e:
                logger.error("Connection to Odoo failed for %s!" % str(e))
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
                        "verify_ssl could not be established for this "
                        "python version: %s"
                    )
                    % sys.version
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
            # todo move disabled modules to configuration
            not_auto_install_list = [
                "partner_autocomplete",
                "iap",
                "mail_bot",
                "account_edi",
                "account_edi_facturx",
                "account_edi_ubl",
                "l10n_it_stock_ddt",
                "l10n_it_edi",
            ]
            mod_not_install = (
                f"modules_auto_install_disabled = {','.join(not_auto_install_list)}"
            )
            Popen(
                [f"echo {mod_not_install} >> {odoorc_path}"],
                shell=True,
            ).wait()

    def button_start_odoo(self):
        self.start_odoo(version_id=self.current_version_id)

    def check_venv(self, version_name):
        folder = os.path.join(self.folder, f"openupgrade{version_name}")
        if os.path.isdir(os.path.join(folder, "bin")):
            return folder
        return False

    def start_odoo(self, version_id, update=False, extra_command=""):
        """
        :param version_id: Odoo version_id to start (8.0, 9.0, 10.0, ...)
        :param update: if True odoo will be updated with -u all and stopped
        :param extra_command: command that will be passed after executable
        :return: null # todo return odoo client if not updating?
        """
        if version_id != self.from_version_id:
            self.state = "migrating"
        else:
            self.state = "updating"
        if update and not config["test_enable"]:
            thread_odoo = threading.Thread(
                target=self._start_odoo_thread, args=(version_id, update, extra_command)
            )
            thread_odoo.start()
        else:
            state = self._start_odoo(version_id, update, extra_command)
            if state and state == "migrated":
                self._action_done()

    def _start_odoo_thread(self, version_id, update=False, extra_command=""):
        state = False
        with api.Environment.manage():
            # with odoo.registry(self.env.cr.dbname).cursor() as new_cr:
            new_cr = self.pool.cursor()
            self = self.with_env(self.env(cr=new_cr))
            version_id = version_id.with_env(self.env)
            state = self._start_odoo(version_id, update, extra_command)
            new_cr.close()
        if state and state == "migrated":
            self._action_done()

    def _start_odoo(self, version_id, update=False, extra_command=""):  # noqa C901
        state = False
        version_name = version_id.name
        version_float = float(version_name)
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
        if version_float > 11:
            load = "base,web"
        if version_float > 13:
            load += ",openupgrade_framework,module_change_auto_install"
        executable = (
            f"{folder}/odoo/openerp-server"
            if version_float < 10
            else f"{folder}/odoo/odoo-bin"
            if version_float < 14
            else f"{folder}/repos/odoo/odoo-bin"
        )
        self._set_odoorc(folder)
        addons_path = f"{folder}/repos/odoo/addons"
        if version_float < 14:
            addons_path = f"{folder}/odoo/addons"
        extra_addons_path = f",{folder}/repos/odoo/odoo/addons,{folder}/odoo"
        if 9 < version_float < 14:
            extra_addons_path = f",{folder}/odoo/odoo/addons"
        for remote_repo in (
            version_id.openupgrader_repo_ids.mapped("remote_repo_ids")
            .filtered(lambda x: x.name != "odoo")
            .mapped("name")
        ):
            # add to addons_path all repos
            extra_addons_path += f",{os.path.join(folder, 'repos', remote_repo)}"
        bash_command = (
            f"{executable} "
            f"-c {folder}/.odoorc "
            f"--addons-path={addons_path}"
            f"{extra_addons_path}"
            f" {extra_command} "
            f"--db_user={self.pg_user} "
            f"--db_password={self.pg_password_var or self.pg_password or ''} "
            f"--db_port={self.db_port} "
            f"--db_host={self.pg_host or ''} "
            f"--xmlrpc-port={self.xmlrpc_port} "
            f"--limit-time-cpu=16000 "
            f"--limit-time-real=32000 "
            f"--limit-memory-soft=4147483648 "
            f"--limit-memory-hard=4679107584 "
            f"--{'longpolling' if version_float < 16 else 'gevent'}-port=8072 "
            f"--load={load} "
            f"-d {self.env.cr.dbname}_migrate "
        )
        if version_name != "7.0":
            data_dir = os.path.join(self.folder, "data_dir")
            if not os.path.isdir(data_dir):
                os.makedirs(data_dir)
            bash_command += f"--data-dir={data_dir} "
        if update:
            bash_command += "-u all --stop "
        subprocess_env = _get_env_for_subprocess(folder, version_id.python_version)
        logger.info(
            "Starting Odoo in virtualenv for migration with command %s" % bash_command
        )

        filename = "odoo_migration.log"
        migration_errors = []
        with io.open(filename, "wb") as writer, io.open(filename, "rb") as reader:
            process = Popen(
                bash_command,
                cwd=folder,
                stdout=writer,
                stderr=writer,
                env=subprocess_env,
                shell=True,
            )
            if update:  # if not updating, this part will recurse infinitely
                while process.poll() is None:
                    out = reader.read().decode()
                    if out and out != " ":
                        if "Some modules have inconsistent states" in out:
                            # try to install missing module with pip on-the-fly
                            match = re.search(r"\[.*\]", out)
                            if match:
                                try:
                                    modules = safe_eval(match[0])
                                except Exception:
                                    logger.info(
                                        "Unable to list modules to install via pip "
                                        "on-the-fly"
                                    )
                            self.install_missing_modules(version_id, modules)
                            migration_errors.append(out)
                        logger.info(out.strip())
                        if "CRITICAL" in out:
                            if version_id == self.current_version_id:
                                state = "restore_failed"
                            elif version_id == self.next_version_id:
                                state = "failed"
                        if "ERROR" in out:
                            migration_errors.append(out)
                        if "WARNING" in out:
                            migration_errors.append(out)
                        if "Modules loaded" in out:
                            if (
                                version_id == self.current_version_id
                                and self.state != "ready_for_migration"
                            ):
                                state = "restored"
                            elif version_id == self.from_version_id:
                                state = "updated"
                            else:
                                state = "migrated"
                # Read the remaining
                out = reader.read().decode()
                logger.info(out)
        self.odoo_pid = process.pid
        if update:
            # only updating the process will end automatically
            logger.info(
                "Odoo migration instance v. %s should be updated and stopped."
                % version_name
            )
            self.migration_error_log += "\n".join(migration_errors)
        if not update and not extra_command:
            time.sleep(5)
            # todo study a safer method to check if Odoo is running!
            self.odoo_migrated_state = "running"
            logger.info("Odoo migration instance v. %s is running." % version_name)
        time.sleep(2)
        return state

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
        logger.info("Odoo migration instances stopped.")

    def _get_odoo_pids(self):
        pids = []
        for command in [
            f"pgrep -a python | grep {self.env.cr.dbname}_migrate",
            f"pgrep -a postgres | grep {self.env.cr.dbname}_migrate",
        ]:
            process = Popen(
                command,
                shell=True,
                stdout=PIPE,
            )
            has_stdout = True
            while has_stdout:
                one_line_output = process.stdout.readline()
                if one_line_output:
                    pids.append(int(one_line_output.split()[0]))
                else:
                    has_stdout = False
        return pids

    def button_stop_odoo(self):
        pids = self._get_odoo_pids()
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

    def restore_filestore(self, from_version_id, to_version_id):
        # restore filestore always from initial folder to default migration folder
        filestore_torestore_path = self.get_filestore_path(
            to_version_id.name, migration_folder=True
        )
        if os.path.isdir(filestore_torestore_path):
            shutil.rmtree(filestore_torestore_path, ignore_errors=True)
        Path(filestore_torestore_path).mkdir(parents=True, exist_ok=True)
        initial_folder = self.get_filestore_path(from_version_id.name)
        logger.info(
            "Restoring filestore from %s to %s folder."
            % (initial_folder, filestore_torestore_path)
        )
        Popen([f"cp -r {initial_folder}/* {filestore_torestore_path}"], shell=True)
        logger.info(
            "Filestore restored from original version %s." % from_version_id.name
        )

    def button_backup_migration(self):
        if not self.current_version_id:
            raise UserError(_("Current version is required!"))
        if not self.current_version_id.db_backup_id:
            self.current_version_id._create_db_backup(folder=self.folder)
        self.current_version_id.db_backup_id.action_backup_migration()

    def get_filestore_path(self, version_name, migration_folder=False):
        # get filestore migrated default path
        filestore_path = os.path.join(
            self.folder,
            "data_dir",
            "filestore",
            f"{self.env.cr.dbname}_migrate",
        )
        if version_name == self.from_version_id.name and not migration_folder:
            # get the filestore from running production instance of Odoo if initial one
            initial_path = os.path.join(
                "/",
                *[
                    x
                    for x in config.filestore(self.env.cr.dbname).split("/")
                    if x != ""
                ],
            )
            if os.path.exists(initial_path):
                filestore_path = initial_path
        return filestore_path

    def dump_database(self, version_name):
        logger.info("Dumping database for version %s" % version_name)
        destination_path = os.path.join(self.folder, f"database.{version_name}.sql")
        connection_string = (
            f"postgresql://{self.pg_user}:"
            f"{self.pg_password_var or self.pg_password or ''}@"
            f"{self.pg_host or ''}:{self.db_port}/{self.env.cr.dbname}"
        )
        process = Popen(
            [
                f"pg_dump {self.pg_options or ''} -Fc -O {connection_string}"
                f"> {destination_path}"
            ],
            shell=True,
        )
        process.wait()
        logger.info("Database dumped for version %s" % version_name)

    def restore(self):
        self.button_stop_odoo()
        process = Popen(
            [
                f"export PGPORT={self.db_port} && "
                f"export PGHOST={self.pg_host or ''} && "
                f"export PGUSER={self.pg_user or ''} && "
                f"export PGPASSWORD={self.pg_password_var or self.pg_password or ''} "
                f"&& dropdb --if-exists {self.env.cr.dbname}_migrate",
            ],
            shell=True,
            stderr=PIPE,
            stdout=PIPE,
        )
        error = process.stderr.readlines()
        errors = [str(e).lower() for e in error if "error" in str(e)]
        if errors:
            raise UserError("\n".join(e for e in errors))
        Popen(
            [
                f"export PGPORT={self.db_port} && "
                f"export PGHOST={self.pg_host or ''} && "
                f"export PGUSER={self.pg_user or ''} && "
                f"export PGPASSWORD={self.pg_password_var or self.pg_password or ''} "
                f"&& createdb {self.env.cr.dbname}_migrate",
            ],
            shell=True,
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

    def button_clean_migration_error_log(self):
        self.migration_error_log = " "

    def button_restore_update(self):
        self.button_restore()
        self.button_update_current_version()

    def button_restore(self):
        self.ensure_one()
        self.migration_error_log = " "
        if not self.next_version_id:
            self.current_version_id = self.from_version_id
        self.next_version_id = self.env["odoo.version"].search(
            [("name", "=", str(float(self.current_version_id.name) + 1))]
        )
        if self.from_version_id == self.current_version_id:
            # restore is needed only when we migrate the first version, then the db is
            # already present
            self.dump_database(self.current_version_id.name)
            if self.migrate_filestore:
                self.restore_filestore(self.current_version_id, self.current_version_id)
            self.restore()
        self.state = "restored"

    def button_update_current_version(self):
        self.ensure_one()
        self.disable_mail(disable=True)
        # n.b. when updating, at the end odoo service is stopped automatically
        self.start_odoo(self.current_version_id, update=True)
        self.state = "updated"

    def button_prepare_for_migration(self):
        if self.from_version_id == self.current_version_id:
            # these actions are needed for the initial version only
            self.set_cron_state_to(active=False)
            if self.migrate_filestore:
                self.restore_filestore(
                    from_version_id=self.current_version_id,
                    to_version_id=self.next_version_id,
                )
            self.disable_mail(disable=True)
        self.sql_fixes(
            self.current_version_id.openupgrader_config_ids.sql_before_migration_command_ids
        )
        self.uninstall_modules(self.current_version_id, before_migration=True)
        self.delete_old_modules(self.current_version_id)
        self.state = "ready_for_migration"

    def set_cron_state_to(self, active):
        if active:
            # re-enable cron after the migration
            ir_cron_ids = self.disabled_cron_ids
        else:
            # disable cron before migrating the instance
            ir_cron_ids = self.env["ir.cron"].search([])
            self.disabled_cron_ids = ir_cron_ids
        if ir_cron_ids:
            sql = (
                f"UPDATE ir_cron SET active = {'true' if active else 'false'} "
                f"WHERE id in {tuple(ir_cron_ids.ids)};"
            )
            Popen(
                [
                    f"psql -p {self.db_port} -d "
                    f'{self.env.cr.dbname}_migrate -c "{sql}"'
                ],
                shell=True,
            )

    def button_draft(self):
        for version_id in [self.current_version_id, self.next_version_id]:
            sql_file_path = os.path.join(
                self.folder,
                f"database.{version_id.name}.sql",
            )
            if os.path.isfile(sql_file_path):
                os.remove(sql_file_path)
        self.current_version_id = False
        self.next_version_id = False
        self.odoo_error_log = False
        self.migration_error_log = False
        self._refresh_odoo_migrated_state()
        self.state = "draft"

    def button_do_migration(self):
        self.start_odoo(self.next_version_id, update=True)

    def _action_done(self):
        self.uninstall_modules(self.next_version_id, after_migration=True)
        self.auto_install_modules(self.next_version_id)
        self.sql_fixes(
            self.current_version_id.openupgrader_config_ids.sql_after_migration_command_ids
        )
        if self.next_version_id.name == "10.0":
            self.remove_modules(self.next_version_id, "upgrade")
            self.remove_modules(self.next_version_id)
            self.install_uninstall_module("l10n_it_intrastat")
        self.dump_database(self.next_version_id.name)
        logger.info(
            _("Migration done from version %s to version %s")
            % (self.current_version_id.name, self.next_version_id.name)
        )
        self.set_cron_state_to(active=True)
        self.current_version_id = self.next_version_id
        self.next_version_id = self.env["odoo.version"].search(
            [("name", "=", str(float(self.current_version_id.name) + 1))]
        )
        logger.info(_("Set next version to %s") % self.next_version_id.name)
        self.state = "done"

    def button_refresh_odoo_migrated_state(self):
        self._refresh_odoo_migrated_state()

    def _refresh_odoo_migrated_state(self):  # noqa C901
        self.odoo_migrated_state = "stopped"
        odoo_pids = self._get_odoo_pids()
        for odoo_pid in odoo_pids:
            if psutil.pid_exists(odoo_pid):
                self.odoo_migrated_state = "running"

    def sql_fixes(self, sql_commands):
        # do not change quote order as it will change the way the sql command is
        # interpreted!
        logger.info("Doing custom sql commands.")
        for sql_command in sql_commands:
            Popen(
                [
                    f"export PGPORT={self.db_port} && "
                    f"export PGHOST={self.pg_host or ''} && "
                    "export "
                    f"PGPASSWORD={self.pg_password_var or self.pg_password or ''} && "
                    f"psql -U {self.pg_user} -d {self.env.cr.dbname}_migrate "
                    f'-c "{sql_command.name}"',
                ],
                shell=True,
            ).wait()

    def post_migration(self, version_id):
        # re-enable mail servers and clean db
        self.disable_mail(disable=False)
        # self.database_cleanup(version_id)

    def install_repo(self, remote_repo, version_name, repo_path=None):
        if repo_path is None:
            repo_path = os.path.join(
                self.folder, f"openupgrade{version_name}", "repos", remote_repo.name
            )
        if not os.path.isdir(repo_path):
            # private repos need token
            repo_url = remote_repo.remote_url
            if repo_url.startswith("git@github.com:"):
                # from git@github.com:username/repo.git
                # to https://username:token@github.com/username/repo.git
                repo_url = repo_url.replace(
                    "git@github.com:",
                    f"https://{remote_repo.github_user}:{remote_repo.github_token}"
                    f"@github.com/",
                )
            Popen(
                [
                    f"git clone --single-branch -b {remote_repo.remote_branch} "
                    f"{repo_url} --depth 1 {repo_path}"
                ],
                shell=True,
            ).wait()
        Popen(
            ["git pull --rebase"],
            cwd=repo_path,
            shell=True,
        ).wait()

    def auto_install_modules(self, version_id):
        self.start_odoo(version_id)
        odoo_client = self.odoo_connect()
        module_obj = odoo_client.env["ir.module.module"]
        if version_id.name == "12.0":
            self.remove_modules(version_id, "upgrade")
        openupgrader_config = self.env["openupgrader.config"].search(
            [("odoo_version_id.id", "=", version_id.id)]
        )
        for module in openupgrader_config.module_auto_install_ids:
            module_to_check = module.name
            module_to_install = module.module_to_install_name
            if module_obj.search(
                [("name", "=", module_to_check), ("state", "=", "installed")]
            ):
                odoo_client.env.install(module_to_install)
        self.button_stop_odoo()

    def uninstall_modules(
        self, version_id, before_migration=False, after_migration=False
    ):
        self.start_odoo(version_id)
        if version_id.name == "12.0":
            self.remove_modules(version_id, "upgrade")
        openupgrader_config = self.env["openupgrader.config"].search(
            [("odoo_version_id.id", "=", version_id.id)]
        )
        if after_migration:
            for module in openupgrader_config.module_to_uninstall_after_migration_ids:
                self.install_uninstall_module(module.name, install=False)
        if before_migration:
            for module in openupgrader_config.module_to_uninstall_before_migration_ids:
                self.install_uninstall_module(module.name, install=False)
        self.button_stop_odoo()

    def delete_old_modules(self, version_id):
        openupgrader_config = self.env["openupgrader.config"].search(
            [("odoo_version_id.id", "=", version_id.id)]
        )
        if openupgrader_config.module_to_delete_after_migration_ids:
            self.start_odoo(version_id)
            odoo_client = self.odoo_connect()
            module_obj = odoo_client.env["ir.module.module"]
            for module in openupgrader_config.module_to_delete_after_migration_ids:
                module = module_obj.search([("name", "=", module)])
                if module:
                    module.unlink()
            self.button_stop_odoo()

    def remove_modules(self, version_id, module_state=""):
        if module_state == "upgrade":
            state = [
                "to upgrade",
            ]
        else:
            state = ["to remove", "to install"]
        odoo_client = self.odoo_connect()
        module_obj = odoo_client.env["ir.module.module"]
        modules = module_obj.browse(module_obj.search([("state", "in", state)]))
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
        logger.info("Modules present before the removal %s" % msg_modules)
        logger.info("Modules present after the removal: %s" % msg_modules_after)

    @staticmethod
    def uninst(module_to_unistall_id, success):
        try:
            module_to_unistall_id.button_immediate_uninstall()
            module_to_unistall_id.unlink()
            logger.info(_("Module %s uninstalled") % module_to_unistall_id.name)
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

    def install_missing_modules(self, version_id, module_names):
        logger.info("Installing missing modules %s with pip." % str(module_names))
        self.ensure_one()
        version_name = version_id.name
        for module_name in module_names:
            venv_path = os.path.join(self.folder, f"openupgrade{version_name}")
            # try to install with pip
            process = Popen(
                [
                    "bin/pip install "
                    f"odoo{version_name.split('.')[0]}-addon-{module_name} "
                ],
                cwd=venv_path,
                shell=True,
            )
            stdout, stderr = process.communicate()
            if stderr:
                logger.info(_("Module %s not found with pip installer.") % module_name)

    def install_uninstall_module(self, module_name, install=True):
        logger.info(
            f"{'Installing' if install else 'Uninstalling'} module %s in Odoo."
            % module_name
        )
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
                elif module.state in ["installed", "to upgrade", "uninstallable"]:
                    res = 0
                    while res < 5:
                        res = self.uninst(module, res)
        else:
            logger.info(_("Module %s not found") % module_name)
