import io
import logging
import os
import re
import shutil
import signal
import ssl
import sys
import time
from pathlib import Path
from subprocess import PIPE, Popen
from urllib.request import HTTPSHandler

import odoorpc
import psutil
from odoorpc.rpc import CookieJar, HTTPCookieProcessor, build_opener

import odoo
from odoo import _, api, fields, models, registry
from odoo.exceptions import UserError
from odoo.modules import get_module_resource
from odoo.tools import config
from odoo.tools.safe_eval import safe_eval

from .openupgrader_config import _get_env_for_subprocess

logger = logging.getLogger(__name__)


class OpenupgraderMigration(models.Model):
    _name = "openupgrader.migration"
    _description = "OpenUpgrader Migration"
    _rec_name = "db_name"

    db_name = fields.Char(
        string="Database name", readonly=True, default=lambda self: self.env.cr.dbname
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
        comodel_name="openupgrader.config",
        string="From version",
    )
    to_version_id = fields.Many2one(
        comodel_name="openupgrader.config",
        string="To version",
    )
    current_version_id = fields.Many2one(
        comodel_name="openupgrader.config",
        string="Current migrated version",
    )
    next_version_id = fields.Many2one(
        comodel_name="openupgrader.config",
        string="Next version to be migrated",
    )
    is_migration_done = fields.Boolean(
        compute="_compute_is_migration_done",
        store=True,
    )
    migrate_filestore = fields.Boolean(default=True)
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
            ("restore_failed", "Restore failed"),
            ("restored", "Restored"),
            ("updated", "Updated"),
            ("ready_for_migration", "Ready for migration"),
            ("failed", "Failed"),
            ("migrated", "Migrated"),
            ("done", "Done"),
        ],
        string="Migration state",
        readonly=True,
        default="draft",
    )
    update_error_log = fields.Text(string="Update errors in log")
    update_warning_log = fields.Text(string="Update warnings in log")
    migration_warning_log = fields.Text(string="Migration warnings in log")
    migration_error_log = fields.Text(string="Migration errors in log")

    @api.depends("current_version_id", "to_version_id")
    def _compute_is_migration_done(self):
        for record in self:
            record.is_migration_done = record.current_version_id == record.to_version_id

    @staticmethod
    def _get_log_path(folder, version_name, migrate=False):
        log_name = "migrate" if migrate else "update"
        return Path(folder) / f"openupgrade{version_name}" / f"{log_name}.log"

    @staticmethod
    def show_message_odoo_running():
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "name": "OpenUpgrader Message",
            "params": {
                "title": _("Odoo Migration is running!"),
                "message": _("If you want to do this action anyway, force the stop."),
                "type": "info",
                "sticky": True,
            },
        }

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

    def _get_db_connection_variables(self):
        return (
            f"export PGPORT={self.db_port} && "
            f"export PGHOST={self.pg_host or ''} && "
            f"export PGUSER={self.pg_user or ''} && "
            f"export PGPASSWORD={self.pg_password_var or self.pg_password or ''} "
        )

    def button_get_logs(self):
        self.ensure_one()
        self.migration_error_log, self.migration_warning_log = self._get_migration_logs(
            self._get_log_path(self.folder, self.next_version_id.name, migrate=True)
        )
        self.update_error_log, self.update_warning_log = self._get_migration_logs(
            self._get_log_path(self.folder, self.current_version_id.name)
        )

    @staticmethod
    def _get_migration_logs(log_file):
        error_log = " "
        warning_log = " "
        if os.path.isfile(log_file):
            with open(log_file, "r") as f:
                for r in f.readlines():
                    if r and r != "" and "ERROR" in r:
                        error_text = r.split("ERROR")[1]
                        if error_text and error_text not in error_log:
                            error_log += error_text
                    if r and r != "" and "WARNING" in r:
                        warning_text = r.split("WARNING")[1]
                        if warning_text and warning_text not in warning_log:
                            warning_log += warning_text
        return error_log, warning_log

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
                    "verify_ssl could not be established for this "
                    "python version: %s" % sys.version
                )
        if sessions:
            handlers.append(HTTPCookieProcessor(CookieJar()))
        opener = build_opener(*handlers)
        return opener

    def _set_odoorc(self, folder):
        odoorc_path = os.path.join(folder, ".odoorc")
        if not os.path.isfile(odoorc_path):
            odoorc_basic_path = get_module_resource("openupgrader", "data", ".odoorc")
            shutil.copyfile(odoorc_basic_path, odoorc_path)
            if float(self.next_version_id.name) > 15:
                Popen(
                    f"sed -i 's/longpolling/gevent/g' {odoorc_path}",
                    shell=True,
                )
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
        if os.path.isdir(os.path.join(folder, ".venv", "bin")):
            return folder
        return False

    def start_odoo(self, version_id, update=False, migrate=False, extra_command=""):
        """
        :param version_id: Odoo version_id to start (8.0, 9.0, 10.0, ...)
        :param update: if True odoo will be updated with -u all and stopped
        :param migrate: if True odoo will be migrated with --stop
        :param extra_command: command that will be passed after executable
        :return: null
        """
        self.flush_model()
        if update and not odoo.tools.config["test_enable"]:
            self._start_odoo_thread(version_id, update, migrate, extra_command)
        else:
            self._start_odoo(version_id, update, migrate, extra_command)

    def _start_odoo_thread(
        self, version_id, update=False, migrate=False, extra_command=""
    ):
        self.ensure_one()
        with registry(self.env.cr.dbname).cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.uid, self.env.context)
            self_env = self.with_env(new_env).browse(self.id)
            self_env._start_odoo(version_id, update, migrate, extra_command)
            new_cr.commit()
            new_cr.close()

    def _start_odoo(  # noqa C901
        self, version_id, update=False, migrate=False, extra_command=""
    ):
        logger.info(
            f"Starting Odoo with options: version={version_id.name}, update={update}, "
            f"migrate={migrate}, commands={extra_command}"
        )
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
        subprocess_env = _get_env_for_subprocess(folder, version_id.python_version)
        bash_command = (
            f"{subprocess_env['PYTHONPATH']} "
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
            f"--limit-time-cpu=99000 "
            f"--limit-time-real=99000 "
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
        if migrate:
            odoo_log = self._get_log_path(self.folder, version_name, migrate=True)
        else:
            odoo_log = self._get_log_path(self.folder, version_name)
        if update:
            bash_command += "-u all --stop "
        else:
            if not os.path.isfile(odoo_log):
                file_writer = open(odoo_log, "w")
                file_writer.write(f"Start Odoo v. {version_id.name} logs")
                file_writer.close()
            bash_command += f"--logfile={odoo_log} "
        logger.info(
            "Starting Odoo in virtualenv to %s version %s with command %s"
            % (
                "migrate" if migrate else "update",
                version_id.name,
                bash_command,
            )
        )

        with io.open(odoo_log, "wb") as writer, io.open(odoo_log, "rb") as reader:
            process = Popen(
                bash_command,
                cwd=folder,
                stdout=writer if update else PIPE,
                stderr=writer if update else PIPE,
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
                                    logger.info(f"Modules not installed {str(modules)}")
                                    # self.install_pip_module(version_id, modules)
                                except Exception:
                                    logger.info(
                                        "Unable to list modules to install via pip "
                                        "on-the-fly"
                                    )
                        logger.info(out.strip())
                # Read the remaining
                out = reader.read().decode()
                logger.info(out)
            # only updating the process will end automatically
            logger.info(
                "Odoo migration instance v. %s should be updated and stopped."
                % version_name
            )
        if extra_command:
            # extra command presumes Odoo will stop automatically like update - TODO check
            process.wait()
        if not update:
            time.sleep(10)
            # todo study a safer method to check if Odoo is running!
            self.odoo_migrated_state = "running"
            logger.info("Odoo migration instance v. %s is running." % version_name)
        time.sleep(2)

    def _stop_pid(self, pid=False):
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(5)
            except OSError:
                time.sleep(10)
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    logger.info("Error %s in killing pid: %s " % (OSError, pid))
        self.odoo_migrated_state = "stopped"
        logger.info("Odoo migration instance stopped.")

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
        # read odoo log and put in logger
        if self.current_version_id:
            odoo_log = self._get_log_path(self.folder, self.current_version_id.name)
            if os.path.isfile(odoo_log):
                logger.debug("Show log for file %s" % odoo_log)
                file_reader = open(odoo_log, "r")
                lines = file_reader.readlines()
                for line in lines:
                    if line != " ":
                        logger.debug(line)

    def disable_mail(self, disable=False):
        # FIXME: DO VIA PSQL IN MIGRATED DB
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
        migrated_folder = self.get_filestore_path()
        if os.path.isdir(migrated_folder):
            shutil.rmtree(migrated_folder, ignore_errors=True)
        Path(migrated_folder).mkdir(parents=True, exist_ok=True)
        initial_folder = self.get_filestore_initial_path()
        logger.info(
            "Restoring filestore from %s to %s folder."
            % (initial_folder, migrated_folder)
        )
        Popen([f"cp -r * {migrated_folder}"], cwd=initial_folder, shell=True).wait()
        logger.info(
            "Filestore restored from original version %s folder %s to %s."
            % (
                from_version_id.name,
                initial_folder,
                migrated_folder,
            )
        )

    def button_backup_migration(self):
        if not self.current_version_id:
            raise UserError(_("Current version is required!"))
        if not self.current_version_id.db_backup_id:
            self.current_version_id._create_db_backup(folder=self.folder)
        self.current_version_id.db_backup_id.action_backup_migration()

    def get_filestore_path(self):
        # get filestore migrated instance path
        filestore_path = os.path.join(
            self.folder,
            "data_dir",
            "filestore",
            f"{self.env.cr.dbname}_migrate",
        )
        return filestore_path

    def get_filestore_initial_path(self):
        # get the filestore from running production instance of Odoo
        initial_path = os.path.join(
            "/",
            *[x for x in config.filestore(self.env.cr.dbname).split("/") if x != ""],
        )
        return initial_path

    def dump_database(self, version_name, migrated=False):
        logger.info(
            f"Dumping {'migrated' if migrated else 'original'} database for version "
            f"{version_name}"
        )
        destination_path = os.path.join(self.folder, f"database.{version_name}.sql")
        conn_vars = self._get_db_connection_variables()
        process = Popen(
            [
                f"{conn_vars} && pg_dump {self.pg_options or ''} -Fc -O "
                f"{self.env.cr.dbname}{'_migrate' if migrated else ''} "
                f"> {destination_path}"
            ],
            shell=True,
        )
        process.wait()
        logger.info(
            f"{'Migrated' if migrated else 'Original'} database dumped for version "
            f"{version_name}"
        )
        return destination_path

    def restore_db(self, version_id=False):
        self.button_stop_odoo()
        conn_vars = self._get_db_connection_variables()
        process = Popen(
            [
                f"{conn_vars} && dropdb --if-exists {self.env.cr.dbname}_migrate",
            ],
            shell=True,
            stderr=PIPE,
            stdout=PIPE,
        )
        process.wait()
        error = process.stderr.readlines()
        errors = [e.decode().lower() for e in error if "error" in e.decode()]
        if errors:
            raise UserError("\n".join(e for e in errors))
        Popen(
            [
                f"{conn_vars} && createdb {self.env.cr.dbname}_migrate",
            ],
            shell=True,
        ).wait()
        # Dump and restore db by sql file as it's the faster way to do it
        dump_file_sql = os.path.join(
            self.folder, f"database.{self.current_version_id.name}.sql"
        )
        if not version_id:
            # this is not a restore done by hand from the user, so create a new dump
            dump_file_sql = self.dump_database(self.from_version_id.name)
        if not os.path.isfile(dump_file_sql):
            raise UserError(_("Dump sql file %s not found!") % dump_file_sql)
        logger.info(
            "Restoring %(kind_db)s db %(from_db)s %(db)s_migrate."
            % dict(
                db=self.env.cr.dbname,
                from_db="" if version_id else f"from {self.env.cr.dbname} to",
                kind_db="last dumped" if version_id else "original",
            )
        )
        Popen(
            [
                f"{conn_vars} && pg_restore {self.pg_options or ''} "
                f"-d {self.env.cr.dbname}_migrate {dump_file_sql}"
            ],
            shell=True,
        ).wait()
        if not version_id:
            # this is not a restore done by hand from the user, so delete dump
            os.unlink(dump_file_sql)

    def button_clean_logs(self):
        self.migration_error_log = " "
        self.migration_warning_log = " "
        self.update_error_log = " "
        self.update_warning_log = " "
        for version_id in self.env["openupgrader.config"].search([]):
            for log_name in ["migrate", "update"]:
                log_path = (
                    Path(self.folder)
                    / f"openupgrade{version_id.name}"
                    / f"{log_name}.log"
                )
                if os.path.isfile(log_path):
                    os.unlink(log_path)

    def button_dump_current_database(self):
        self.dump_database(self.current_version_id.name, migrated=True)

    def button_restore_last_database(self):
        self._restore(force=True)

    def button_restore(self):
        for version_id in self.env["openupgrader.config"].search([]):
            folder = self.check_venv(version_id.name)
            if not folder:
                raise UserError(
                    _("Missing env for version %s! Create in Odoo Version menu.")
                    % version_id.name
                )
        self._restore()

    def _restore(self, force=False):
        self.ensure_one()
        self.button_refresh_odoo_migrated_state()
        if self.odoo_migrated_state == "running":
            self.show_message_odoo_running()
        self.migration_error_log = " "
        if not self.current_version_id:
            self.current_version_id = self.from_version_id
        if not self.next_version_id:
            self.next_version_id = self.env["openupgrader.config"].search(
                [("name", "=", str(float(self.current_version_id.name) + 1))]
            )
        if self.from_version_id == self.current_version_id:
            # restore is needed only when we migrate the first version, after the db is
            # already present in the postgresql cluster
            if self.migrate_filestore:
                self.restore_filestore(self.current_version_id, self.current_version_id)
            self.restore_db()
        if force:
            self.restore_db(self.current_version_id)
        self.state = "restored"

    def button_update_current_version(self):
        self.ensure_one()
        self.button_refresh_odoo_migrated_state()
        if self.odoo_migrated_state == "running":
            self.show_message_odoo_running()
        if self.state == "updated":
            return {
                "type": "ir.actions.client",
                "tag": "reload",
            }
        self.disable_mail(disable=True)
        # odoo service is stopped automatically at the end of the update process
        return self.start_odoo(self.current_version_id, update=True)

    def button_prepare_for_migration(self):
        self.ensure_one()
        self.button_refresh_odoo_migrated_state()
        # do pre upgrade stuff
        if self.odoo_migrated_state == "running":
            self.show_message_odoo_running()
        if self.from_version_id == self.current_version_id:
            # these actions are needed for the initial version only
            self.set_cron_state_to(active=False)
            # self.disable_mail(disable=True)
        self.sql_fixes(self.current_version_id.sql_before_migration_command_ids)
        self.uninstall_modules(self.current_version_id, before_migration=True)
        self.delete_old_modules(self.current_version_id)
        self.state = "ready_for_migration"

    def set_cron_state_to(self, active):
        conn_vars = self._get_db_connection_variables()
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
                [f'{conn_vars} && psql -d {self.env.cr.dbname}_migrate -c "{sql}"'],
                shell=True,
            )

    def button_draft(self):
        self.button_refresh_odoo_migrated_state()
        if self.odoo_migrated_state == "running":
            self.show_message_odoo_running()
        for version_id in [self.current_version_id, self.next_version_id]:
            sql_file_path = os.path.join(
                self.folder,
                f"database.{version_id.name}.sql",
            )
            if os.path.isfile(sql_file_path):
                os.remove(sql_file_path)
        self.current_version_id = False
        self.next_version_id = False
        self.disabled_cron_ids = False
        self.button_clean_logs()
        self.state = "draft"

    def button_do_migration(self):
        self.button_refresh_odoo_migrated_state()
        if self.odoo_migrated_state == "running":
            self.show_message_odoo_running()
        if self.state == "migrated":
            logger.info(
                "Migration done from version %s to version %s"
                % (self.current_version_id.name, self.next_version_id.name)
            )
            # do after migration stuff
            self.uninstall_modules(self.next_version_id, after_migration=True)
            self.auto_install_modules(self.next_version_id)
            self.sql_fixes(self.current_version_id.sql_after_migration_command_ids)
            # if self.next_version_id.name == "10.0":
            #     self.remove_modules(self.next_version_id, "upgrade")
            #     self.remove_modules(self.next_version_id)
            #     self.install_uninstall_module("l10n_it_intrastat")
            # move version to the next step
            self.current_version_id = self.next_version_id
            self.next_version_id = self.env["openupgrader.config"].search(
                [("name", "=", str(float(self.current_version_id.name) + 1))]
            )
            if self.is_migration_done:
                self.set_cron_state_to(active=True)
                logger.info(
                    "Migration completed from version %s to version %s"
                    % (self.from_version_id.name, self.to_version_id.name)
                )
                self.state = "done"
            return {
                "type": "ir.actions.client",
                "tag": "reload",
            }
        return self.start_odoo(self.next_version_id, update=True, migrate=True)

    def button_refresh_odoo_migrated_state(self):
        self.odoo_migrated_state = self._get_odoo_migrated_state()

    def _get_odoo_migrated_state(self):  # noqa C901
        odoo_migrated_state = "stopped"
        odoo_pids = self._get_odoo_pids()
        for odoo_pid in odoo_pids:
            if psutil.pid_exists(odoo_pid):
                odoo_migrated_state = "running"

        # get the state of the migration from log upgrade (or update?) file
        # todo put in a cron job?
        if self.next_version_id:
            odoo_migrate_log = self._get_log_path(
                self.folder, self.next_version_id.name, migrate=True
            )
            odoo_log = self._get_log_path(
                self.folder,
                self.next_version_id.name,
            )
            if os.path.isfile(odoo_migrate_log):
                with open(odoo_migrate_log, "r") as f:
                    for log_line in f.readlines():
                        if "CRITICAL" in log_line:
                            self.state = "failed"
                            break
                        if "Modules loaded" in log_line:
                            self.state = "migrated"
                            break
            elif os.path.isfile(odoo_log):
                with open(odoo_log, "r") as f:
                    for log_line in f.readlines():
                        if "CRITICAL" in log_line:
                            self.state = "restore_failed"
                            break
                        if "Modules loaded" in log_line:
                            self.state = "updated"
                            break
        return odoo_migrated_state

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
            f"git fetch origin {version_name} "
            f"&& git reset --hard origin/{version_name}",
            cwd=repo_path,
            shell=True,
        ).wait()

    def auto_install_modules(self, version_id):
        self.start_odoo(version_id)
        odoo_client = self.odoo_connect()
        module_obj = odoo_client.env["ir.module.module"]
        if version_id.name == "12.0":
            self.remove_modules(version_id, "upgrade")
        for module in version_id.module_auto_install_ids:
            module_to_check = module.name
            module_to_install = module.module_to_install_name
            if module_obj.search(
                [("name", "=", module_to_check), ("state", "=", "installed")]
            ):
                module_toinstall_id = module_obj.search(
                    [("name", "=", module_to_install)]
                )
                if module_toinstall_id:
                    module_obj.browse(module_toinstall_id).button_immediate_install()
        self.button_stop_odoo()

    def uninstall_modules(
        self, version_id, before_migration=False, after_migration=False
    ):
        self.start_odoo(version_id)
        if version_id.name == "12.0":
            self.remove_modules(version_id, "upgrade")
        if after_migration:
            for module in version_id.module_to_uninstall_after_migration_ids:
                self.install_uninstall_module(module.name, install=False)
        if before_migration:
            for module in version_id.module_to_uninstall_before_migration_ids:
                self.install_uninstall_module(module.name, install=False)
        self.button_stop_odoo()

    def delete_old_modules(self, version_id):
        if version_id.module_to_delete_after_migration_ids:
            self.start_odoo(version_id)
            odoo_client = self.odoo_connect()
            module_obj = odoo_client.env["ir.module.module"]
            for module in version_id.module_to_delete_after_migration_ids:
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
        logger.info("Modules present before the removal: %s" % msg_modules)
        logger.info("Modules present after the removal: %s" % msg_modules_after)

    @staticmethod
    def uninst(module_to_unistall_id, success):
        try:
            module_to_unistall_id.button_immediate_uninstall()
            module_to_unistall_id.unlink()
            logger.info("Module %s uninstalled" % module_to_unistall_id.name)
            success = 5
        except Exception as e:
            logger.info(
                "Module %s not uninstalled for %s, trying %s/%s times."
                % (
                    module_to_unistall_id.name,
                    str(e).replace("\n", ""),
                    success + 1,
                    5,
                )
            )
            time.sleep(10)
            success += 1
        return success

    def install_pip_modules(self, version_id, module_names):
        logger.info("Installing Odoo modules with pip: %s" % str(module_names))
        self.ensure_one()
        odoo_version_int = int(version_id.name.split(".")[0])
        venv_path = os.path.join(self.folder, f"openupgrade{version_id.name}")
        subprocess_env = _get_env_for_subprocess(venv_path, version_id.python_version)
        # try to install with pip and log error if it fails
        commands = [
            "uv pip install --pre odoo{version_name}-addon-{name}".format(
                version_name=odoo_version_int if odoo_version_int < 15 else "",
                name=name,
            )
            for name in module_names
        ]
        for command in commands:
            process = Popen(
                command,
                cwd=venv_path,
                shell=True,
                stderr=PIPE,
                stdout=PIPE,
                env=subprocess_env,
            )
            error = process.stderr.readlines()
            errors = [e.decode().lower() for e in error if "error" in e.decode()]
            if errors:
                logger.info(
                    "Some modules not found with pip installer: %s"
                    % "\n".join(e for e in errors)
                )

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
                    module.button_immediate_install()
                elif module.state in ["installed", "to upgrade", "uninstallable"]:
                    res = 0
                    while res < 5:
                        res = self.uninst(module, res)
            return modules
        else:
            logger.info("Module %s not found" % module_name)
            return False
