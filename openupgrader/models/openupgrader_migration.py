import io
import json
import logging
import os
import re
import shutil
import signal
import ssl
import sys
import time
from pathlib import Path
from subprocess import PIPE, Popen, run
from urllib.request import HTTPSHandler, Request, urlopen

import odoorpc
import psutil
from odoorpc.rpc import CookieJar, HTTPCookieProcessor, build_opener

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
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
        string="Database name",
        readonly=True,
        default=lambda self: self.env.cr.dbname,
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
        help="Set the environment variable (like $POSTGRES_PASSWORD), "
        "alternative to setting the password directly",
    )
    pg_host = fields.Char(
        string="Postgres Host", default=lambda self: config.get("db_host", "db")
    )
    pg_options = fields.Char(
        string="Postgres options",
        help="Custom options for the postgres connection, " "like '--cluster 14/main'",
    )
    verify_ssl = fields.Boolean()
    address = fields.Char("Odoo URL")
    local = fields.Boolean("Odoo is in local network")
    db_port = fields.Char(
        string="Database port",
        default=lambda self: config.get("db_port") != "False"
        and config.get("db_port")
        or "5432",
    )
    xmlrpc_port = fields.Char(
        string="XmlRpc port",
        help="Set a different port from the current one used, "
        "as this would block the instance.",
        default=lambda self: str(int(config.get("http_port", "8032") + 1)),
    )
    folder = fields.Char(
        default=lambda self: self._default_folder(),
        help="Absolute path for migrated Odoo instance",
        required=True,
    )
    from_config_id = fields.Many2one(
        comodel_name="openupgrader.config",
        string="From version",
    )
    to_config_id = fields.Many2one(
        comodel_name="openupgrader.config",
        string="To version",
    )
    current_config_id = fields.Many2one(
        comodel_name="openupgrader.config",
        string="Current migrated version",
    )
    next_config_id = fields.Many2one(
        comodel_name="openupgrader.config",
        string="Next version to be migrated",
    )
    is_migration_done = fields.Boolean(
        compute="_compute_is_migration_done",
        store=True,
    )
    migrate_filestore = fields.Boolean(default=True)
    dump_each_version_database = fields.Boolean(
        default=True,
        help="If enabled, each migration version will be dumped to a file "
        "with name 'database.{version_name}.sql' after migration and "
        "after the execution of the methods to auto install and "
        "uninstall modules and do sql fixes.",
    )
    openupgrade_repo = fields.Char(
        string="OpenUpgrade Repository",
        default="https://github.com/OCA/OpenUpgrade.git",
    )
    odoo_running_state = fields.Selection(
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
            ("updating", "Updating"),
            ("updated", "Updated"),
            ("ready_for_migration", "Ready for migration"),
            ("failed", "Failed"),
            ("migrating", "Migrating"),
            ("migrated", "Migrated"),
            ("done", "Done"),
        ],
        string="Migration state",
        readonly=True,
        default="draft",
    )
    update_critical_log = fields.Text(string="Update criticals in log")
    update_error_log = fields.Text(string="Update errors in log")
    update_warning_log = fields.Text(string="Update warnings in log")
    migration_critical_log = fields.Text(string="Migration criticals in log")
    migration_warning_log = fields.Text(string="Migration warnings in log")
    migration_error_log = fields.Text(string="Migration errors in log")
    to_uninstall_modules = fields.Text(string="Modules to be uninstalled")
    uninstalled_modules = fields.Text(string="Uninstalled modules")
    uninstallable_modules = fields.Text(string="Uninstallable modules")
    config_file = fields.Binary(string="Config file (yml)")
    config_file_name = fields.Char(string="Config file name")

    def write(self, vals):
        res = super().write(vals)
        if "config_file" in vals:
            openupgrader_configs = self.env["openupgrader.config"].search(
                [("openupgrader_migration_id", "=", self.id)]
            )
            for openupgrader_config in openupgrader_configs:
                openupgrader_config.action_load_config()
        return res

    @api.depends("current_config_id", "to_config_id")
    def _compute_is_migration_done(self):
        for record in self:
            record.is_migration_done = record.current_config_id == record.to_config_id

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
            f"export PGPASSWORD="
            f"{self.pg_password_var or self.pg_password or ''} "
        )

    def button_get_logs(self):
        self.ensure_one()
        self.migration_critical_log = ""
        self.migration_error_log = ""
        self.migration_warning_log = ""
        self.update_critical_log = ""
        self.update_error_log = ""
        self.update_warning_log = ""
        for openupgrader_config in self.env["openupgrader.config"].search(
            [("openupgrader_migration_id", "=", self.id)], order="name desc"
        ):
            (
                migration_critical_log,
                migration_error_log,
                migration_warning_log,
            ) = self._get_migration_logs(
                self._get_log_path(self.folder, openupgrader_config.name, migrate=True)
            )
            self.migration_critical_log += (
                f"### MIGRATION CRITICAL LOG V. {openupgrader_config.name}\n"
            )
            self.migration_critical_log += migration_critical_log
            self.migration_error_log += (
                f"### MIGRATION ERROR LOG V. {openupgrader_config.name}\n"
            )
            self.migration_error_log += migration_error_log
            self.migration_warning_log += (
                f"### MIGRATION WARNING LOG V. {openupgrader_config.name}\n"
            )
            self.migration_warning_log += migration_warning_log
            (
                update_critical_log,
                update_error_log,
                update_warning_log,
            ) = self._get_migration_logs(
                self._get_log_path(self.folder, openupgrader_config.name)
            )
            self.update_critical_log += (
                f"### UPDATE CRITICAL LOG V. {openupgrader_config.name}\n"
            )
            self.update_critical_log += update_critical_log
            self.update_error_log += (
                f"### UPDATE ERROR LOG V. {openupgrader_config.name}\n"
            )
            self.update_error_log += update_error_log
            self.update_warning_log += (
                f"### UPDATE WARNING LOG V. {openupgrader_config.name}\n"
            )
            self.update_warning_log += update_warning_log

    @staticmethod
    def _get_migration_logs(log_file):
        critical_log = " "
        error_log = " "
        warning_log = " "
        if os.path.isfile(log_file):
            with open(log_file, "r") as f:
                for r in f.readlines():
                    if r and r != "":
                        if "CRITICAL" in r:
                            c_text = r.split("CRITICAL")[1]
                            if c_text and c_text not in critical_log:
                                critical_log += c_text
                        if "ERROR" in r:
                            e_text = r.split("ERROR")[1]
                            if e_text and e_text not in error_log:
                                error_log += e_text
                        if "WARNING" in r:
                            w_text = r.split("WARNING")[1]
                            if w_text and w_text not in warning_log:
                                warning_log += w_text
        return critical_log, error_log, warning_log

    def odoo_connect(self, config_id):
        if self._get_odoo_process_state() == "stopped":
            # start current config odoo if not running
            self.start_odoo(config_id)
        if self.db_name and self.db_user and self.db_password:
            try:
                client = odoorpc.ODOO(
                    host="localhost",
                    protocol="jsonrpc",
                    port=self.xmlrpc_port,
                    opener=self._get_opener(verify_ssl=False),
                )
            except Exception as e:
                logger.info("Connection to Odoo failed for %s!" % str(e))
                # try stop-restart odoo as it is unresponsive
                self.start_odoo(config_id)
                return None
            try:
                client.login(
                    db=f"{self.db_name}_migrate",
                    login=self.db_user,
                    password=self.db_password,
                )
                time.sleep(5)
                return client
            except Exception as e:
                error_string = str(e)
                if (
                    "login failed" in error_string
                    or "Wrong login ID or password" in error_string
                ):
                    raise ValidationError(
                        _("Login to Odoo failed for %s!" % error_string)
                    ) from e
                logger.info("Login to Odoo failed for %s!" % error_string)
                return None
        raise ValidationError(
            _(
                "Db name, login and password are required to connect to the Odoo "
                "instance. Please fill them and try again."
            )
        )

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

    @staticmethod
    def _set_odoorc(folder, config_id):
        odoorc_path = os.path.join(folder, ".odoorc")
        if not os.path.isfile(odoorc_path):
            odoorc_basic_path = get_module_resource("openupgrader", "data", ".odoorc")
            shutil.copyfile(odoorc_basic_path, odoorc_path)
            if float(config_id.name) > 15:
                sed_cmd = f"sed -i 's/longpolling/gevent/g' {odoorc_path}"
                Popen(sed_cmd, shell=True)
            # todo move disabled modules to configuration
            not_auto_install_list = [
                "partner_autocomplete",
                "iap",
                "mail_bot",
                "account_edi_facturx",
                "account_edi_ubl",
                "l10n_it_stock_ddt",
            ]
            if float(config_id.name) <= 14:
                not_auto_install_list.extend(
                    [
                        "account_edi",
                        "l10n_it_edi",
                    ]
                )
            mod_not_install = (
                f"modules_auto_install_disabled = {','.join(not_auto_install_list)}"
            )
            Popen(
                [f"echo {mod_not_install} >> {odoorc_path}"],
                shell=True,
            ).wait()

    def button_start_odoo(self):
        self.start_odoo(config_id=self.current_config_id)

    def check_venv(self, version_name):
        folder = os.path.join(self.folder, f"openupgrade{version_name}")
        if os.path.isdir(os.path.join(folder, ".venv", "bin")):
            return folder
        raise UserError(
            _("Missing environment for version %s! Create it in Odoo Version menu.")
            % version_name
        )

    def start_odoo(self, config_id, update=False, migrate=False, extra_command=""):
        """
        :param config_id: Odoo config_id to start (8.0, 9.0, 10.0, ...)
        :param update: if True odoo will be updated with -u all and stopped
        :param migrate: if True odoo will be migrated with --stop
        :param extra_command: command that will be passed after executable
        :return: null
        """
        self.flush()
        if not config_id:
            raise ValidationError(_("Missing odoo version to start"))
        if update:
            self._start_odoo_thread(config_id, update, migrate, extra_command)
        else:
            self._start_odoo(config_id, update, migrate, extra_command)

    def _start_odoo_thread(
        self, config_id, update=False, migrate=False, extra_command=""
    ):
        with api.Environment.manage():
            new_cr = self.pool.cursor()
            self = self.with_env(self.env(cr=new_cr))
            self._start_odoo(config_id, update, migrate, extra_command)
            new_cr.close()

    def _start_odoo(  # noqa C901
        self, config_id, update=False, migrate=False, extra_command=""
    ):
        logger.info(
            f"Starting Odoo with options: version={config_id.name}, "
            f"update={update}, migrate={migrate}, commands={extra_command}"
        )
        version_name = config_id.name
        version_float = float(version_name)
        self.button_stop_odoo()
        folder = self.check_venv(version_name)
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
        self._set_odoorc(folder, config_id)
        addons_path = f"{folder}/repos/odoo/addons"
        if version_float < 14:
            addons_path = f"{folder}/odoo/addons"
        extra_addons_path = f",{folder}/repos/odoo/odoo/addons,{folder}/odoo"
        if 9 < version_float < 14:
            extra_addons_path = f",{folder}/odoo/odoo/addons"
        subprocess_env = _get_env_for_subprocess(folder, config_id.python_version)
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
        if not os.path.isfile(odoo_log):
            file_writer = open(odoo_log, "w")
            file_writer.write(f"Start Odoo v. {config_id.name} logs")
            file_writer.close()
        bash_command += f"--logfile={odoo_log} "
        logger.info(
            "Starting Odoo in virtualenv to %s version %s with command %s"
            % (
                "migrate" if migrate else "update",
                config_id.name,
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
                    try:
                        out = reader.read().decode()
                    except UnicodeDecodeError:
                        continue
                    if out and out != " ":
                        # try to install missing module with pip on-the-fly
                        if any(
                            x in out
                            for x in [
                                "Some modules have inconsistent states",
                                "Unmet dependencies",
                            ]
                        ):
                            if "Some modules have inconsistent states" in out:
                                match_string = (
                                    "Some modules have inconsistent states, some "
                                    "dependencies may be missing: "
                                )
                                match = re.search(r"%s\[.*\]" % match_string, out)
                            else:
                                match_string = "Unmet dependencies: "
                                match = re.search(r"%s[a-z0-9_,]+" % match_string, out)
                            if match:
                                try:
                                    modules_str = match[0].replace(match_string, "")
                                    if "," in modules_str and not (
                                        modules_str.startswith("[")
                                        or modules_str.startswith("(")
                                    ):
                                        modules = [
                                            m.strip()
                                            for m in modules_str.split(",")
                                            if m.strip()
                                        ]
                                    else:
                                        modules = safe_eval(modules_str)
                                    logger.info(
                                        f"Installing missing modules {str(modules)}"
                                    )
                                    self.install_pip_modules(config_id, modules)
                                except ValueError:
                                    logger.info(
                                        f"Unable to list modules to install via pip "
                                        f"on-the-fly: {match[0]}"
                                    )
                                except Exception as e:
                                    logger.info(
                                        f"Unexpected error while installing missing "
                                        f"modules via pip on-the-fly: {e}"
                                    )
                        logger.info(out.strip())
                # Read the remaining
                try:
                    out = reader.read().decode()
                except UnicodeDecodeError:
                    pass
                logger.info(out)
            # only updating the process will end automatically
            logger.info(
                "Odoo migration instance v. %s should be updated and stopped."
                % version_name
            )
        if extra_command:
            # extra command presumes Odoo will stop automatically like update
            process.wait()
        if not update:
            time.sleep(10)
            self.button_check_odoo_migrated_running_state()
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
                except OSError as e:
                    logger.info("Error %s in killing pid: %s " % (e, pid))
        self.odoo_running_state = "stopped"
        logger.info("Odoo migration instance stopped.")

    def _get_odoo_pids(self):
        pids = []
        db_name = self.env.cr.dbname
        commands = [
            f"pgrep -a python | grep {db_name}_migrate",
            f"pgrep -a postgres | grep {db_name}_migrate",
        ]
        for command in commands:
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

    def restore_filestore(self, from_config_id, to_config_id):
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
                from_config_id.name,
                initial_folder,
                migrated_folder,
            )
        )

    def button_backup_migration(self):
        if not self.current_config_id:
            raise UserError(_("Current version is required!"))
        if not self.current_config_id.db_backup_id:
            self.current_config_id._create_db_backup(folder=self.folder)
        self.current_config_id.db_backup_id.action_backup_migration()

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
        db_name = self.env.cr.dbname
        path_parts = [x for x in config.filestore(db_name).split("/") if x != ""]
        initial_path = os.path.join("/", *path_parts)
        return initial_path

    def dump_database(self, version_name, migrated=False):
        logger.info(
            "Dumping %s database for version %s",
            "migrated" if migrated else "original",
            version_name,
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
            "%s database dumped for version %s",
            "Migrated" if migrated else "Original",
            version_name,
        )
        return destination_path

    def restore_db(self, config_id=False):
        self.button_stop_odoo()
        conn_vars = self._get_db_connection_variables()
        db_name = self.env.cr.dbname
        process = Popen(
            [
                f"{conn_vars} && dropdb --if-exists {db_name}_migrate",
            ],
            shell=True,
            stderr=PIPE,
            stdout=PIPE,
        )
        process.wait()
        error = process.stderr.readlines()
        errors = []
        for e in error:
            try:
                err = e.decode().lower()
                if "error" in err:
                    errors.append(err)
            except UnicodeDecodeError:
                continue
        if errors:
            raise UserError("\n".join(e for e in errors))
        db_migrate = f"{self.env.cr.dbname}_migrate"
        Popen(
            [
                f"{conn_vars} && createdb {db_migrate}",
            ],
            shell=True,
        ).wait()
        # Dump and restore db by sql file as it's the faster way to do it
        dump_file_sql = os.path.join(
            self.folder, f"database.{self.current_config_id.name}.sql"
        )
        if not config_id:
            # not a restore done by hand, so create a new dump
            dump_file_sql = self.dump_database(self.from_config_id.name)
        if not os.path.isfile(dump_file_sql):
            raise UserError(_("Dump sql file %s not found!") % dump_file_sql)
        logger.info(
            "Restoring %(kind_db)s from %(sql_file)s db %(from_db)s %(db)s_migrate."
            % dict(
                db=self.env.cr.dbname,
                from_db="" if config_id else f"from {self.env.cr.dbname} to",
                kind_db="last dumped" if config_id else "original",
                sql_file=dump_file_sql,
            )
        )
        Popen(
            [
                f"{conn_vars} && pg_restore {self.pg_options or ''} "
                f"-d {self.env.cr.dbname}_migrate {dump_file_sql}"
            ],
            shell=True,
        ).wait()
        if not config_id and not self.dump_each_version_database:
            # not a restore done by hand, so delete dump
            os.unlink(dump_file_sql)

    def button_clean_logs(self):
        self.migration_critical_log = " "
        self.migration_error_log = " "
        self.migration_warning_log = " "
        self.update_critical_log = " "
        self.update_error_log = " "
        self.update_warning_log = " "
        for config_id in self.env["openupgrader.config"].search([]):
            for log_name in ["migrate", "update"]:
                log_path = (
                    Path(self.folder)
                    / f"openupgrade{config_id.name}"
                    / f"{log_name}.log"
                )
                if os.path.isfile(log_path):
                    os.unlink(log_path)

    def button_dump_current_database(self):
        self.dump_database(self.current_config_id.name, migrated=True)

    def button_restore_last_database(self):
        self._restore(force=True)

    def button_restore(self):
        for config_id in self.env["openupgrader.config"].search([]):
            self.check_venv(config_id.name)
        self._restore()

    def _restore(self, force=False):
        self.ensure_one()
        self.button_check_odoo_migrated_running_state()
        if self.odoo_running_state == "running":
            self.show_message_odoo_running()
        self.button_clean_logs()
        if not self.current_config_id:
            self.current_config_id = self.from_config_id
        if not self.next_config_id:
            self.next_config_id = self.env["openupgrader.config"].search(
                [("name", "=", str(float(self.current_config_id.name) + 1))]
            )
        if self.from_config_id == self.current_config_id:
            # restore is needed only when we migrate the first version, after the db is
            # already present in the postgresql cluster
            if self.migrate_filestore:
                self.restore_filestore(self.current_config_id, self.current_config_id)
            self.restore_db()
        if force:
            self.restore_db(self.current_config_id)
        self.state = "restored"

    def button_update_current_config(self):
        self.ensure_one()
        self.button_refresh_odoo_running_state()
        if self.odoo_running_state == "running":
            self.show_message_odoo_running()
        if self.state == "updated":
            return {
                "type": "ir.actions.client",
                "tag": "reload",
            }
        # odoo service is stopped automatically at the end of the update process
        return self.start_odoo(self.current_config_id, update=True)

    def button_prepare_for_migration(self):
        self.ensure_one()
        self.button_refresh_odoo_running_state()
        # do pre-upgrade stuff
        if self.odoo_running_state == "running":
            self.show_message_odoo_running()
        before_sql = self.current_config_id.sql_before_migration_command_ids
        self.sql_fixes(before_sql)
        before_python = self.current_config_id.python_before_migration_command_ids
        self.python_fixes(before_python)
        self.uninstall_modules(self.current_config_id, before_migration=True)
        self.delete_not_installed_module_views()
        self.delete_old_modules(self.current_config_id)
        if not self.is_migration_done:
            # write in update log "Ready for migration" to check later
            self.state = "ready_for_migration"
            log_path = self._get_log_path(self.folder, self.current_config_id.name)
            with open(log_path, "w") as log_file:
                now = fields.Datetime.now()
                log_file.write(f"\n\nReady for migration at {now}")

    def button_draft(self):
        self.env.ref("openupgrader.cron_openugrader_do_auto_migration").active = False
        self.button_check_odoo_migrated_running_state()
        if self.odoo_running_state == "running":
            self.show_message_odoo_running()
        for config_id in [self.current_config_id, self.next_config_id]:
            sql_file_path = os.path.join(
                self.folder,
                f"database.{config_id.name}.sql",
            )
            if os.path.isfile(sql_file_path):
                os.remove(sql_file_path)
        self.current_config_id = False
        self.next_config_id = False
        self.uninstalled_modules = False
        self.uninstallable_modules = False
        self.button_clean_logs()
        self.state = "draft"

    def button_recreate_all_env(self):
        self.ensure_one()
        openupgrader_configs = self.env["openupgrader.config"].search(
            [("openupgrader_migration_id", "=", self.id)]
        )
        for openupgrader_config in openupgrader_configs:
            with api.Environment.manage(), self.env.registry.cursor() as new_cr:
                new_env = api.Environment(new_cr, self.env.uid, self.env.context)
                openupgrader_config.with_env(new_env).button_recreate_venv()

    def button_do_all(self):
        #  0. set migration state to draft
        #  1. start migration with the restore of the db-filestore
        #  2. activate the cron for the actual migration
        self.button_stop_odoo()
        self.button_draft()
        self.button_restore()
        while self.state != "restored":
            time.sleep(2)
        while self.state != "updated":
            self.button_update_current_config()
            time.sleep(5)
        self.button_prepare_for_migration()
        self.env.ref("openupgrader.cron_openugrader_do_auto_migration").write(
            {
                "active": True,
                "nextcall": fields.Datetime.now(),
                "numbercall": -1,
                "priority": 1,
            }
        )
        # Forziamo l'attivazione immediata scrivendo direttamente in SQL
        # se necessario, ma proviamo prima con l'ORM corretto.
        logger.info(
            "Cron %s activated and committed.",
            self.env.ref("openupgrader.cron_openugrader_do_auto_migration").name,
        )

    def button_uninstall_missing_modules(self):
        if self.is_migration_done and self.to_uninstall_modules:
            uninstalled_modules = []
            uninstallable_modules = []
            modules_to_process = safe_eval(self.to_uninstall_modules)
            # Process modules in chunks of 15
            for i in range(0, len(modules_to_process), 15):
                chunk = modules_to_process[i : i + 15]
                self.start_odoo(self.current_config_id)
                for module in chunk:
                    res = self.install_uninstall_module(
                        module, self.current_config_id, install=False
                    )
                    if res:
                        uninstalled_modules.append(module)
                    else:
                        uninstallable_modules.append(module)
                self.button_stop_odoo()
            if uninstalled_modules:
                self.uninstalled_modules = str(uninstalled_modules)
            if uninstallable_modules:
                self.uninstallable_modules = str(uninstallable_modules)

    def _cron_migration(self):
        logger.info("Starting OpenUpgrader auto-migration cron")
        try:
            migrations = self.env["openupgrader.migration"].search(
                [("state", "not in", ["failed", "restore_failed", "done"])]
            )
            if not migrations:
                logger.info("No pending migrations found for cron.")
            for migration in migrations:
                logger.info(
                    f"Processing migration for database: {migration.db_name} "
                    f"(ID: {migration.id})"
                )
                # Wait until migration and update are stopped.
                (
                    odoo_running_state,
                    migration_state,
                    current_version,
                ) = migration._get_odoo_running_state()

                # If it's running, we just skip this iteration and wait for
                # the next cron run.
                if (
                    migration_state in ["migrating", "updating"]
                    or odoo_running_state == "running"
                ):
                    logger.info(
                        f"Migration for {migration.db_name} is currently "
                        f"{migration_state} (Odoo {odoo_running_state}). "
                        f"Skipping."
                    )
                    continue

                if migration_state in ["updated", "migrated"]:
                    logger.info(
                        f"Migration for {migration.db_name} is "
                        f"{migration_state}. Preparing for next step."
                    )
                    migration.button_prepare_for_migration()

                # Refresh state after preparation
                (
                    odoo_running_state,
                    migration_state,
                    current_version,
                ) = migration._get_odoo_running_state()

                if migration_state == "ready_for_migration":
                    logger.info(f"Starting migration step for {migration.db_name}")
                    migration.button_do_migration()
                elif migration_state == "done":
                    logger.info(f"Migration for {migration.db_name} completed.")
                else:
                    logger.info(
                        f"Migration for {migration.db_name} in state "
                        f"{migration_state}. No action taken."
                    )
        except Exception:
            logger.exception("Error in OpenUpgrader auto-migration cron")
            raise

        logger.info("OpenUpgrader auto-migration cron finished")

    def _update_to_uninstall_modules(self):
        odoo_log = self._get_log_path(
            self.folder,
            self.current_config_id.name,
        )
        if os.path.isfile(odoo_log):
            obsolete_modules = []
            openupgrader_configs = self.env["openupgrader.config"].search(
                [
                    ("obsolete_modules", "!=", False),
                    ("openupgrader_migration_id", "=", self.id),
                ]
            )
            if openupgrader_configs:
                for openupgrader_config in openupgrader_configs:
                    obsolete_modules.extend(
                        safe_eval(openupgrader_config.obsolete_modules)
                    )
                obsolete_modules = set(obsolete_modules)
            to_uninstall_modules = []
            with open(odoo_log, "r") as f:
                for log_line in f.readlines():
                    if "Some modules have inconsistent states" in log_line:
                        # Set modules to be uninstalled if missing in the final version
                        match = re.search(r"\[.*\]", log_line)
                        if match:
                            modules = safe_eval(match[0])
                            for module in modules:
                                if module not in obsolete_modules:
                                    to_uninstall_modules.append(module)
            self.to_uninstall_modules = str(to_uninstall_modules)

    def _do_after_migration(self):
        logger.info(
            f"Migration done from version {self.current_config_id.name} "
            f"to version {self.next_config_id.name}"
        )
        # do after migration stuff
        self.auto_install_modules(self.next_config_id)
        self.uninstall_modules(self.next_config_id, after_migration=True)
        self.sql_fixes(self.current_config_id.sql_after_migration_command_ids)
        self.python_fixes(self.current_config_id.python_after_migration_command_ids)
        # move version to the next step
        self.current_config_id = self.next_config_id
        self.next_config_id = self.env["openupgrader.config"].search(
            [("name", "=", str(float(self.current_config_id.name) + 1))], limit=1
        )
        if self.dump_each_version_database:
            self.button_dump_current_database()
        if self.is_migration_done:
            from_n = self.from_config_id.name
            to_n = self.to_config_id.name
            logger.info(
                "Migration completed from version %s to version %s", from_n, to_n
            )
            self.state = "done"
            self._update_to_uninstall_modules()

    def button_do_migration(self):
        self.button_refresh_odoo_running_state()
        if self.odoo_running_state == "running":
            self.show_message_odoo_running()
        if self.state in ["migrated", "done"]:
            return {
                "type": "ir.actions.client",
                "tag": "reload",
            }
        return self.start_odoo(self.next_config_id, update=True, migrate=True)

    def button_refresh_odoo_running_state(self):
        """
        Refresh the running state of Odoo and the migration state.
        If a migration is completed, perform post-migration tasks.
        """
        self.ensure_one()
        (
            odoo_running_state,
            migration_state,
            current_version,
        ) = self._get_odoo_running_state()

        if current_version and self.current_config_id:
            if migration_state == "migrated":
                # Se la migrazione è completata, esegue i compiti post-migrazione
                # e imposta la versione successiva da migrare.
                self._do_after_migration()

        if not self.is_migration_done:
            self.state = migration_state
        self.odoo_running_state = odoo_running_state

    def button_check_odoo_migrated_running_state(self):
        """Minimal refresh of Odoo running state only."""
        self.ensure_one()
        odoo_running_state, _m, _c = self._get_odoo_running_state()
        self.odoo_running_state = odoo_running_state

    def _get_odoo_process_state(self):
        """Check if any Odoo process is currently running."""
        odoo_pids = self._get_odoo_pids()
        for odoo_pid in odoo_pids:
            if psutil.pid_exists(odoo_pid):
                return "running"
        return "stopped"

    def _get_migration_state_from_logs(self):
        """
        Scan log files to determine current migration state and version.
        Scans from newest version downwards to find last known state.
        """
        migration_state = self.state
        current_version = self.from_config_id

        # Check versions in descending order to find the latest log
        ou_configs = self.env["openupgrader.config"].search([], order="name DESC")
        for ou_config in ou_configs:
            # 1. Check update log (usually follows migration)
            update_log = self._get_log_path(self.folder, ou_config.name)
            if os.path.isfile(update_log):
                patterns = {
                    "CRITICAL": "restore_failed",
                    "Ready for migration": "ready_for_migration",
                    "Modules loaded": "updated",
                }
                state, found = self._parse_log_file(
                    update_log, patterns, default_state="updating"
                )
                if found:
                    return state, ou_config

            # 2. Check migration log
            migrate_log = self._get_log_path(self.folder, ou_config.name, migrate=True)
            if os.path.isfile(migrate_log):
                patterns = {
                    "CRITICAL": "failed",
                    "Modules loaded": "migrated",
                }
                state, found = self._parse_log_file(
                    migrate_log, patterns, default_state="migrating"
                )
                if found:
                    return state, ou_config

        return migration_state, current_version

    def _parse_log_file(self, log_path, patterns, default_state):
        """
        Helper to parse a log file for specific patterns.
        :param log_path: path to the log file
        :param patterns: dict of {string_to_find: state_to_return}
        :param default_state: state to return if file exists but no pattern matches
        :return: (state, found_bool)
        """
        if not os.path.isfile(log_path):
            return None, False

        with open(log_path, "r") as f:
            # We read all lines. For large logs, we might want to read only the end.
            lines = f.readlines()
            for line in reversed(lines):  # Check from the end for efficiency
                for pattern, state in patterns.items():
                    if pattern in line:
                        return state, True
        return default_state, True

    def _get_odoo_running_state(self):  # noqa C901
        """
        Get the running state of Odoo and the current migration state.
        :return: Tuple of (odoo_running_state, migration_state, current_version)
        """
        odoo_running_state = self._get_odoo_process_state()

        if odoo_running_state == "running":
            logger.info("Odoo is already running")
            return odoo_running_state, self.state, self.from_config_id

        migration_state, current_version = self._get_migration_state_from_logs()
        return odoo_running_state, migration_state, current_version

    def sql_fixes(self, sql_commands):
        # do not change quote order as it will change the way the sql command is
        # interpreted!
        logger.info("Doing custom sql commands.")
        for sql_command in sql_commands:
            run(
                [
                    f"export PGPORT={self.db_port} && "
                    f"export PGHOST={self.pg_host or ''} && "
                    f"export PGUSER={self.pg_user} && export "
                    f"PGPASSWORD={self.pg_password_var or self.pg_password or ''} && "
                    f"psql -d {self.env.cr.dbname}_migrate "
                    f'-c "{sql_command.name}"',
                ],
                shell=True,
            )

    def python_fixes(self, python_commands):
        logger.info("Doing custom python commands.")
        for python_command in python_commands:
            try:
                safe_eval(
                    python_command.name,
                    {"self": self, "api": api, "fields": fields, "models": models},
                )
            except Exception as e:
                logger.error(
                    f"Error executing python command: {python_command.name}. Error: {e}"
                )

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
                repo_url = "https://{user}:{token}@github.com/{path}".format(
                    user=remote_repo.github_user,
                    token=remote_repo.github_token,
                    path=repo_url.split(":", 1)[1],
                )

            run(
                [
                    "git",
                    "clone",
                    "--single-branch",
                    "--depth",
                    "1",
                    "-b",
                    remote_repo.remote_branch,
                    repo_url,
                    repo_path,
                ],
                check=False,
            )

        run(
            ["git", "pull", "origin", version_name, "--rebase"],
            cwd=repo_path,
            check=False,
        )

    def auto_install_modules(self, config_id):
        self.start_odoo(config_id)
        odoo_client = self.odoo_connect(config_id)
        if not odoo_client:
            return
        module_obj = odoo_client.env["ir.module.module"]
        if config_id.name == "12.0":
            self.remove_modules(config_id, "upgrade")
        # force recompute of installed modules for the current version
        config_id._compute_module_installed_ids()
        for module in config_id.module_auto_install_ids:
            module_to_check = module.name
            module_to_install_name = module.module_to_install_name

            # Use search with limit=1 for efficiency
            if module_obj.search(
                [("name", "=", module_to_check), ("state", "=", "installed")], limit=1
            ):
                # uv pip install module as possibly absent
                self.install_pip_modules(config_id, module_to_install_name)
                module_obj.update_list()
                module_toinstall = module_obj.search(
                    [("name", "=", module_to_install_name)], limit=1
                )
                if module_toinstall:
                    module_toinstall_id = module_obj.browse(module_toinstall)
                    if module_toinstall_id:
                        logging.info(f"Installing module: {module_toinstall_id.name}")
                        module_toinstall_id.button_immediate_install()
        self.button_stop_odoo()

    def uninstall_modules(
        self, config_id, before_migration=False, after_migration=False
    ):
        self.start_odoo(config_id)
        if config_id.name == "12.0":
            self.remove_modules(config_id, "upgrade")

        modules_to_uninstall = []
        if after_migration:
            after_ids = config_id.module_to_uninstall_after_migration_ids
            modules_to_uninstall.extend(after_ids)
        if before_migration:
            before_ids = config_id.module_to_uninstall_before_migration_ids
            modules_to_uninstall.extend(before_ids)

        for module in modules_to_uninstall:
            self.install_uninstall_module(module.name, config_id, install=False)

        self.button_stop_odoo()

    def delete_not_installed_module_views(self):
        conn_vars = self._get_db_connection_variables()
        sql_commands = [
            """
            DELETE FROM ir_act_window_view WHERE view_id NOT IN (
            SELECT res_id FROM ir_model_data
            WHERE model='ir.ui.view'
            AND module IN (
            SELECT name FROM ir_module_module WHERE state='installed'));
            """,
            """
            DELETE FROM ir_ui_view WHERE id NOT IN (
            SELECT res_id FROM ir_model_data
            WHERE model='ir.ui.view'
            AND module IN (
            SELECT name FROM ir_module_module WHERE state='installed'));
            """,
        ]
        logger.info(
            "Delete via sql all views and act_windows that aren't linked to an "
            "installed module."
        )
        for sql_command in sql_commands:
            Popen(
                [
                    f"{conn_vars} && "
                    f'psql -d {self.env.cr.dbname}_migrate -c "{sql_command}"'
                ],
                shell=True,
            )

    def delete_old_modules(self, config_id):
        if config_id.module_to_delete_after_migration_ids:
            self.start_odoo(config_id)
            odoo_client = self.odoo_connect(config_id)
            if not odoo_client:
                return
            module_obj = odoo_client.env["ir.module.module"]
            for module_to_delete in config_id.module_to_delete_after_migration_ids:
                module_id = module_obj.browse(
                    module_obj.search(
                        [
                            ("name", "=", module_to_delete.name),
                            ("state", "not in", ["to upgrade", "to install"]),
                        ]
                    )
                )
                if module_id:
                    module_id.unlink()
            self.button_stop_odoo()

    def remove_modules(self, config_id, module_state=""):
        if module_state == "upgrade":
            state = [
                "to upgrade",
            ]
        else:
            state = ["to remove", "to install"]
        odoo_client = self.odoo_connect(config_id)
        if not odoo_client:
            return
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

    def _check_oca_authorship(self, pkg_name, release):
        """
        Verify if the package on PyPI is owned by the 'OCA' user.
        """
        url = f"https://pypi.org/pypi/{pkg_name}/json"
        try:
            req = Request(url)
            # Use a short timeout to avoid blocking migration for too long
            with urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    ownership = data.get("ownership", {})
                    roles = ownership.get("roles", [])
                    releases = data.get("releases", {})
                    for role_info in roles:
                        if (
                            role_info.get("user") == "OCA"
                            and role_info.get("role") == "Owner"
                            and release[:4] in [r[:4] for r in releases]
                        ):
                            return True
            logger.warning(
                "Package %s found on PyPI but not owned by OCA or not with release %s. "
                "Authorship check failed.",
                pkg_name,
                release,
            )
        except Exception as e:
            logger.error(
                "Error verifying OCA authorship for %s release %s: %s",
                pkg_name,
                release,
                e,
            )
        return False

    def install_pip_modules(self, config_id, module_names):  # noqa C901
        if not isinstance(module_names, list):
            module_names = [module_names]
        logger.info("Installing Odoo modules with pip: %s" % str(module_names))
        self.ensure_one()
        odoo_version_int = int(config_id.name.split(".")[0])
        venv_path = os.path.join(self.folder, f"openupgrade{config_id.name}")
        subprocess_env = _get_env_for_subprocess(venv_path, config_id.python_version)
        # try to install with pip and log error if it fails
        not_installable_modules = []
        if not config_id.obsolete_modules:
            obsolete_modules = []
        else:
            obsolete_modules = safe_eval(config_id.obsolete_modules)
        if not config_id.core_modules:
            core_modules = []
        else:
            core_modules = safe_eval(config_id.core_modules)
        uninstall_before = config_id.module_to_uninstall_before_migration_ids
        uninstall_before_names = uninstall_before.mapped("name")
        for name in module_names:
            # exclude module if present in config_id obsolete modules,
            # core modules or to be uninstalled before migration
            if (
                name in core_modules
                or name in obsolete_modules
                or name in uninstall_before_names
            ):
                continue
            # all pip servers used are safe, do not ignore any package found
            release_val = odoo_version_int if odoo_version_int < 15 else ""
            version_val = f"=={config_id.name}.*" if odoo_version_int >= 15 else ""
            base_pkg_name = f"odoo{release_val}-addon-{name}"
            pkg_name = f"{base_pkg_name}{version_val}"

            # Priority 1: Extra index URL
            extra_index_url = subprocess_env.get("UV_INDEX")
            installed_from_extra = False
            if extra_index_url:
                # Use a copy of env to avoid modifying it for subsequent calls
                env_extra = subprocess_env.copy()
                # Ensure UV_INDEX doesn't conflict with --default-index
                # and forces uv to use only the provided index
                if "UV_INDEX" in env_extra:
                    del env_extra["UV_INDEX"]

                command = (
                    "uv pip install --index {extra_index_url} "
                    "--default-index https://pypi.org/simple "
                    "--index-strategy first-index --upgrade "
                    "--prerelease=allow {pkg}"
                ).format(extra_index_url=extra_index_url, pkg=pkg_name)
                logger.info("Attempting to install from extra index")
                process = Popen(
                    command,
                    cwd=venv_path,
                    shell=True,
                    stderr=PIPE,
                    stdout=PIPE,
                    env=env_extra,
                )
                process.communicate()
                if process.returncode == 0:
                    installed_from_extra = True
                    logger.info(
                        "Odoo module %s installed successfully from extra index" % name
                    )

            if not installed_from_extra:
                # Priority 2: OCA (standard index)
                # Verify authorship before installing from standard index
                if self._check_oca_authorship(base_pkg_name, config_id.name):
                    # Ensure we use ONLY standard index even if UV_INDEX is set
                    env_standard = subprocess_env.copy()
                    if "UV_INDEX" in env_standard:
                        del env_standard["UV_INDEX"]

                    command = (
                        "uv pip install --default-index "
                        "https://pypi.org/simple "
                        "--index-strategy unsafe-best-match --upgrade "
                        "--prerelease=allow {pkg}"
                    ).format(pkg=pkg_name)
                    logger.info(
                        "Installing Odoo module from standard index: %s",
                        command,
                    )
                    process = Popen(
                        command,
                        cwd=venv_path,
                        shell=True,
                        stderr=PIPE,
                        stdout=PIPE,
                        env=env_standard,
                    )
                    stdout, stderr = process.communicate()
                    log_texts = []
                    if stderr:
                        for log_line in stderr.splitlines():
                            try:
                                log_l = log_line.decode().lower()
                                log_texts.append(log_l)
                            except UnicodeDecodeError:
                                continue

                    if process.returncode != 0:
                        if any(
                            "no solution found" in log_text for log_text in log_texts
                        ):
                            not_installable_modules.append(name)
                            logger.info(
                                "Module %s not found with uv pip installer: %s"
                                % (
                                    name,
                                    "\n".join(log_text for log_text in log_texts),
                                )
                            )
                        elif any("pkg_resources" in log_text for log_text in log_texts):
                            not_installable_modules.append(name)
                            err_log = "\n".join(log_text for log_text in log_texts)
                            logger.info(
                                "Module %s not installable for setuptools error: %s",
                                name,
                                err_log,
                            )
                        else:
                            logger.warning(
                                "Failed to install module %s: %s",
                                name,
                                "\n".join(log_text for log_text in log_texts),
                            )
                    else:
                        logger.info(
                            "Odoo module %s installed successfully from standard index"
                            % name
                        )
                else:
                    logger.error(
                        "Skipping installation of %s from standard index: "
                        "OCA authorship not verified",
                        pkg_name,
                    )
                    not_installable_modules.append(name)

    def install_uninstall_module(self, module_name, config_id, install=True):
        logger.info(
            f"{'Installing' if install else 'Uninstalling'} module %s in Odoo."
            % module_name
        )
        odoo_client = self.odoo_connect(config_id)
        if not odoo_client:
            return False
        module_obj = odoo_client.env["ir.module.module"]
        module_obj.update_list()
        to_remove_modules = module_obj.search([("state", "=", "to remove")])
        for module_to_remove_id in to_remove_modules:
            module_obj.browse(module_to_remove_id).button_uninstall_cancel()
        domain = [("name", "=", module_name)]
        if not install:
            domain.append(("state", "!=", "not installed"))
        module_ids = module_obj.search(domain)
        if module_ids:
            modules = module_obj.browse(module_ids)
            logger.info(
                "Found {} modules to {}".format(
                    len(modules), "install" if install else "uninstall"
                )
            )
            for module in modules:
                if install:
                    module.button_immediate_install()
                elif module.state in ["installed", "to upgrade", "uninstallable"]:
                    try_number = 0
                    while try_number < 5:
                        try:
                            module.button_immediate_uninstall()
                            module.unlink()
                            logger.info("Module %s uninstalled" % module.name)
                            try_number = 5
                        except Exception as e:
                            try_number += 1
                            logger.info(
                                "Module %s not uninstalled for %s, trying %s/%s times."
                                % (
                                    module.name,
                                    str(e).replace("\n", ""),
                                    try_number,
                                    5,
                                )
                            )
                            if _(
                                "One or more of the selected modules have already been "
                                "uninstalled, if you believe this to be an error, you "
                                "may try again later or contact support."
                            ) in str(e):
                                try_number = 5
                            time.sleep(10)
            return modules
        else:
            logger.info("Module %s not found" % module_name)
            return False
