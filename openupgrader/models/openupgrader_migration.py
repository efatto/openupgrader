import configparser
import http.client
import importlib.util
import logging
import os
import re
import shutil
import socket
import time
from datetime import datetime
from pathlib import Path
from subprocess import PIPE, Popen, run
from urllib.parse import urlsplit

import odoorpc

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import config
from odoo.tools.safe_eval import safe_eval

from .openupgrader_config import _get_env_for_subprocess
from .tools import (
    _get_log_path,
    _get_migration_logs,
    _get_migration_state_entry_from_file,
    _get_migration_state_from_file,
    _get_odoo_batch_pids,
    _get_odoo_pids,
    _get_odoo_process_state,
    _get_opener,
    _get_pid_listening_on_port,
    _get_replace_text_sql,
    _init_migration_state_file,
    _process_uses_database,
    _sql_quote,
    _stop_pid,
)

logger = logging.getLogger(__name__)

# Maximum number of attempts used to uninstall the modules left in state
# ``to remove`` on the migrated database (each attempt restarts the migration
# instance and uninstalls them with ``button_immediate_uninstall``). The
# current attempt number is persisted in the migration state file under the
# ``uninstall_modules_retry_number`` key.
MAX_UNINSTALL_MODULES_RETRY = 5

# Maximum number of automatic retries for a failed migration/update step. The
# auto-migration cron retries the step up to this number of times and then
# marks the migration as failed, so a deterministic error cannot loop forever.
MAX_MIGRATION_RETRY = 3

# OdooRPC uses a 120s timeout by default. Installing/uninstalling modules on a
# large database can take much longer; when the client times out the server-side
# operation keeps running, and retrying immediately starts a second concurrent
# module operation (which can then block on PostgreSQL row locks). Wait for the
# RPC to finish instead.
# It can be lowered with ``OPENUPGRADER_RPC_TIMEOUT`` (seconds) on installations
# where a hung module operation must not cost a full hour of the migration.
ODOO_RPC_TIMEOUT = int(os.environ.get("OPENUPGRADER_RPC_TIMEOUT") or 3600)

# Failed installation attempts allowed per module of the after-migration step:
# the module is then skipped instead of being retried at every cron iteration.
MAX_INSTALL_MODULE_RETRY = 2


def _is_rpc_timeout(exc):
    """Tell whether ``exc`` is an RPC timeout.

    A timed-out RPC keeps running on the migrated instance: retrying (or
    moving to the next module) would start a concurrent registry update.
    """
    if isinstance(exc, odoorpc.error.RPCError):
        # the server answered with an error (a ``lock timeout`` message
        # included): nothing is still running on the migrated instance
        return False
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    text = str(exc).lower()
    return "timed out" in text or "timeout" in text or "time out" in text


class MigratedDbBusyError(UserError):
    """The migrated database stayed busy for the whole timeout.

    Distinct from the other ``UserError`` of the guards (a running ``-u all``
    batch, an unreadable ``pg_stat_activity``): those must abort the step, while
    a busy database only means a module operation is still running.
    """


def _is_rpc_transport_failure(exc):
    """Tell whether ``exc`` left the RPC session to the instance unusable.

    Covers a client timeout (the server keeps working) and a connection that
    died (instance killed by ``--limit-memory-hard``, restarted, ...).
    """
    if isinstance(exc, odoorpc.error.RPCError):
        return False
    return isinstance(
        exc, (ConnectionError, socket.timeout, TimeoutError, http.client.HTTPException)
    ) or _is_rpc_timeout(exc)


# A module operation (install/uninstall/update) locks the module and model
# tables of the migrated database and its registry reload unlinks ``ir.model``
# records, deleting their ``mail_message``/``ir_attachment`` data. A second
# operation started while the first one is still running on the same database
# blocks on PostgreSQL row locks (or deletes data the first one is using), so
# module operations are serialized: before starting one, wait up to this many
# seconds for the migrated database to become idle, then refuse instead of
# racing with the running operation.
MIGRATED_DB_BUSY_TIMEOUT = 3600
MIGRATED_DB_BUSY_POLL_INTERVAL = 5

# Seconds to wait for the migration instance to listen on its RPC port after a
# start (a big database takes longer than the fixed 10 s it used to wait).
MIGRATION_INSTANCE_START_TIMEOUT = int(
    os.environ.get("OPENUPGRADER_START_TIMEOUT") or 300
)

# A module operation that is still running after the RPC timeout is waited for
# as long as it makes progress, but never longer than this (seconds; 24 h by
# default, ``OPENUPGRADER_MIGRATED_DB_MAX_WAIT``).
MIGRATED_DB_MAX_WAIT = int(
    os.environ.get("OPENUPGRADER_MIGRATED_DB_MAX_WAIT") or 24 * 3600
)

# Rows deleted per committed batch when the mail data of the models of a module
# to uninstall is purged in advance (see _purge_mail_data).
MAIL_PURGE_BATCH_SIZE = 5000
# Safety net against an endless purge loop (rows inserted concurrently).
MAIL_PURGE_MAX_BATCHES = 10000
# The models of the ``mail`` module itself: Odoo skips their mail data when it
# unlinks them, and purging them would delete the mail data of all models.
MAIL_MODELS_NOT_PURGED = frozenset(
    ("mail.activity", "mail.activity.type", "mail.followers", "mail.message")
)


class OpenupgraderMigration(models.Model):
    _name = "openupgrader.migration"
    _description = "OpenUpgrader Migration"
    _rec_name = "db_name"

    db_name = fields.Char(
        string="Database name",
        readonly=True,
        default=lambda self: self.env.cr.dbname,
    )
    old_website_name = fields.Char(
        string="Old website name",
        default=lambda self: self._get_old_website_name(),
        help="Website hostname detected from the current Odoo instance "
        "(web.base.url, then website.domain). It can be edited if needed. "
        "If the current name contains 'test' string, the occurrences without this "
        "string will be replaced too.",
    )
    new_website_name = fields.Char(
        string="New website name",
        compute="_compute_new_website_name",
        store=True,
        readonly=False,
        help="Website hostname to use after migration. It is pre-filled from "
        "the old website name by adding 'migr' to its first dot-separated "
        "part, e.g. odoo.mysite.it -> odoomigr.mysite.it. It can be edited.",
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
    is_ultimate_migration = fields.Boolean(
        help="Indicates if this migration is the ultimate one",
    )
    is_migration_done = fields.Boolean(
        compute="_compute_is_migration_done",
    )
    migrate_filestore = fields.Boolean(default=True)
    dump_each_version_database = fields.Boolean(
        default=True,
        help="If enabled, each migration version will be dumped to a file "
        "with name 'database.{version_name}.dump' after migration and "
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
            ("starting", "Starting"),
            ("started", "Started"),
            ("restore_failed", "Restore failed"),
            ("restored", "Restored"),
            ("updating", "Updating"),
            ("updated", "Updated"),
            ("ready_for_migration", "Ready for migration"),
            ("failed", "Failed"),
            ("migrating", "Migrating"),
            ("migrated", "Migrated"),
            ("auto", "Auto Migrating"),
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
    pending_modules = fields.Text(string="Pending modules set as uninstalled")
    uninstalled_modules = fields.Text(string="Uninstalled modules")
    uninstallable_modules = fields.Text(string="Uninstallable modules")
    not_autoinstallable_modules = fields.Text(
        string="Modules set as not autoinstallable"
    )
    config_file = fields.Binary(string="Config file (yml)")
    config_file_name = fields.Char(string="Config file name")
    config_file_path = fields.Char(
        string="Config file path (yml)",
        help="Optional path of the yml configuration on the server. When set, "
        "it takes precedence over the uploaded file and can be reloaded after "
        "editing it on disk.",
    )

    def write(self, vals):
        res = super().write(vals)
        if "config_file" in vals or "config_file_path" in vals:
            openupgrader_configs = self.env["openupgrader.config"].search(
                [("openupgrader_migration_id", "=", self.id)]
            )
            for openupgrader_config in sorted(
                openupgrader_configs, key=lambda c: float(c.name)
            ):
                openupgrader_config.action_load_config()
        return res

    def button_reload_config_file(self):
        """Reload the yml configuration, preferring ``config_file_path``.

        Pointing ``config_file_path`` at the yml allows editing it on disk and
        reloading the configuration without uploading the file again.
        """
        self.ensure_one()
        if self.config_file_path and not os.path.isfile(self.config_file_path):
            raise UserError(_("Config file %s not found!") % self.config_file_path)
        openupgrader_configs = self.env["openupgrader.config"].search(
            [("openupgrader_migration_id", "=", self.id)]
        )
        for openupgrader_config in sorted(
            openupgrader_configs, key=lambda c: float(c.name)
        ):
            openupgrader_config.action_load_config()
        return {"type": "ir.actions.client", "tag": "reload"}

    @api.depends("current_config_id", "to_config_id")
    def _compute_is_migration_done(self):
        for record in self:
            record.is_migration_done = record.current_config_id == record.to_config_id

    @staticmethod
    def show_message_odoo_running_and_return(title=None, message=None):
        if title is None:
            title = _("Odoo Migration is running!")
        if message is None:
            message = _("If you want to do this action anyway, force the stop.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "name": "OpenUpgrader Message",
            "params": {
                "title": title,
                "message": message,
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

    @api.model
    def _default_migration_state_path(self):
        """Default to ``migration_state.json`` file inside current home folder."""
        file_path = os.path.join(
            self._default_folder(),
            "migration_state.json",
        )
        if not os.path.isfile(file_path):
            _init_migration_state_file(
                file_path,
                self.env["openupgrader.config"].search([]).mapped("name"),
            )
        return file_path

    def get_migration_state_from_file(self, config_name=None):
        return _get_migration_state_from_file(
            self._default_migration_state_path(),
            config_name
            or self.env["openupgrader.config"]
            .search([("openupgrader_migration_id", "=", self.id)])
            .mapped("name"),
        )

    def init_migration_state_file(self, reset_env=False):
        _init_migration_state_file(
            self._default_migration_state_path(),
            self.env["openupgrader.config"]
            .search(
                [
                    ("openupgrader_migration_id", "=", self.id),
                ]
            )
            .mapped("name"),
            reset_env=reset_env,
        )

    def _get_db_connection_variables(self):
        return (
            f"export PGPORT={self.db_port or ''} && "
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
            ) = _get_migration_logs(
                _get_log_path(self.folder, openupgrader_config.name, migrate=True)
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
            ) = _get_migration_logs(
                _get_log_path(self.folder, openupgrader_config.name)
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

    def _get_migrated_db_name(self):
        """Return the database name used by the migration instance.

        It is always ``<working database>_migrate``: the working database is
        never touched by the migration instance. ``self.db_name`` is only
        kept for compatibility with older records; all the dump/restore/SQL
        code already targets ``self.env.cr.dbname``, so using anything else
        here would start the migration instance on a different database than
        the one being migrated.
        """
        working_db_name = self.env.cr.dbname
        if self.db_name and self.db_name != working_db_name:
            logger.warning(
                "The migration record database %s differs from the current "
                "database %s: using %s as migration database.",
                self.db_name,
                working_db_name,
                f"{working_db_name}_migrate",
            )
        return f"{working_db_name}_migrate"

    def _get_working_instance_ports(self):
        """Return the ports already used by the working Odoo instance."""
        ports = set()
        for config_key in ("http_port", "longpolling_port", "gevent_port"):
            try:
                ports.add(int(config.get(config_key)))
            except (TypeError, ValueError):
                continue
        return ports

    def _get_migration_instance_ports(self):
        """Return the HTTP and websocket ports of the migration instance.

        The websocket/longpolling port used to be hardcoded to 8072, which is
        also the default of the working Odoo instance: the migration instance
        could not bind it and its requests could be served by the working
        one. The port is now derived from the configured XML-RPC port and is
        guaranteed not to be a port already used by the working instance.
        """
        working_ports = self._get_working_instance_ports()
        try:
            http_port = int(self.xmlrpc_port)
        except (TypeError, ValueError) as e:
            raise UserError(_("XmlRpc port must be an integer.")) from e
        if http_port in working_ports:
            raise UserError(
                _(
                    "XmlRpc port %(port)s is used by the working Odoo instance. "
                    "Set a different XmlRpc port on the migration before "
                    "installing or uninstalling modules."
                )
                % {"port": http_port}
            )
        websocket_port = http_port + 1
        while websocket_port in working_ports:
            websocket_port += 1
        return http_port, websocket_port

    def _check_migration_rpc_target(self, port, migrated_db):
        """Ensure the Odoo process listening on ``port`` is the migration one.

        The migration instance runs side by side with the working one. If the
        RPC port is served by the working instance, ``install_uninstall_module``
        would have the working Odoo process run the module operations, sharing
        its registry, its ``ir_cron`` lock and its database connections.
        """
        pid = _get_pid_listening_on_port(port)
        if not pid:
            # The listener could not be identified (e.g. insufficient
            # permissions): keep the previous behaviour, the port check done
            # when starting the instance is still enforced.
            logger.warning(
                "Unable to identify the process listening on port %s: cannot "
                "verify that it is the migration instance of database %s.",
                port,
                migrated_db,
            )
            return
        if _process_uses_database(pid, migrated_db):
            return
        raise UserError(
            _(
                "The Odoo instance listening on port %(port)s is not the "
                "migration instance of database %(db)s. Refusing to run module "
                "operations on it: stop that instance or set a different "
                "XmlRpc port on the migration."
            )
            % {"port": port, "db": migrated_db}
        )

    def _check_no_migration_batch_running(self):
        """Refuse to touch the migrated instance while it is updating.

        ``-u all --stop`` processes unlink ``ir.model`` records and delete
        their ``mail_message``/``ir_attachment`` data. Killing such a process
        (as ``button_stop_odoo`` would) or connecting to it to install or
        uninstall a module makes the two operations race on the same
        PostgreSQL tables.
        """
        batch_pids = _get_odoo_batch_pids(self.env.cr.dbname)
        if not batch_pids:
            return
        raise UserError(
            _(
                "The migration instance of database %(db)s is still running an "
                "update (process %(pids)s). Wait for it to finish: stopping it "
                "or running a module operation concurrently would corrupt the "
                "migrated database."
            )
            % {
                "db": self._get_migrated_db_name(),
                "pids": ", ".join(str(pid) for pid in batch_pids),
            }
        )

    def _lock_migrated_db(self):
        """Take one transaction-scoped lock per migrated database.

        The lock lives in the working database (it is not tied to the
        migrated one, which may be dropped and recreated) and is shared by the
        cron, the UI buttons and any shell driver. Two processes can therefore
        not start, stop or modify the same ``_migrate`` database at the same
        time. The lock is released when the current transaction is committed
        or rolled back.
        """
        migrated_db = self._get_migrated_db_name()
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s), hashtext(%s))",
            ("openupgrader", migrated_db),
        )
        if not self.env.cr.fetchone()[0]:
            raise UserError(
                _(
                    "Another OpenUpgrader operation is already running on the "
                    "migrated database %(db)s. Wait for it to finish and try "
                    "again."
                )
                % {"db": migrated_db}
            )

    def _count_migrated_db_open_transactions(self, migrated_db):
        """Count the open transactions of the ``migrated_db`` database.

        A registry update keeps an open transaction between its statements
        (``idle in transaction``) and runs statements on tables that are not
        listed here, so counting only module-table queries is not enough. The
        probe runs against the ``postgres`` database, so it never competes
        with ``dropdb``/``pg_restore`` and failures are reported instead of
        being silently treated as "idle".
        """
        conn_vars = self._get_db_connection_variables()
        sql = (
            "SELECT count(*) FROM pg_stat_activity "
            f"WHERE datname = {_sql_quote(migrated_db)} "
            "AND pid <> pg_backend_pid() "
            "AND xact_start IS NOT NULL "
            "AND query NOT LIKE 'autovacuum:%'"
        )
        result = run(
            [f'{conn_vars} && psql -d postgres -t -A -c "{sql}"'],
            shell=True,
            stdout=PIPE,
            stderr=PIPE,
            # ``text`` is only available from Python 3.7, while Odoo 14
            # supports Python 3.6.
            universal_newlines=True,
        )
        if result.returncode:
            raise UserError(
                _(
                    "Unable to inspect the open transactions of the migrated "
                    "database %(db)s: %(error)s"
                )
                % {
                    "db": migrated_db,
                    "error": (result.stderr or result.stdout or "").strip(),
                }
            )
        try:
            return int((result.stdout or "0").strip().splitlines()[-1])
        except (IndexError, TypeError, ValueError) as e:
            raise UserError(
                _(
                    "Unable to read the open transaction count of the migrated "
                    "database %(db)s."
                )
                % {"db": migrated_db}
            ) from e

    def _get_migrated_db_open_transactions(self, migrated_db):
        """Return the open transactions of ``migrated_db``.

        Each item is a dict with ``pid``, ``state``, ``wait_event_type``,
        ``wait_event``, ``xact_age`` and ``state_age`` (seconds) and ``query``
        (truncated). The probe runs against the ``postgres`` database, like
        ``_count_migrated_db_open_transactions``, and fails closed: an
        unreadable or unparsable answer raises instead of being taken for
        "no open transaction".
        """
        conn_vars = self._get_db_connection_variables()
        # chr(1) cannot appear in a query text: no psql -F separator to quote
        # (and nothing for the shell to interpret)
        separator = "chr(1)"
        sql = (
            "SELECT pid || {sep} || coalesce(state, '') || {sep} || "
            "coalesce(wait_event_type, '') || {sep} || coalesce(wait_event, '') "
            "|| {sep} || coalesce(extract(epoch from now() - xact_start)::bigint, "
            "0) || {sep} || coalesce(extract(epoch from now() - state_change)::"
            "bigint, 0) || {sep} || left(regexp_replace(coalesce(query, ''), '\\s+', "
            "' ', 'g'), 120) "
            "FROM pg_stat_activity "
            "WHERE datname = {} "
            "AND pid <> pg_backend_pid() "
            "AND xact_start IS NOT NULL "
            "AND query NOT LIKE 'autovacuum:%' "
            "ORDER BY xact_start"
        ).format(_sql_quote(migrated_db), sep=separator)
        result = run(
            [f'{conn_vars} && psql -d postgres -t -A -c "{sql}"'],
            shell=True,
            stdout=PIPE,
            stderr=PIPE,
            universal_newlines=True,
        )
        if result.returncode:
            raise UserError(
                _(
                    "Unable to inspect the open transactions of the migrated "
                    "database %(db)s: %(error)s"
                )
                % {
                    "db": migrated_db,
                    "error": (result.stderr or result.stdout or "").strip(),
                }
            )
        transactions = []
        # split on newlines only: str.splitlines() also splits on the control
        # characters a query text may contain
        for line in (result.stdout or "").split("\n"):
            if not line.strip():
                continue
            fields = line.split("\x01")
            try:
                (
                    pid,
                    state,
                    wait_event_type,
                    wait_event,
                    xact_age,
                    state_age,
                    query,
                ) = fields
                xact_age = int(xact_age)
                state_age = int(state_age)
            except ValueError as e:
                raise UserError(
                    _(
                        "Unable to read the open transactions of the migrated "
                        "database %(db)s: unexpected output %(line)r"
                    )
                    % {"db": migrated_db, "line": line[:200]}
                ) from e
            transactions.append(
                {
                    "pid": pid,
                    "state": state,
                    "wait_event_type": wait_event_type,
                    "wait_event": wait_event,
                    "xact_age": xact_age,
                    "state_age": state_age,
                    "query": query,
                }
            )
        return transactions

    @staticmethod
    def _transaction_is_working(transaction):
        """Tell whether a transaction is making progress.

        ``active`` with a lock, timer or client wait is not: the statement is
        blocked (``Client`` is a client that stopped reading, i.e. a stuck
        worker), while a running statement may wait for I/O or a lightweight
        lock it is going to get.
        """
        return transaction["state"] == "active" and transaction[
            "wait_event_type"
        ] not in (
            "Client",
            "Lock",
            "Timeout",
        )

    def _wait_active_module_operation(self, migrated_db, module_name=None):
        """Wait for a module operation that outlived the RPC timeout.

        A client timeout stops nothing on the migration instance: the
        ``mail_message`` delete of a module uninstallation can run for hours.
        Waiting for it preserves the work, so the wait is not bound to the
        timeout of a single statement: it ends when the database is idle, and
        gives up only when *every* open transaction has been not working for
        ``MIGRATED_DB_BUSY_TIMEOUT`` (an abandoned request, ``idle in
        transaction``) or after the absolute ``MIGRATED_DB_MAX_WAIT``.

        The stall test uses ``state_age`` (``now() - state_change``), not the
        sampled state: a healthy uninstall is ``idle in transaction`` between
        two short statements for a few seconds only, so sampling could
        otherwise take it for a stuck one.
        """
        self._check_no_migration_batch_running()
        started = time.time()
        while True:
            transactions = self._get_migrated_db_open_transactions(migrated_db)
            if not transactions:
                return
            working = [
                transaction
                for transaction in transactions
                if self._transaction_is_working(transaction)
            ]
            if (
                not working
                and min(transaction["state_age"] for transaction in transactions)
                >= MIGRATED_DB_BUSY_TIMEOUT
            ):
                raise MigratedDbBusyError(
                    _(
                        "%(count)s open transactions have been idle on the "
                        "migrated database %(db)s for more than %(timeout)s "
                        "seconds: refusing to start a new module operation "
                        "concurrently."
                    )
                    % {
                        "count": len(transactions),
                        "db": migrated_db,
                        "timeout": MIGRATED_DB_BUSY_TIMEOUT,
                    }
                )
            if time.time() - started >= MIGRATED_DB_MAX_WAIT:
                raise MigratedDbBusyError(
                    _(
                        "The module operation on the migrated database %(db)s "
                        "has been running for more than %(timeout)s seconds: "
                        "refusing to wait any longer."
                    )
                    % {"db": migrated_db, "timeout": MIGRATED_DB_MAX_WAIT}
                )
            for transaction in transactions:
                logger.info(
                    "Waiting for the module operation%s on %s: pid %s state=%s "
                    "wait=%s/%s xact_age=%ss state_age=%ss query=%s",
                    " on %s" % module_name if module_name else "",
                    migrated_db,
                    transaction["pid"],
                    transaction["state"],
                    transaction["wait_event_type"] or "-",
                    transaction["wait_event"] or "-",
                    transaction["xact_age"],
                    transaction["state_age"],
                    transaction["query"],
                )
            time.sleep(MIGRATED_DB_BUSY_POLL_INTERVAL)

    def _wait_migrated_db_idle(self, migrated_db):
        """Wait until no module operation runs on ``migrated_db``.

        A previous RPC whose driver request was killed or timed out keeps
        running on the migrated instance; the retry must wait for it instead
        of starting a second concurrent registry update on the same database.
        See ``_wait_active_module_operation`` for the wait rules: a running
        statement is never interrupted, an abandoned transaction is.
        """
        self._wait_active_module_operation(migrated_db)

    def _terminate_migrated_db_backends(self, migrated_db):
        """Terminate the sessions still connected to ``migrated_db``."""
        conn_vars = self._get_db_connection_variables()
        result = run(
            [
                f"{conn_vars} && psql -d postgres -t -A -c "
                f'"SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                f"WHERE datname = {_sql_quote(migrated_db)} "
                f'AND pid <> pg_backend_pid()"'
            ],
            shell=True,
            stdout=PIPE,
            stderr=PIPE,
            universal_newlines=True,
        )
        if result.returncode:
            logger.warning(
                "Unable to terminate the sessions of %s: %s",
                migrated_db,
                (result.stderr or result.stdout or "").strip(),
            )

    def _stop_after_rpc_timeout(self, migrated_db, module_name):
        """Kill the server side of a timed-out or lost module operation.

        The interrupted operation is rolled back, so it cannot race with the
        next one. Nothing is raised: the caller decides whether to go on.
        """
        logger.error(
            "The module operation on %s (%s) did not complete (client timeout "
            "or lost connection): stopping the migration instance and "
            "terminating its database sessions.",
            module_name,
            migrated_db,
        )
        self.button_stop_odoo(force=True)
        self._terminate_migrated_db_backends(migrated_db)

    def _wait_after_rpc_timeout(self, migrated_db, module_name):
        """Wait for a timed-out module operation instead of aborting it.

        A client timeout does not interrupt anything on the migration instance:
        a big delete (the ``mail_message`` rows of an uninstalled module) can
        run longer than ``ODOO_RPC_TIMEOUT``. Killing it throws the work away
        and the retry starts it over, so wait for the database to be idle and
        stop the instance only when it stays busy.

        :return: ``True`` when the operation finished server side.
        """
        logger.warning(
            "The module operation on %s (%s) outlived the RPC timeout: waiting "
            "for it instead of aborting it.",
            module_name,
            migrated_db,
        )
        try:
            self._wait_active_module_operation(migrated_db, module_name)
        except MigratedDbBusyError:
            # only a database that stayed busy: the operation is stuck, the
            # other guard errors (running -u all, unreadable pg_stat_activity)
            # are real aborts and must not kill a healthy instance
            logger.error(
                "The module operation on %s (%s) did not finish: stopping the "
                "migration instance.",
                module_name,
                migrated_db,
            )
            self._stop_after_rpc_timeout(migrated_db, module_name)
            return False
        logger.info(
            "The module operation on %s (%s) finished after the RPC timeout.",
            module_name,
            migrated_db,
        )
        return True

    def _set_migrated_modules_to_remove(self, module_names):
        """Mark *installed* modules as ``to remove`` directly in the database.

        Only ``installed`` modules are flipped: ``to install``/``to upgrade``
        ones are leftovers of an interrupted operation and must not turn into
        removal candidates.

        :return: ``True`` when the update ran, ``False`` otherwise.
        """
        module_names = sorted(set(module_names))
        if not module_names:
            return False
        invalid_names = [
            name for name in module_names if not re.match(r"^[a-zA-Z0-9_.\-]+$", name)
        ]
        if invalid_names:
            logger.error(
                "Invalid module names %s, not scheduling their removal.",
                invalid_names,
            )
            return False
        in_clause = ",".join(_sql_quote(name) for name in module_names)
        sql_command = (
            "SET lock_timeout = '30s'; UPDATE ir_module_module SET "
            "state = 'to remove' WHERE name IN "
            f"({in_clause}) AND state = 'installed';"
        )
        conn_vars = self._get_db_connection_variables()
        result = run(
            [
                f"{conn_vars} && psql -v ON_ERROR_STOP=1 "
                f"-d {self._get_migrated_db_name()} "
                f'-c "{sql_command}"'
            ],
            shell=True,
            stdout=PIPE,
            stderr=PIPE,
            universal_newlines=True,
        )
        if result.returncode:
            logger.error(
                "Unable to schedule the removal of %s: %s",
                ", ".join(module_names),
                (result.stderr or result.stdout or "").strip(),
            )
            return False
        return True

    def _abort_on_rpc_timeout(self, migrated_db, module_name):
        """Like ``_stop_after_rpc_timeout``, but aborts the step (uninstalls)."""
        self._stop_after_rpc_timeout(migrated_db, module_name)
        raise UserError(
            _(
                "The module operation on %(module)s in the migrated database "
                "%(db)s did not complete. The server-side operation was "
                "aborted; wait for the database to be idle before retrying."
            )
            % {"module": module_name, "db": migrated_db}
        )

    def odoo_connect(self, config_id):
        migrated_db = self._get_migrated_db_name()
        self._lock_migrated_db()
        self._check_no_migration_batch_running()
        if _get_odoo_process_state(self.env.cr.dbname) == "stopped":
            # start current config odoo if not running
            self.start_odoo(config_id)
        if self.db_name and self.db_user and self.db_password:
            try:
                http_port, _websocket_port = self._get_migration_instance_ports()
                self._check_migration_rpc_target(http_port, migrated_db)
                client = odoorpc.ODOO(
                    host="localhost",
                    protocol="jsonrpc",
                    port=http_port,
                    opener=_get_opener(verify_ssl=False),
                )
            except UserError:
                raise
            except Exception as e:
                logger.info("Connection to Odoo failed for %s!" % str(e))
                # try stop-restart odoo as it is unresponsive
                self.start_odoo(config_id)
                return None
            try:
                client.login(
                    db=migrated_db,
                    login=self.db_user,
                    password=self.db_password,
                )
                if client.env.db != migrated_db:
                    raise UserError(
                        _(
                            "The migration RPC session is bound to database "
                            "%(session_db)s instead of %(db)s."
                        )
                        % {"session_db": client.env.db, "db": migrated_db}
                    )
                client.config["timeout"] = ODOO_RPC_TIMEOUT
                time.sleep(5)
                return client
            except UserError:
                raise
            except Exception as e:
                error_string = str(e)
                if (
                    "login failed" in error_string
                    or "Wrong login ID or password" in error_string
                ):
                    raise ValidationError(
                        _("Login to Odoo failed for %s!") % error_string
                    ) from e
                logger.info("Login to Odoo failed for %s!" % error_string)
                return None
        raise ValidationError(
            _(
                "Db name, login and password are required to connect to the Odoo "
                "instance. Please fill them and try again."
            )
        )

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

    def _try_install_missing_pip_modules(self, config_id, out):
        """Pip-install modules reported as missing by an Odoo update.

        Modules explicitly scheduled for removal are skipped: Odoo reports
        them as missing/inconsistent precisely because they must disappear.
        """
        if not any(
            x in out
            for x in [
                "Some modules have inconsistent states",
                "Unmet dependencies",
            ]
        ):
            return
        if "Some modules have inconsistent states" in out:
            match_string = (
                "Some modules have inconsistent states, some "
                "dependencies may be missing: "
            )
            match = re.search(r"%s\[.*\]" % match_string, out)
        else:
            match_string = "Unmet dependencies: "
            match = re.search(r"%s[a-z0-9_,]+" % match_string, out)
        if not match:
            return
        try:
            modules_str = match[0].replace(match_string, "")
            if "," in modules_str and not (
                modules_str.startswith("[") or modules_str.startswith("(")
            ):
                modules = [
                    module.strip()
                    for module in modules_str.split(",")
                    if module.strip()
                ]
            else:
                modules = safe_eval(modules_str)
            modules_to_uninstall = set(
                config_id.module_to_uninstall_after_migration_ids.mapped("name")
            ) | set(config_id.module_to_uninstall_before_migration_ids.mapped("name"))
            skipped_modules = [
                module for module in modules if module in modules_to_uninstall
            ]
            modules = [
                module for module in modules if module not in modules_to_uninstall
            ]
            if skipped_modules:
                logger.info(
                    "Skipping modules scheduled for removal on-the-fly: %s",
                    skipped_modules,
                )
            if modules:
                logger.info(f"Installing missing modules {str(modules)}")
                config_id.install_pip_modules(modules)
        except ValueError:
            logger.info(
                f"Unable to list modules to install via pip " f"on-the-fly: {match[0]}"
            )
        except Exception as e:
            logger.info(
                f"Unexpected error while installing missing "
                f"modules via pip on-the-fly: {e}"
            )

    def start_odoo(  # noqa C901
        self,
        config_id,
        update=False,
        migrate=False,
        extra_command="",
        try_install_missing_pip_module=True,
    ):
        """
        :param config_id: Odoo config_id to start (8.0, 9.0, 10.0, ...)
        :param update: if True odoo will be updated with -u all and stopped
        :param migrate: if True odoo will be migrated with --stop
        :param extra_command: command that will be passed after executable
        :param try_install_missing_pip_module: if True odoo will try to install missing
         pip module
        :return: null
        """
        self.flush()
        if not config_id:
            raise ValidationError(_("Missing odoo version to start"))
        if self.is_ultimate_migration:
            try_install_missing_pip_module = False
        logger.info(
            f"Starting Odoo with options: version={config_id.name}, "
            f"update={update}, migrate={migrate}, commands={extra_command}, "
            f"try_install_missing_pip_module={try_install_missing_pip_module}"
        )
        version_name = config_id.name
        version_float = float(version_name)
        migrated_db = self._get_migrated_db_name()
        self._lock_migrated_db()
        # never stop (or race with) a running -u all: it is updating the same
        # database and unlinks ir.model/mail data
        self._check_no_migration_batch_running()
        http_port, websocket_port = self._get_migration_instance_ports()
        websocket_server = "longpolling" if version_float < 16 else "gevent"
        # Odoo 7.0 has no --db-filter option: do not break it.
        db_filter = (
            f"--db-filter=^{re.escape(migrated_db)}$ " if version_float >= 8 else ""
        )
        # the migration instance may still be finishing a module operation
        # started by a request whose driver was killed: wait for it instead of
        # killing the server and its backend in the middle of a registry update
        self._wait_migrated_db_idle(migrated_db)
        self.button_stop_odoo(force=True)
        # the stopped instance may still have a backend running the last
        # statement: do not start an update next to it
        self._terminate_migrated_db_backends(migrated_db)
        folder = self.check_venv(version_name)
        load = "web"
        if version_name == "10.0":
            load = "web,web_kanban"
        if version_float > 11:
            load = "base,web"
        if version_float > 13:
            load += ",openupgrade_framework"
        if version_float >= 13:
            load += ",module_change_auto_install"
        executable = config_id._get_odoo_executable(folder)
        addons_path = f"{folder}/repos/odoo/addons"
        if version_float < 14:
            addons_path = f"{folder}/odoo/addons"
        extra_addons_path = f",{folder}/repos/odoo/odoo/addons,{folder}/odoo"
        if 9 < version_float < 14:
            extra_addons_path = f",{folder}/odoo/odoo/addons"
        subprocess_env = _get_env_for_subprocess(folder, config_id.python_version)
        # identify the connections of the migration instance in pg_stat_activity
        # (Odoo >= 16 overrides application_name from ODOO_PGAPPNAME)
        subprocess_env["PGAPPNAME"] = subprocess_env[
            "ODOO_PGAPPNAME"
        ] = f"openupgrader-{migrated_db}"
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
            f"{db_filter}"
            f"--xmlrpc-port={http_port} "
            f"--max-cron-threads=0 "
            f"--limit-time-cpu=99000 "
            f"--limit-time-real=99000 "
            f"--limit-memory-soft=4147483648 "
            f"--limit-memory-hard=4679107584 "
            f"--{websocket_server}-port={websocket_port} "
            f"--load={load} "
            f"-d {migrated_db} "
        )
        if version_name != "7.0":
            data_dir = os.path.join(self.folder, "data_dir")
            if not os.path.isdir(data_dir):
                os.makedirs(data_dir)
            bash_command += f"--data-dir={data_dir} "
        if migrate:
            odoo_log = _get_log_path(self.folder, version_name, migrate=True)
        else:
            odoo_log = _get_log_path(self.folder, version_name)
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
        if migrate:
            config_id.update_migration_state_file(
                "migrating",
                date_started=fields.Datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        with open(odoo_log, "wb") as writer, open(odoo_log, "rb") as reader:
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
                        if try_install_missing_pip_module:
                            self._try_install_missing_pip_modules(config_id, out)
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
        if extra_command != "":
            logger.info(f"Executing extra command: {extra_command}")
            # extra command presumes Odoo will stop automatically like update
            process.wait()
        if not update:
            self._wait_for_odoo_started(http_port)
        time.sleep(2)

    def _wait_for_odoo_started(self, http_port, timeout=None):
        """Wait until the migration instance listens on its RPC port.

        Odoo takes longer than a fixed handful of seconds to load a big
        database, and an RPC call made too early makes ``odoo_connect`` restart
        the instance (or refuse it, when the port is still bound by the one it
        just stopped).
        """
        if timeout is None:
            timeout = MIGRATION_INSTANCE_START_TIMEOUT
        deadline = time.time() + timeout
        while True:
            if _get_odoo_process_state(
                self.env.cr.dbname
            ) == "running" and _get_pid_listening_on_port(http_port):
                self.odoo_running_state = "running"
                return True
            if time.time() >= deadline:
                logger.warning(
                    "The migration instance of %s did not listen on port %s "
                    "within %s seconds.",
                    self._get_migrated_db_name(),
                    http_port,
                    timeout,
                )
                self.button_check_odoo_migrated_running_state()
                return False
            time.sleep(2)

    def button_stop_odoo(self, force=False):
        """Stop the migration instance.

        :param force: skip the advisory lock and the running-update check.
            Only the explicit "stop auto migration" flow uses it: every other
            caller must not interrupt an update of the migrated database.
        """
        if not force:
            self._lock_migrated_db()
            self._check_no_migration_batch_running()
        pids = _get_odoo_pids(self.env.cr.dbname)
        for pid in pids:
            _stop_pid(pid)
        self.odoo_running_state = "stopped"

    def restore_filestore(self):
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
                self.current_config_id.name,
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

    def dump_database_and_globals(self, version_name, migrated=False):
        logger.info(
            "Dumping %s database for version %s",
            "migrated" if migrated else "original",
            version_name,
        )
        destination_path = os.path.join(self.folder, f"database.{version_name}.dump")
        globals_path = os.path.join(self.folder, f"globals.{version_name}.sql")
        conn_vars = self._get_db_connection_variables()
        run(
            f"{conn_vars} && pg_dump {self.pg_options or ''} -Fc -O "
            f"{self.env.cr.dbname}{'_migrate' if migrated else ''} "
            f"> {destination_path}",
            shell=True,
        )
        logger.info(
            "%s database dumped for version %s",
            "Migrated" if migrated else "Original",
            version_name,
        )
        # Stream output to a file written with the current user. Reuse the
        # connection variables (host/port/user/password), otherwise pg_dumpall
        # falls back to the local socket and fails on remote/non-default setups.
        with open(globals_path, "w") as output_file:
            run(
                f"{conn_vars} && pg_dumpall --globals-only",
                stdout=output_file,
                shell=True,
            )
        return destination_path

    def restore_db(self, config_id=False):
        migrated_db = self._get_migrated_db_name()
        self._lock_migrated_db()
        self._check_no_migration_batch_running()
        self.button_stop_odoo()
        conn_vars = self._get_db_connection_variables()
        version_name = self.current_config_id.name
        # Terminate any session still connected to the migrated database,
        # otherwise ``dropdb`` fails with "database is being accessed by other
        # users".
        self._terminate_migrated_db_backends(migrated_db)
        process = Popen(
            [
                f"{conn_vars} && dropdb --if-exists {migrated_db}",
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
        run(
            [
                f"{conn_vars} && createdb {migrated_db}",
            ],
            shell=True,
        )
        # Dump and restore db by dump file as it's the faster way to do it
        dump_file_sql = os.path.join(self.folder, f"database.{version_name}.dump")
        if not config_id:
            # not a restore done by hand, so create a new dump
            dump_file_sql = self.dump_database_and_globals(self.from_config_id.name)
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
        run(
            [
                f"{conn_vars} && pg_restore {self.pg_options or ''} "
                f"-d {migrated_db} {dump_file_sql}"
            ],
            shell=True,
        )
        if not config_id and not self.dump_each_version_database:
            # not a restore done by hand, so delete dump
            os.unlink(dump_file_sql)

    def button_clean_logs(self, config_id=None):
        self.migration_critical_log = " "
        self.migration_error_log = " "
        self.migration_warning_log = " "
        self.update_critical_log = " "
        self.update_error_log = " "
        self.update_warning_log = " "
        config_domain = []
        if config_id:
            # include the given configuration too, so that a failed step whose
            # logs are cleaned before a retry is not detected as failed again
            config_domain = [("name", ">=", config_id.name)]
        for config_id in self.env["openupgrader.config"].search(config_domain):
            for log_name in ["migrate", "update"]:
                log_path = (
                    Path(self.folder)
                    / f"openupgrade{config_id.name}"
                    / f"{log_name}.log"
                )
                if os.path.isfile(log_path):
                    os.unlink(log_path)

    def button_dump_current_database(self):
        self.dump_database_and_globals(self.current_config_id.name, migrated=True)

    def button_restore_last_database(self):
        self.env.ref("openupgrader.cron_openugrader_do_auto_migration").active = False
        self._restore(force=True)

    def button_restore(self):
        for config_id in self.env["openupgrader.config"].search([]):
            self.check_venv(config_id.name)
        self._restore()

    def _restore(self, force=False):
        self._lock_migrated_db()
        self.ensure_one()
        state = "restored"
        (
            odoo_running_state,
            _migration_state,
            _current_version,
        ) = self._get_odoo_running_state()
        if odoo_running_state == "running":
            return self.show_message_odoo_running_and_return()
        if force:
            self.button_clean_logs(self.current_config_id)
            state = "migrated"
        else:
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
                self.restore_filestore()
            self.restore_db()
        if force:
            self.restore_db(self.current_config_id)
        self.state = state
        self.current_config_id.update_migration_state_file(state)
        for op_config in self.env["openupgrader.config"].search(
            [("name", ">", self.current_config_id.name)]
        ):
            op_config.update_migration_state_file(state=False)

    def button_update_current_config(self):
        self._lock_migrated_db()
        self.ensure_one()
        (
            odoo_running_state,
            migration_state,
            _current_version,
        ) = self._get_odoo_running_state()
        if odoo_running_state == "running":
            return self.show_message_odoo_running_and_return()
        if migration_state == "updated":
            return {
                "type": "ir.actions.client",
                "tag": "reload",
            }
        # odoo service is stopped automatically at the end of the update process
        return self.start_odoo(self.current_config_id, update=True)

    @staticmethod
    def _extract_website_hostname(value):
        """Return the hostname of a URL or domain, without scheme and port."""
        value = (value or "").strip()
        if not value:
            return ""
        if "://" not in value and not value.startswith("//"):
            value = "//" + value
        try:
            hostname = urlsplit(value).hostname or ""
        except ValueError:
            hostname = ""
        return hostname.lower().strip(".")

    def _get_old_website_name(self):
        """Detect the old website hostname from the current Odoo instance."""
        candidates = [
            self.env["ir.config_parameter"].sudo().get_param("web.base.url"),
        ]
        if "website" in self.env:
            website = self.env["website"].sudo().search([], limit=1)
            if website:
                candidates.append(website.domain)
        candidates.append(self.address)
        for candidate in candidates:
            hostname = self._extract_website_hostname(candidate)
            if hostname and hostname not in (
                "http",
                "https",
                "localhost",
                "127.0.0.1",
                "::1",
            ):
                return hostname
        for candidate in candidates:
            hostname = self._extract_website_hostname(candidate)
            if hostname and hostname not in ("http", "https"):
                return hostname
        return ""

    @staticmethod
    def _build_new_website_name(old_name):
        """Add ``migr`` to the first dot-separated part of the old name."""
        old_name = (old_name or "").strip().strip(".")
        if not old_name:
            return ""
        parts = old_name.split(".", 1)
        if len(parts) == 1:
            return f"{parts[0]}migr"
        return f"{parts[0]}migr.{parts[1]}"

    @api.depends("old_website_name")
    def _compute_new_website_name(self):
        for record in self:
            record.new_website_name = record._build_new_website_name(
                record.old_website_name
            )

    @staticmethod
    def _get_old_website_name_variants(old_name):
        """Return the old hostname and, for test hosts, its non-test variant.

        When the current website host contains ``test`` (for example
        ``odootest.mysite.it``), the database may still contain links to the
        production host (``odoo.mysite.it``).  Both have to be rewritten to
        the new website name.
        """
        old_name = (old_name or "").strip()
        if not old_name:
            return []
        variants = [old_name]
        if "test" not in old_name.lower():
            return variants
        without_test = ".".join(
            label
            for label in (
                re.sub("test", "", part, flags=re.IGNORECASE).strip("-.")
                for part in old_name.split(".")
            )
            if label
        )
        if without_test and without_test not in variants:
            variants.append(without_test)
        return variants

    @classmethod
    def _get_website_link_replacements(cls, old_name, new_name):
        """Return the URL variants to replace for a hostname change."""
        return [
            (f"{prefix}{old}", f"{prefix}{new_name}")
            for old in cls._get_old_website_name_variants(old_name)
            for prefix in ("https://", "http://", "//")
        ]

    def replace_website_links(self):
        """Replace website URL occurrences in the migrated database."""
        self.ensure_one()
        self._lock_migrated_db()
        old_name = (
            self._extract_website_hostname(self.old_website_name)
            or self._get_old_website_name()
        )
        if old_name in ("http", "https"):
            old_name = self._get_old_website_name()
        new_name = self._extract_website_hostname(self.new_website_name)
        if new_name in ("http", "https"):
            new_name = ""
        if not old_name:
            logger.warning(
                "No old website name found: website link replacement skipped."
            )
            return
        if not new_name:
            new_name = self._build_new_website_name(old_name)
            self.new_website_name = new_name
        if old_name == new_name:
            logger.info(
                "Old and new website names are the same (%s): replacement " "skipped.",
                old_name,
            )
            return
        old_variants = self._get_old_website_name_variants(old_name)
        hostname_re = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")
        for label, hostname in [("old", variant) for variant in old_variants] + [
            ("new", new_name)
        ]:
            if not hostname_re.match(hostname):
                raise UserError(
                    _("Invalid %(label)s website name: %(hostname)s")
                    % {"label": label, "hostname": hostname}
                )
        if self.old_website_name != old_name:
            self.old_website_name = old_name
        if self.new_website_name != new_name:
            self.new_website_name = new_name
        sql = _get_replace_text_sql(
            self._get_website_link_replacements(old_name, new_name)
        )
        new_sql = _sql_quote(new_name)
        for old_variant in old_variants:
            old_sql = _sql_quote(old_variant)
            sql += f"""
UPDATE ir_config_parameter
SET value = replace(value, {old_sql}, {new_sql})
WHERE key = 'web.base.url'
  AND strpos(value, {old_sql}) > 0;
DO \\$\\$
BEGIN
    IF to_regclass('public.website') IS NOT NULL THEN
        BEGIN
            UPDATE website
            SET domain = replace(domain, {old_sql}, {new_sql})
            WHERE strpos(domain, {old_sql}) > 0;
        EXCEPTION WHEN unique_violation THEN
            RAISE WARNING 'website domain replacement skipped: %', SQLERRM;
        END;
    END IF;
END
\\$\\$;
"""
        conn_vars = self._get_db_connection_variables()
        migrated_db = self._get_migrated_db_name()
        self._wait_migrated_db_idle(migrated_db)
        logger.info(
            "Replacing website links %s -> %s in %s",
            old_variants,
            new_name,
            migrated_db,
        )
        result = run(
            [f"{conn_vars} && psql -d {migrated_db} " f'-c "{sql}"'],
            shell=True,
            stdout=PIPE,
            stderr=PIPE,
            universal_newlines=True,
        )
        if result.returncode:
            raise UserError(
                _("Website link replacement failed:\n%s")
                % (result.stderr or result.stdout)
            )
        if result.stderr:
            logger.info("Website link replacement: %s", result.stderr.strip())
        logger.info("Website link replacement completed.")

    def button_prepare_for_migration(self):
        self._lock_migrated_db()
        # do pre-upgrade stuff
        self.ensure_one()
        (
            odoo_running_state,
            migration_state,
            current_version,
        ) = self._get_odoo_running_state()
        if odoo_running_state == "running":
            return self.show_message_odoo_running_and_return()
        before_sql = self.current_config_id.sql_before_migration_command_ids
        self.sql_fixes(before_sql)
        self.uninstall_modules(self.current_config_id, before_migration=True)
        self.remove_not_installed_module_views()
        self.remove_assets()
        self.replace_website_links()
        self.delete_old_modules(self.current_config_id)
        self.current_config_id.update_migration_state_file("ready_for_migration")

    def button_draft(self):
        self._lock_migrated_db()
        self.env.ref("openupgrader.cron_openugrader_do_auto_migration").active = False
        (
            odoo_running_state,
            _migration_state,
            _current_version,
        ) = self._get_odoo_running_state()
        if odoo_running_state == "running":
            return self.show_message_odoo_running_and_return()
        for config_id in [self.current_config_id, self.next_config_id]:
            sql_file_path = os.path.join(
                self.folder,
                f"database.{config_id.name}.dump",
            )
            if os.path.isfile(sql_file_path):
                os.remove(sql_file_path)
        self.current_config_id = False
        self.next_config_id = False
        self.pending_modules = False
        self.uninstalled_modules = False
        self.uninstallable_modules = False
        self.button_clean_logs()
        self.init_migration_state_file()
        self.state = "draft"

    def button_create_all_envs(self):
        self.ensure_one()
        openupgrader_configs = self.env["openupgrader.config"].search(
            [("openupgrader_migration_id", "=", self.id)]
        )
        for openupgrader_config in openupgrader_configs:
            try:
                openupgrader_config.button_create_env()
            except Exception:
                logger.exception(
                    "Error creating environment for version %s",
                    openupgrader_config.name,
                )

    def button_delete_all_envs(self):
        self.ensure_one()
        self.init_migration_state_file(reset_env=True)
        openupgrader_configs = self.env["openupgrader.config"].search(
            [("openupgrader_migration_id", "=", self.id)]
        )
        for openupgrader_config in openupgrader_configs:
            openupgrader_config.button_delete_env()

    def button_stop_auto_migration(self):
        self.ensure_one()
        cron = self.env.ref("openupgrader.cron_openugrader_do_auto_migration")
        max_wait = 10
        while cron.active and max_wait > 0:
            try:
                cron.active = False
            except Exception:
                time.sleep(30)
                max_wait -= 1
        if cron.active:
            raise ValidationError(_("Stop auto migration failed! Retry later."))
        # explicit emergency stop: force overrides the running-update guard
        self.button_stop_odoo(force=True)
        self.state = "draft"  # TODO put the current migration state if possible
        # reset next config migration state in json file
        self.next_config_id.update_migration_state_file(state=False)
        self.button_clean_logs(config_id=self.next_config_id)

    def button_do_auto_migration(self):
        self._lock_migrated_db()
        #  0. set migration state to draft
        #  1. start migration with the restore of the db-filestore
        #  2. activate the cron for the actual migration
        auto_migration_cron = self.env.ref(
            "openupgrader.cron_openugrader_do_auto_migration"
        )
        if self.is_ultimate_migration:
            self.env["ir.config_parameter"].sudo().set_param(
                "ribbon.name", "MIGRATION<br/>in progress"
            )
        self.button_stop_odoo()
        self.button_draft()
        # start migration with cron
        self.state = "starting"
        self.from_config_id.update_migration_state_file(
            state="starting",
            date_started=fields.Datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        auto_migration_cron.write(
            {
                "active": True,
                "nextcall": fields.Datetime.now(),
                "numbercall": -1,
                "priority": 1,
            }
        )
        logger.info(
            "Cron %s activated.",
            auto_migration_cron.name,
        )
        if self.is_ultimate_migration:
            self.set_current_instance_env_to_migr_and_stop()

    def button_continue_auto_migration(self):
        self._lock_migrated_db()
        auto_migration_cron = self.env.ref(
            "openupgrader.cron_openugrader_do_auto_migration"
        )
        auto_migration_cron.active = True
        self.state = "auto"

    def start_initial_migration(self):
        self.from_config_id.update_migration_state_file("started")
        self.button_restore()
        self.button_update_current_config()
        (
            _odoo_running_state,
            migration_state,
            _current_version,
        ) = self._get_odoo_running_state()
        if migration_state != "updated":
            raise ValidationError(
                _(
                    "Exiting as update of initial version %(version)s failed! "
                    "Current migration state %(state)s"
                )
                % dict(version=self.current_config_id.name, state=migration_state)
            )
        logger.info(
            f"Update of current version {self.current_config_id.name} completed"
        )
        self.current_config_id.update_migration_state_file(migration_state)
        self.current_config_id.reset_migration_retry_number()
        self.uninstall_modules(self.current_config_id, after_migration=True)
        self.button_prepare_for_migration()
        self.state = "auto"

    def set_current_instance_env_to_migr_and_stop(self):
        odooconf_path = config.rcfile
        config_parser = configparser.ConfigParser()
        config_parser.read(odooconf_path)
        if "options" in config_parser:
            config_parser["options"]["running_env"] = "migr"
        with open(odooconf_path, "w") as config_file:
            config_parser.write(config_file)
        self.env.cr.commit()  # pylint: disable=E8102
        for process_pattern in ("odoo-bin", "odoo"):
            process = run(
                ["pgrep", "-f", process_pattern],
                stdout=PIPE,
                stderr=PIPE,
            )
            if process.returncode == 0:
                run(["pkill", "-9", "-f", process_pattern])
                break

    def button_refresh_pending_modules(self):
        found_modules_by_state = self._verify_module_states()
        pending_modules = found_modules_by_state.get("pending", [])
        self.pending_modules = str(sorted(set(pending_modules)))

    def _get_migrated_module_state(self, module_name):
        """Read the state of a module from the migrated database.

        The state is read with SQL on purpose: the OdooRPC recordset caches
        the value read before the module operation, and the SQL read does not
        wait for the registry reload the instance is running.
        """
        if not re.match(r"^[a-zA-Z0-9_.\-]+$", module_name or ""):
            logger.error("Invalid module name %r, cannot read its state.", module_name)
            return None
        conn_vars = self._get_db_connection_variables()
        sql_command = (
            "SELECT state FROM ir_module_module WHERE name = %s;"
            % _sql_quote(module_name)
        )
        result = run(
            [
                f"{conn_vars} && psql -d {self._get_migrated_db_name()} -c "
                f'"{sql_command}" -t -A'
            ],
            shell=True,
            stdout=PIPE,
            stderr=PIPE,
            universal_newlines=True,
        )
        if result.returncode != 0:
            logger.error(
                "Unable to read the state of module %s: %s",
                module_name,
                (result.stderr or result.stdout or "").strip(),
            )
            return None
        return result.stdout.strip()

    def _get_pending_modules_to_uninstall(self):
        """Return the modules of the migrated DB left in ``to remove``.

        Only ``to remove`` modules are uninstall candidates. Modules left in
        ``to upgrade``/``to install`` are the leftovers of an interrupted
        update: they must be updated again, not uninstalled, so they are only
        reported (the previous implementation set them to ``to remove`` too and
        a single interrupted update could uninstall hundreds of modules).
        """
        found_modules_by_state = self._verify_module_states()
        leftovers = sorted(
            found_modules_by_state.get("to upgrade", [])
            + found_modules_by_state.get("to install", [])
        )
        if leftovers:
            logger.warning(
                "Modules left in 'to upgrade'/'to install' state in %s are not "
                "uninstalled, they are the leftovers of an interrupted update "
                "and the update must be run again: %s",
                self._get_migrated_db_name(),
                leftovers,
            )
        return found_modules_by_state.get("to remove", [])

    def get_pending_modules(self):
        """Return the modules that still have to be uninstalled."""
        self._lock_migrated_db()
        pending_modules = self._get_pending_modules_to_uninstall()
        if not pending_modules and self.current_config_id:
            # Nothing left to uninstall: reset the retry counter stored in the
            # migration state file.
            self.current_config_id.reset_uninstall_modules_retry_number()
            return []
        if not pending_modules:
            return []
        if not self.current_config_id:
            logger.error(
                "No current version configured for %s: cannot uninstall %s.",
                self.db_name,
                sorted(pending_modules),
            )
            return []
        uninstall_modules_retry_number = (
            self.current_config_id.get_uninstall_modules_retry_number()
        )
        if uninstall_modules_retry_number >= MAX_UNINSTALL_MODULES_RETRY:
            logger.warning(
                "Maximum number of retries (%s) reached while trying to uninstall "
                "pending modules for version %s. Skipping uninstallation of %s.",
                MAX_UNINSTALL_MODULES_RETRY,
                self.current_config_id.name,
                sorted(pending_modules),
            )
            return []
        return pending_modules

    def uninstall_pending_modules(self):
        """Uninstall the modules left in ``to remove`` with Odoo's mechanism.

        The migration instance is started normally (no ``-u all``) and every
        module is uninstalled with ``button_immediate_uninstall``, exactly like
        the Odoo UI does: it sets the module and its dependents to ``to remove``
        and reloads the registry once. No full update is needed, and running one
        here is not only slow (minutes instead of seconds on a real database) but
        also wrong: OpenUpgrade patches ``update_list()`` to set every
        ``auto_install`` module whose dependencies are installed back to
        ``to install``, so a module without target-version code is re-added at
        every update and never removed.
        """
        self._lock_migrated_db()
        pending_modules = self.get_pending_modules()
        if not pending_modules:
            return
        config_id = self.current_config_id
        # Persist the retry number in the migration state file before starting
        # Odoo, so that a failed/crashed attempt is taken into account as well.
        uninstall_modules_retry_number = (
            config_id.increment_uninstall_modules_retry_number()
        )
        logger.info(
            "Uninstalling pending modules for version %s (attempt %s/%s): %s",
            config_id.name,
            uninstall_modules_retry_number,
            MAX_UNINSTALL_MODULES_RETRY,
            sorted(pending_modules),
        )
        try:
            # Odoo uninstalls every 'to remove' module in the registry reload of
            # the first uninstallation, deleting the mail data of their models in
            # one unbounded statement each: purge the rows of all of them in
            # committed batches first (they are all scheduled for removal, so
            # they would be deleted anyway, but this way the progress is not lost
            # when the RPC times out)
            self._purge_mail_data(sorted(pending_modules))
            self.start_odoo(config_id)
            for module_name in sorted(pending_modules):
                try:
                    self.install_uninstall_module(module_name, config_id, install=False)
                except UserError:
                    raise
                except Exception:
                    logger.exception(
                        "Unable to uninstall module %s in version %s",
                        module_name,
                        config_id.name,
                    )
        finally:
            # a module operation may still be finishing on the instance (after
            # a client timeout or a crash): wait for it instead of killing the
            # server in the middle of a registry update. A stop error must not
            # hide the uninstallation error.
            migrated_db = self._get_migrated_db_name()
            try:
                self._wait_migrated_db_idle(migrated_db)
                self.button_stop_odoo()
            except UserError:
                logger.exception(
                    "Unable to stop the migration instance of %s after the "
                    "uninstallation attempt.",
                    self.db_name,
                )
        remaining_modules = self._get_pending_modules_to_uninstall()
        # report the real outcome: a module whose uninstallation failed is not
        # necessarily back to 'to remove' (it may have been cancelled to
        # 'installed' before the failure), so the state is read again
        module_states = {
            module_name: self._get_migrated_module_state(module_name)
            for module_name in pending_modules
        }
        uninstalled_modules = [
            module_name
            for module_name, state in module_states.items()
            if state in ("", "uninstalled", "uninstallable")
        ]
        self.uninstalled_modules = str(sorted(uninstalled_modules))
        self.uninstallable_modules = str(
            sorted(set(pending_modules) - set(uninstalled_modules))
        )
        self.pending_modules = str(sorted(remaining_modules))
        logger.info(
            "Pending modules of %s after uninstallation: %s",
            self.db_name,
            sorted(remaining_modules),
        )

    def _cron_migration(self):  # noqa: C901
        logger.info("Starting OpenUpgrader auto-migration cron")
        try:
            migrations = self.env["openupgrader.migration"].search(
                [("state", "not in", ["failed", "restore_failed", "done"])]
            )
            if not migrations:
                done_migrations = self.env["openupgrader.migration"].search(
                    [("state", "=", "done")]
                )
                for done_migration in done_migrations:
                    try:
                        done_migration._lock_migrated_db()
                    except UserError:
                        logger.info(
                            "Another OpenUpgrader operation is running on %s, "
                            "skipping this cron iteration.",
                            done_migration._get_migrated_db_name(),
                        )
                        continue
                    done_config = done_migration.current_config_id
                    migration_state = (
                        done_config.get_migration_state_from_file()[0]
                        if done_config
                        else None
                    )
                    if migration_state == "done":
                        continue
                    pending_modules = done_migration.get_pending_modules()
                    if pending_modules:
                        done_migration.uninstall_pending_modules()
                    else:
                        done_migration._do_end_migration()
                        logger.info(
                            "No pending modules found in done migrations, this "
                            "cron should be de-activated."
                        )
                logger.info("No pending migrations found for cron.")
            for migration in migrations:
                try:
                    migration._lock_migrated_db()
                except UserError:
                    logger.info(
                        "Another OpenUpgrader operation is running on %s, "
                        "skipping this cron iteration.",
                        migration._get_migrated_db_name(),
                    )
                    continue
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
                if odoo_running_state == "running":
                    logger.info(
                        f"Migration for {migration.db_name} is currently "
                        f"doing {migration_state} (Odoo is {odoo_running_state}). "
                        f"Skipping."
                    )
                    continue
                if migration_state == "starting":
                    migration.start_initial_migration()
                    continue
                if migration_state == "restored":
                    # The restore succeeded but the initial update did not run
                    # (or was interrupted). Recover from it instead of leaving
                    # the cron in a silent no-op loop.
                    if not migration.current_config_id:
                        migration.current_config_id = migration.from_config_id
                    if not migration.next_config_id:
                        migration.next_config_id = migration.env[
                            "openupgrader.config"
                        ].search(
                            [
                                (
                                    "name",
                                    "=",
                                    str(float(migration.current_config_id.name) + 1),
                                )
                            ],
                            limit=1,
                        )
                    migration.button_update_current_config()
                    continue
                # It the migration is done and the version is not yet moved to the next
                # one, do after migration stuff and set to the next version
                if (
                    migration_state == "migrated"
                    and current_version
                    and migration.current_config_id
                    and current_version != migration.current_config_id.name
                ):
                    migration._do_after_migration()
                    continue
                # If the migration instance is stopped and updated or migrated, prepare
                # for the next version migration
                if migration_state in ["updated", "migrated", "ready_to_prepare"]:
                    logger.info(
                        f"Migration for {migration.db_name} is "
                        f"{migration_state}. Preparing for next step."
                    )
                    migration.button_prepare_for_migration()
                elif migration_state == "ready_for_migration":
                    logger.info(f"Starting migration step for {migration.db_name}")
                    migration.button_do_migration()
                elif migration_state == "migrating":
                    logger.info(
                        f"Migration for {migration.db_name} is migrating but "
                        f"not running, so try to restart it."
                    )
                    migration.button_do_migration()
                elif migration_state in ["failed", "restore_failed"]:
                    # Retry a failed step a bounded number of times instead of
                    # leaving the cron in a silent no-op loop forever.
                    failed_config = (
                        migration.env["openupgrader.config"].search(
                            [("name", "=", current_version)], limit=1
                        )
                        or migration.next_config_id
                        or migration.current_config_id
                    )
                    if not failed_config:
                        logger.info(
                            "No configuration available to retry for %s.",
                            migration.db_name,
                        )
                        continue
                    migration_retry_number = failed_config.get_migration_retry_number()
                    if migration_retry_number >= MAX_MIGRATION_RETRY:
                        logger.warning(
                            "Maximum number of retries (%s) reached for "
                            "version %s in state %s; marking the migration "
                            "as failed.",
                            MAX_MIGRATION_RETRY,
                            failed_config.name,
                            migration_state,
                        )
                        migration.state = "failed"
                        continue
                    migration_retry_number = (
                        failed_config.increment_migration_retry_number()
                    )
                    logger.info(
                        "Retrying version %s in state %s (attempt %s/%s).",
                        failed_config.name,
                        migration_state,
                        migration_retry_number,
                        MAX_MIGRATION_RETRY,
                    )
                    # Remove the logs of the failed attempt, otherwise the log
                    # scanner would immediately detect the same failure again.
                    migration.button_clean_logs(failed_config)
                    if (
                        migration_state == "restore_failed"
                        and migration.current_config_id == failed_config
                    ):
                        failed_config.update_migration_state_file("started")
                        migration.button_update_current_config()
                    else:
                        failed_config.update_migration_state_file("ready_for_migration")
                        migration.button_do_migration()
                else:
                    logger.info(
                        f"Migration for {migration.db_name} in version "
                        f"{current_version} in state {migration_state}. "
                        f"No action taken."
                    )
        except Exception:
            logger.exception("Error in OpenUpgrader auto-migration cron")
            raise

        logger.info("OpenUpgrader auto-migration cron finished")

    def _verify_module_states(self):
        found_modules_by_state = {
            "to upgrade": [],
            "to remove": [],
            "to install": [],
            "pending": [],
            "uninstalled": [],
            "uninstallable": [],
            "installed": [],
        }
        conn_vars = self._get_db_connection_variables()
        sql_command = (
            "SELECT name, state FROM ir_module_module "
            "WHERE state in ('to upgrade', 'to remove', 'to install');"
        )
        result = run(
            [
                f"{conn_vars} && psql -d {self.env.cr.dbname}_migrate -c "
                f"\"{sql_command}\" -t -A -F ','"
            ],
            shell=True,
            stdout=PIPE,
            stderr=PIPE,
            universal_newlines=True,
        )

        if result.returncode == 0:
            # Output format: name,state
            lines = result.stdout.strip().split("\n")
            for line in lines:
                if line:
                    parts = line.split(",")
                    if len(parts) == 2:
                        module, state = parts
                        found_modules_by_state[state].append(module)
            found_modules_by_state["pending"] = (
                found_modules_by_state.get("to upgrade", [])
                + found_modules_by_state.get("to remove", [])
                + found_modules_by_state.get("to install", [])
            )
        else:
            # fail closed: an unreadable state must never be taken for "no
            # pending module", otherwise the final dump is written while the
            # modules to uninstall are still there
            logger.error(f"Error checking module states: {result.stderr}")
            raise UserError(
                _(
                    "Unable to read the module states of the migrated database "
                    "%(db)s: %(error)s"
                )
                % {
                    "db": self._get_migrated_db_name(),
                    "error": (result.stderr or result.stdout or "").strip(),
                }
            )

        return found_modules_by_state

    def _do_after_migration(self):
        next_config = self.next_config_id
        if not next_config and self.to_config_id == self.current_config_id:
            # migration should be done, enable re-try last after migration stuff
            next_config = self.current_config_id
        if not next_config:
            raise ValidationError(
                _(
                    "Missing configuration for the version after %(current)s: "
                    "cannot run the after-migration steps up to %(target)s."
                )
                % {
                    "current": self.current_config_id.name or "-",
                    "target": self.to_config_id.name or "-",
                }
            )
        logger.info(
            f"Migration done from version {self.current_config_id.name} "
            f"to version {next_config.name}"
        )
        # The after-migration work belongs to the version we have just migrated
        # to: at this point current_config_id is still the source version.
        # A failed module installation is skipped, but an unreachable instance
        # must keep the step from being marked as done.
        install_result = self.auto_install_modules(next_config) or {}
        not_processed_modules = install_result.get("not_processed") or []
        if not_processed_modules:
            raise UserError(
                _(
                    "The migration instance of %(db)s could not be reached "
                    "again: the installation of %(modules)s was not even "
                    "tried. The after-migration step is left pending so it can "
                    "be retried."
                )
                % {
                    "db": self._get_migrated_db_name(),
                    "modules": ", ".join(not_processed_modules),
                }
            )
        self.uninstall_modules(next_config, after_migration=True)
        self.sql_fixes(next_config.sql_after_migration_command_ids)
        # Move to the next step only once the after-migration work succeeded, so
        # that a failure keeps current_config_id on the source version and the
        # cron can retry _do_after_migration.
        self.current_config_id = next_config
        self.next_config_id = self.env["openupgrader.config"].search(
            [("name", "=", str(float(self.current_config_id.name) + 1))], limit=1
        )
        if self.dump_each_version_database:
            self.button_dump_current_database()
        self.current_config_id.update_migration_state_file("ready_to_prepare")
        self.current_config_id.reset_migration_retry_number()
        # compare the fields directly: is_migration_done is a computed field
        # whose cache could have been filled before current_config_id was moved
        if self.current_config_id == self.to_config_id:
            from_n = self.from_config_id.name
            to_n = self.to_config_id.name
            logger.info(
                "Migration completed from version %s to version %s", from_n, to_n
            )
            # Keep the per-version state at ready_to_prepare: the final dump
            # is written by _do_end_migration() and only then the state is set
            # to done.
            self.state = "done"

    def _do_end_migration(self):
        """Write the final dump and mark the migration as done.

        The cron calls this method for every migration still in state
        ``done``. The per-version ``done`` state in the migration state file
        is written only after the dump, so subsequent cron runs can reuse it
        as a marker and avoid dumping the database again.
        """
        self.ensure_one()
        if not self.current_config_id:
            return
        (
            migration_state,
            _env_state,
            _config_name,
        ) = self.current_config_id.get_migration_state_from_file()
        if migration_state == "done":
            logger.info("Final dump already done for %s, skipping.", self.db_name)
            return
        self.button_dump_current_database()
        self.current_config_id.update_migration_state_file("done")
        logger.info(
            "Final dump of %s for version %s completed.",
            self.db_name,
            self.current_config_id.name,
        )

    def button_do_migration(self):
        self._lock_migrated_db()
        (
            odoo_running_state,
            migration_state,
            current_version,
        ) = self._get_odoo_running_state()
        if odoo_running_state == "running":
            return self.show_message_odoo_running_and_return()
        if migration_state in ["migrated", "done"]:
            return {
                "type": "ir.actions.client",
                "tag": "reload",
            }
        return self.start_odoo(self.next_config_id, update=True, migrate=True)

    def button_check_odoo_migrated_running_state(self):
        """Minimal refresh of Odoo running state only."""
        self.ensure_one()
        (
            odoo_running_state,
            _migration_state,
            _current_version,
        ) = self._get_odoo_running_state()
        self.odoo_running_state = odoo_running_state

    def _update_migration_state_from_logs(self):
        """
        Scan the log files of the steps currently in progress to determine the
        current migration state and version, and save it in the state file.

        Only the current and next configurations are inspected so that a stale
        log of a later step cannot resurrect an already handled state. Only the
        lines written after the step was last started (``date_started``) are
        taken into account, so a recovered failure does not mark the step as
        failed again.
        """
        state_path = self._default_migration_state_path()
        configs = []
        for loop_config in (self.next_config_id, self.current_config_id):
            if loop_config and loop_config not in configs:
                configs.append(loop_config)
        for ou_config in configs:
            entry = _get_migration_state_entry_from_file(state_path, ou_config.name)
            migration_state = entry.get("state")
            min_timestamp = entry.get("date_started")
            # 1. Check the migration log of the version being migrated
            if self.next_config_id and ou_config == self.next_config_id:
                migrate_log = _get_log_path(self.folder, ou_config.name, migrate=True)
                if os.path.isfile(migrate_log):
                    patterns = {
                        "CRITICAL": "failed",
                        "Modules loaded": migration_state
                        if migration_state == "ready_to_prepare"
                        else "migrated",
                    }
                    state, found = self._parse_log_file(
                        migrate_log,
                        patterns,
                        default_state=migration_state,
                        min_timestamp=min_timestamp,
                    )
                    if found:
                        ou_config.update_migration_state_file(state)
                        return
            # 2. Check the update log (initial update / auto-install start)
            update_log = _get_log_path(self.folder, ou_config.name)
            if os.path.isfile(update_log):
                patterns = {
                    "CRITICAL": "restore_failed",
                    "Modules loaded": "migrated"
                    if self.current_config_id == self.to_config_id == ou_config
                    else migration_state
                    if migration_state in ["ready_for_migration", "ready_to_prepare"]
                    else "updated",
                }
                state, found = self._parse_log_file(
                    update_log,
                    patterns,
                    default_state=migration_state,
                    min_timestamp=min_timestamp,
                )
                if found:
                    ou_config.update_migration_state_file(state)
                    return

    @staticmethod
    def _parse_log_file(log_path, patterns, default_state, min_timestamp=None):
        """
        Helper to parse a log file for specific patterns.
        :param log_path: path to the log file
        :param patterns: dict of {string_to_find: state_to_return}
        :param default_state: state to return if file exists but no pattern matches
        :param min_timestamp: only consider lines timed at or after this
            "YYYY-MM-DD HH:MM:SS" timestamp (used to ignore previous runs)
        :return: (state, found_bool)
        """
        if not os.path.isfile(log_path):
            return None, False

        min_datetime = None
        if min_timestamp:
            try:
                min_datetime = datetime.strptime(min_timestamp, "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                min_datetime = None

        with open(log_path, "r") as f:
            # We read all lines. For large logs, we might want to read only the end.
            lines = f.readlines()
        for line in reversed(lines):  # Check from the end for efficiency
            if min_datetime is not None:
                match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if match:
                    try:
                        line_datetime = datetime.strptime(
                            match.group(1), "%Y-%m-%d %H:%M:%S"
                        )
                    except ValueError:
                        line_datetime = None
                    # ignore lines written before the current run started
                    if line_datetime and line_datetime < min_datetime:
                        continue
            for pattern, state in patterns.items():
                if pattern in line:
                    return state, True
        return default_state, True

    def _get_odoo_running_state(self):  # noqa C901
        """
        Get the running state of Odoo and the current migration state.
        :return: Tuple of (odoo_running_state, migration_state, current_version)
        """
        odoo_running_state = _get_odoo_process_state(self.env.cr.dbname)

        if odoo_running_state == "running":
            logger.info("Odoo is already running")
            return odoo_running_state, self.state, self.from_config_id

        self._update_migration_state_from_logs()
        (
            migration_state,
            _env_state,
            current_version,
        ) = self.get_migration_state_from_file()
        return odoo_running_state, migration_state, current_version

    def sql_fixes(self, sql_commands):
        # do not change quote order as it will change the way the sql command is
        # interpreted!
        self._lock_migrated_db()
        migrated_db = self._get_migrated_db_name()
        self._wait_migrated_db_idle(migrated_db)
        logger.info("Doing custom sql commands.")
        conn_vars = self._get_db_connection_variables()
        for sql_command in sql_commands:
            logger.info(f"Executing sql command: {sql_command.name}")
            # ON_ERROR_STOP makes psql return a non-zero exit code when the
            # command fails; without it the failure is only printed on stderr
            # and the migration silently continues.
            result = run(
                [
                    f"{conn_vars} && psql -v ON_ERROR_STOP=1 "
                    f"-d {migrated_db} "
                    f'-c "{sql_command.name}"',
                ],
                shell=True,
                stdout=PIPE,
                stderr=PIPE,
                # ``text`` is only available from Python 3.7, while Odoo 14
                # supports Python 3.6.
                universal_newlines=True,
            )
            if (result.stdout or "").strip():
                logger.info("SQL command result: %s", result.stdout.strip())
            if result.returncode:
                logger.error(
                    "SQL command failed (exit code %s): %s\n%s",
                    result.returncode,
                    sql_command.name,
                    (result.stderr or result.stdout or "").strip(),
                )
            elif (result.stderr or "").strip():
                logger.info("SQL command output: %s", result.stderr.strip())

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
        else:
            run(
                ["git", "pull", "origin", version_name, "--rebase"],
                cwd=repo_path,
                check=False,
            )

    def auto_install_modules(self, config_id):
        """Install the modules of ``config_id`` on the migrated database.

        A failing module is recorded and does not abort the step: the instance
        is stopped, the driver reconnects and the remaining modules are still
        installed. After ``MAX_INSTALL_MODULE_RETRY`` attempts it is skipped.

        :return: dict with the ``failed``, ``skipped`` and ``not_processed``
            (never tried: the instance was unreachable) module names.
        """
        result = {"failed": [], "skipped": [], "not_processed": []}
        self._lock_migrated_db()
        self.start_odoo(config_id)
        odoo_client = self.odoo_connect(config_id)
        if not odoo_client:
            result["not_processed"] = [
                module.module_to_install_name
                for module in config_id.module_auto_install_ids
                | config_id.renamed_module_ids
            ]
            return result
        migrated_db = self._get_migrated_db_name()
        # force recompute of installed modules for the current version
        config_id._compute_module_installed_ids()
        install_failed_modules = config_id.get_install_failed_modules()
        modules = config_id.module_auto_install_ids | config_id.renamed_module_ids
        for index, module in enumerate(modules):
            if not odoo_client:
                # the instance could not be reached again: report the untried
                # modules instead of losing them
                result["not_processed"] = [
                    remaining.module_to_install_name for remaining in modules[index:]
                ]
                logger.error(
                    "Unable to reconnect to the migrated database %s: %s "
                    "module(s) were not installed (%s).",
                    migrated_db,
                    len(result["not_processed"]),
                    ", ".join(result["not_processed"]),
                )
                break
            odoo_client = self._install_module(
                config_id,
                module,
                odoo_client,
                migrated_db,
                install_failed_modules,
                result,
            )
        if result["failed"]:
            logger.warning(
                "The installation of %s module(s) failed on %s: %s. The "
                "migration goes on without them; see the previous errors.",
                len(result["failed"]),
                migrated_db,
                ", ".join(sorted(result["failed"])),
            )
        if result["skipped"]:
            logger.warning(
                "Module(s) skipped after %s failed installation attempts: %s",
                MAX_INSTALL_MODULE_RETRY,
                ", ".join(sorted(result["skipped"])),
            )
        self.button_stop_odoo()
        return result

    def _install_module(
        self,
        config_id,
        module,
        odoo_client,
        migrated_db,
        install_failed_modules,
        result,
    ):
        """Install one module of the after-migration step.

        :return: the session to use for the next module: the same one, or a new
            one when the failure killed it (``None`` when it cannot be opened).
        """
        module_to_install_name = module.module_to_install_name
        module_obj = odoo_client.env["ir.module.module"]
        try:
            if not module_obj.search(
                [("name", "=", module.name), ("state", "=", "installed")],
            ):
                return odoo_client
            if (
                install_failed_modules.get(module_to_install_name, 0)
                >= MAX_INSTALL_MODULE_RETRY
            ):
                logger.warning(
                    "Skipping module %s: its installation already failed %s "
                    "times on version %s. Install it manually on %s once the "
                    "reason is fixed (or delete its install_failed_modules "
                    "entry in the migration state file of version %s).",
                    module_to_install_name,
                    MAX_INSTALL_MODULE_RETRY,
                    config_id.name,
                    migrated_db,
                    config_id.name,
                )
                result["skipped"].append(module_to_install_name)
                return odoo_client
            # install pip package as possibly absent
            config_id.install_pip_modules(module_to_install_name)
            self._wait_migrated_db_idle(migrated_db)
            module_obj.update_list()
            module_toinstall = module_obj.search(
                [("name", "=", module_to_install_name)], limit=1
            )
            if not module_toinstall:
                logger.warning(
                    "Module %s is not available in version %s: skipping its "
                    "installation.",
                    module_to_install_name,
                    config_id.name,
                )
                return odoo_client
            module_toinstall_id = module_obj.browse(module_toinstall)
            if not module_toinstall_id:
                return odoo_client
            self._wait_migrated_db_idle(migrated_db)
            logger.info(
                "Installing module %s on migrated database %s",
                module_to_install_name,
                migrated_db,
            )
            module_toinstall_id.button_immediate_install()
        except UserError:
            # driver-side guard (busy database, unsafe RPC target, ...)
            raise
        except Exception as e:
            return self._handle_install_failure(
                config_id,
                module_to_install_name,
                odoo_client,
                migrated_db,
                e,
                install_failed_modules,
                result,
            )
        if install_failed_modules.pop(module_to_install_name, None) is not None:
            # installation finally succeeded: forget the failures
            config_id.set_install_failed_modules(install_failed_modules)
        return odoo_client

    def _handle_install_failure(
        self,
        config_id,
        module_name,
        odoo_client,
        migrated_db,
        error,
        install_failed_modules,
        result,
    ):
        """Record a failed module installation and get a usable session back."""
        session_lost = _is_rpc_transport_failure(error)
        if session_lost:
            self._wait_after_rpc_timeout(migrated_db, module_name)
            if self._get_migrated_module_state(module_name) == "installed":
                # the installation completed after all: only the answer was lost
                logger.info(
                    "Module %s was installed after the RPC timeout.", module_name
                )
                if install_failed_modules.pop(module_name, None) is not None:
                    config_id.set_install_failed_modules(install_failed_modules)
                return self.odoo_connect(config_id)
        logger.error("Error installing module %s: %s", module_name, error)
        result["failed"].append(module_name)
        install_failed_modules[module_name] = (
            install_failed_modules.get(module_name, 0) + 1
        )
        config_id.set_install_failed_modules(install_failed_modules)
        if session_lost:
            return self.odoo_connect(config_id)
        return odoo_client

    def _exclude_core_modules(self, config_id, module_names):
        core_modules = config_id._get_core_modules()
        core_modules_to_keep = module_names & core_modules
        if core_modules_to_keep:
            logger.info(
                "Not uninstalling modules that are now part of Odoo %s: %s",
                config_id.name,
                sorted(core_modules_to_keep),
            )
        return module_names - core_modules

    def _get_apriori_modules_to_uninstall(self, config_id):
        """Return the old module names declared in apriori.py for a version.

        Only the keys of ``renamed_modules`` and ``merged_modules`` are
        returned. They are the modules to uninstall after the migration to
        that version. Modules shipped with the target Odoo core are excluded:
        the old OCA module may have been replaced by a core module using the
        same technical name.
        """
        if not config_id:
            return set()
        apriori_py_path = os.path.join(
            self.folder,
            f"openupgrade{config_id.name}",
            "odoo",
            "openupgrade_scripts",
            "apriori.py",
        )
        if not os.path.isfile(apriori_py_path):
            return set()
        try:
            spec = importlib.util.spec_from_file_location("apriori", apriori_py_path)
            apriori_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(apriori_mod)
        except Exception:
            logger.exception("Error loading apriori.py from %s", apriori_py_path)
            return set()
        renamed_modules = getattr(apriori_mod, "renamed_modules", {}) or {}
        merged_modules = getattr(apriori_mod, "merged_modules", {}) or {}
        modules_to_uninstall = set(renamed_modules) | set(merged_modules)
        return self._exclude_core_modules(config_id, modules_to_uninstall)

    def uninstall_modules(
        self, config_id, before_migration=False, after_migration=False
    ):
        self._lock_migrated_db()
        modules_to_uninstall = set()
        if after_migration:
            after_ids = config_id.module_to_uninstall_after_migration_ids
            modules_to_uninstall.update(after_ids.mapped("name"))
            modules_to_uninstall.update(
                self._get_apriori_modules_to_uninstall(config_id)
            )
        if before_migration:
            before_ids = config_id.module_to_uninstall_before_migration_ids
            modules_to_uninstall.update(before_ids.mapped("name"))
        modules_to_uninstall = self._exclude_core_modules(
            config_id, modules_to_uninstall
        )

        self.start_odoo(config_id)
        odoo_client = self.odoo_connect(config_id)
        module_obj = odoo_client.env["ir.module.module"] if odoo_client else None
        # only the modules that Odoo really uninstalls are purged: a missing or
        # uninstalled module is skipped, and a 'to install' one is refused by
        # button_uninstall (the purge must never delete data of a module that
        # stays installed)
        purgable_module_names = set()
        if module_obj and modules_to_uninstall:
            found_module_ids = module_obj.search(
                [("name", "in", sorted(modules_to_uninstall))]
            )
            found_modules = (
                module_obj.browse(found_module_ids) if found_module_ids else []
            )
            found_module_names = {module.name for module in found_modules}
            missing_modules = modules_to_uninstall - found_module_names
            already_uninstalled = {
                module.name for module in found_modules if module.state == "uninstalled"
            }
            if missing_modules:
                logger.info(
                    "Skipping modules that do not exist in version %s: %s",
                    config_id.name,
                    sorted(missing_modules),
                )
            if already_uninstalled:
                logger.info(
                    "Skipping already uninstalled modules in version %s: %s",
                    config_id.name,
                    sorted(already_uninstalled),
                )
            modules_to_uninstall = found_module_names - already_uninstalled
            purgable_module_names = {
                module.name
                for module in found_modules
                if module.state in ("installed", "to upgrade", "to remove")
            }
        try:
            # the uninstallation deletes the mail data of the removed models in
            # one unbounded statement: purge it in committed batches first (only
            # for the modules that Odoo is going to uninstall, never for one that
            # stays installed)
            if purgable_module_names:
                self._purge_mail_data(sorted(purgable_module_names))
            for module_name in sorted(modules_to_uninstall):
                try:
                    self.install_uninstall_module(module_name, config_id, install=False)
                except UserError:
                    raise
                except Exception as e:
                    if _is_rpc_timeout(e):
                        self._abort_on_rpc_timeout(
                            self._get_migrated_db_name(), module_name
                        )
                    logger.exception(
                        "Unable to uninstall module %s in version %s",
                        module_name,
                        config_id.name,
                    )
        finally:
            self.button_stop_odoo()

    def remove_not_installed_module_views(self):
        self._lock_migrated_db()
        migrated_db = self._get_migrated_db_name()
        self._wait_migrated_db_idle(migrated_db)
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
            DELETE FROM ir_ui_view WHERE type <> 'qweb' AND id NOT IN (
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
            result = run(
                [f"{conn_vars} && " f'psql -d {migrated_db} -c "{sql_command}"'],
                shell=True,
                stdout=PIPE,
                stderr=PIPE,
                universal_newlines=True,
            )
            if result.returncode:
                logger.error(
                    "Unable to delete views not linked to an installed module: %s",
                    (result.stderr or result.stdout or "").strip(),
                )

    def remove_assets(self):
        self._lock_migrated_db()
        migrated_db = self._get_migrated_db_name()
        self._wait_migrated_db_idle(migrated_db)
        conn_vars = self._get_db_connection_variables()
        views_list = [
            "portal._assets_primary_variables",
            "web._assets_primary_variables",
            "website._assets_primary_variables",
        ]
        sql_commands = [
            (
                """
            WITH RECURSIVE views_to_delete AS (
                SELECT v.id
                FROM ir_ui_view v
                WHERE key = ANY(ARRAY%s)
                UNION ALL
                SELECT v.id
                FROM ir_ui_view v
                JOIN views_to_delete vtd ON v.inherit_id = vtd.id
            ),
            deleted_imd AS (
                DELETE FROM ir_model_data
                WHERE model = 'ir.ui.view'
                  AND res_id IN (SELECT id FROM views_to_delete)
            )
            DELETE FROM ir_ui_view
            WHERE id IN (SELECT id FROM views_to_delete)
            """
                % list(views_list)
            ),
        ]
        logger.info(
            "Delete via sql web, website and portal views inherited by to-be-removed "
            "views."
        )
        for sql_command in sql_commands:
            result = run(
                [f"{conn_vars} && " f'psql -d {migrated_db} -c "{sql_command}"'],
                shell=True,
                stdout=PIPE,
                stderr=PIPE,
                universal_newlines=True,
            )
            if result.returncode:
                logger.error(
                    "Unable to delete inherited assets views: %s",
                    (result.stderr or result.stdout or "").strip(),
                )

    def delete_old_modules(self, config_id):
        self._lock_migrated_db()
        if config_id.module_to_delete_after_migration_ids:
            self.start_odoo(config_id)
            odoo_client = self.odoo_connect(config_id)
            if not odoo_client:
                return
            module_obj = odoo_client.env["ir.module.module"]
            migrated_db = self._get_migrated_db_name()
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
                    self._wait_migrated_db_idle(migrated_db)
                    module_id.unlink()
            self.button_stop_odoo()

    def _remove_uninstalled_module_record(self, module):
        """Delete the ``ir.module.module`` row of a just uninstalled module.

        The registry reloaded at the end of ``button_immediate_uninstall`` runs
        OpenUpgrade's patched ``update_list()``: every module marked
        ``auto_install`` whose dependencies are installed is set back to
        ``to install``, even when its target-version code is missing (Odoo then
        skips it as not installable and leaves an inconsistent state). That is
        the only state that is cancelled here: the uninstallation is already
        done, so the pending install must not survive and block the deletion of
        the record. Any other unexpected state (a module really reinstalled, a
        pending upgrade, ...) is kept and reported, so the database is not
        silently left without its data.
        """
        module_name = module.name
        state = self._get_migrated_module_state(module_name)
        if state == "to install":
            # the uninstallation is already done, only the module record was
            # put back to 'to install' by the reloaded registry
            module.button_install_cancel()
        elif state not in ("", "uninstalled", "uninstallable"):
            logger.error(
                "Module %s is in state %r after its uninstallation: keeping "
                "its record.",
                module_name,
                state,
            )
            return False
        module.unlink()
        return True

    def _remove_uninstalled_module_records(self, modules):
        """Delete the module records of the modules uninstalled by one reload.

        ``button_immediate_uninstall`` sets to ``to remove`` and uninstalls the
        module, its downstream dependencies and the modules already scheduled
        for removal, all in the same registry reload.
        """
        removed_modules = []
        for module in modules:
            if self._remove_uninstalled_module_record(module):
                removed_modules.append(module.name)
        logger.info("Modules uninstalled: %s", sorted(removed_modules))
        return removed_modules

    def _get_module_model_names(self, module_names):
        """Return the technical models that Odoo removes with ``module_names``.

        Mirrors ``ir.model.data._module_data_uninstall``: the ``ir.model``
        records referenced by the ``ir_model_data`` rows of the modules, minus
        the models that still have an ``ir_model_data`` row of another module
        (a model defined by a removed module but extended by an installed one
        is kept by Odoo, so its mail data must be kept too), minus the models
        of ``mail`` itself (Odoo skips their mail data, purging them would
        delete the mail data of every model).
        """
        module_names = sorted(
            name
            for name in set(module_names or [])
            if re.fullmatch(r"[a-zA-Z0-9_.\-]+", name or "")
        )
        if not module_names:
            return []
        in_clause = ",".join(_sql_quote(name) for name in module_names)
        sql_command = (
            "SELECT DISTINCT im.model FROM ir_model_data d "
            "JOIN ir_model im ON im.id = d.res_id "
            f"WHERE d.model = 'ir.model' AND d.module IN ({in_clause}) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM ir_model_data o "
            "WHERE o.model = 'ir.model' AND o.res_id = d.res_id "
            f"AND o.module NOT IN ({in_clause}) "
            "AND o.module <> '__cloc_exclude__')"
            ";"
        )
        conn_vars = self._get_db_connection_variables()
        result = run(
            [
                f"{conn_vars} && psql -d {self._get_migrated_db_name()} -t -A "
                f'-c "{sql_command}"'
            ],
            shell=True,
            stdout=PIPE,
            stderr=PIPE,
            universal_newlines=True,
        )
        if result.returncode:
            logger.error(
                "Unable to list the models of the modules to uninstall %s: %s",
                module_names,
                (result.stderr or result.stdout or "").strip(),
            )
            return []
        model_names = [
            line.strip() for line in (result.stdout or "").splitlines() if line.strip()
        ]
        # the names are embedded in a psql command line: never trust the table
        invalid_names = [
            name
            for name in model_names
            if not re.fullmatch(r"[a-zA-Z0-9_.]+", name)
            or name in MAIL_MODELS_NOT_PURGED
        ]
        if invalid_names:
            logger.error(
                "Not purging the mail data of the models %s (invalid name or "
                "model of the mail module itself).",
                invalid_names,
            )
            model_names = [name for name in model_names if name not in invalid_names]
        return model_names

    def _purge_mail_data(self, module_names):
        """Delete the mail data of the models of ``module_names`` in advance.

        Odoo 18 uninstalls a module by unlinking its ``ir.model`` records, and
        ``mail``'s ``IrModel.unlink`` deletes *all* the ``mail_message``,
        ``mail_followers`` and ``mail_activity`` rows of those models inside the
        uninstall transaction, without limit and without logging anything: on a
        production database that single statement runs for hours, the RPC client
        times out and the operation either blocks the driver or is killed and
        restarted from scratch. Deleting the same rows here, in small committed
        batches, makes the uninstallation fast (the unbounded ``DELETE`` finds
        nothing left) and keeps the progress if it is interrupted.

        :return: the number of rows deleted.
        """
        model_names = self._get_module_model_names(module_names)
        if not model_names:
            return 0
        migrated_db = self._get_migrated_db_name()
        # an operation left running by a previous attempt (typically the very
        # delete being purged) must finish first: wait for it while it is active
        self._wait_active_module_operation(migrated_db)
        conn_vars = self._get_db_connection_variables()
        models = ",".join(_sql_quote(name) for name in model_names)
        statements = [
            (
                "mail_activity",
                "DELETE FROM mail_activity WHERE id IN ("
                "SELECT id FROM mail_activity WHERE res_model_id IN "
                f"(SELECT id FROM ir_model WHERE model IN ({models})) "
                f"LIMIT {MAIL_PURGE_BATCH_SIZE})",
            ),
            (
                "mail_followers",
                "DELETE FROM mail_followers WHERE id IN ("
                f"SELECT id FROM mail_followers WHERE res_model IN ({models}) "
                f"LIMIT {MAIL_PURGE_BATCH_SIZE})",
            ),
            (
                "mail_message",
                "DELETE FROM mail_message WHERE id IN ("
                f"SELECT id FROM mail_message WHERE model IN ({models}) "
                f"LIMIT {MAIL_PURGE_BATCH_SIZE})",
            ),
        ]
        logger.info(
            "Purging the mail data of the models of the modules to uninstall "
            "%s (%s models, %s rows per commit).",
            module_names,
            len(model_names),
            MAIL_PURGE_BATCH_SIZE,
        )
        deleted_rows = 0
        failed = False
        for table, statement in statements:
            batches = 0
            while True:
                batches += 1
                if batches > MAIL_PURGE_MAX_BATCHES:
                    logger.error(
                        "Stopped purging %s for modules %s after %s batches: "
                        "rows are still being inserted?",
                        table,
                        module_names,
                        batches,
                    )
                    failed = True
                    break
                sql_command = (
                    "SET lock_timeout = '60s'; "
                    f"WITH d AS ({statement} RETURNING 1) SELECT count(*) FROM d;"
                )
                result = run(
                    [
                        f"{conn_vars} && psql -v ON_ERROR_STOP=1 "
                        f'-d {migrated_db} -t -A -c "{sql_command}"'
                    ],
                    shell=True,
                    stdout=PIPE,
                    stderr=PIPE,
                    universal_newlines=True,
                )
                if result.returncode:
                    logger.error(
                        "Unable to purge %s for modules %s: %s. The "
                        "uninstallation will delete the remaining rows in one "
                        "statement.",
                        table,
                        module_names,
                        (result.stderr or result.stdout or "").strip(),
                    )
                    failed = True
                    break
                try:
                    deleted = int((result.stdout or "0").strip().splitlines()[-1])
                except (IndexError, ValueError):
                    logger.error(
                        "Unexpected output purging %s for modules %s: %s",
                        table,
                        module_names,
                        (result.stdout or "").strip(),
                    )
                    failed = True
                    break
                if not deleted:
                    break
                deleted_rows += deleted
                logger.info(
                    "Purged %s %s row(s) for modules %s.",
                    deleted,
                    table,
                    module_names,
                )
        logger.info(
            "Mail data purge for modules %s done: %s row(s) deleted%s.",
            module_names,
            deleted_rows,
            " (incomplete, see the previous errors)" if failed else "",
        )
        return deleted_rows

    def _uninstall_module(
        self, module, module_obj, cleanup_ids, migrated_db, config_id
    ):
        """Uninstall one module, retrying a failed RPC call.

        ``cleanup_ids`` are the modules whose records are deleted once the
        uninstallation reload is done.
        """
        module_name = module.name
        try_number = 0
        try:
            while try_number < 5:
                # the previous attempt may still be running on the migrated
                # instance: wait for it instead of starting a second concurrent
                # registry update
                try:
                    self._wait_migrated_db_idle(migrated_db)
                except MigratedDbBusyError as e:
                    try_number += 1
                    logger.error(
                        "The migrated database is still busy after %s "
                        "attempt(s): %s",
                        try_number,
                        e,
                    )
                    continue
                try:
                    module.button_immediate_uninstall()
                    self._remove_uninstalled_module_records(
                        module_obj.browse(sorted(cleanup_ids))
                    )
                    try_number = 5
                except UserError:
                    raise
                except Exception as e:
                    if _is_rpc_transport_failure(e):
                        # the uninstallation may still be running server side
                        # (a big mail_message delete outlives the RPC timeout):
                        # do not throw it away, wait for it and reconnect
                        self._wait_after_rpc_timeout(migrated_db, module_name)
                        odoo_client = self.odoo_connect(config_id)
                        if not odoo_client:
                            raise
                        module_obj = odoo_client.env["ir.module.module"]
                        # 'to install' too: the reloaded registry puts
                        # auto_install modules back to that state
                        state = self._get_migrated_module_state(module_name)
                        if state in ("", "uninstalled", "uninstallable", "to install"):
                            # it did finish: only the records are left
                            self._remove_uninstalled_module_records(
                                module_obj.browse(sorted(cleanup_ids))
                            )
                            try_number = 5
                            continue
                        module = module_obj.browse(
                            module_obj.search([("name", "=", module_name)], limit=1)
                        )
                    try_number += 1
                    logger.info(
                        "Module %s not uninstalled for %s, trying %s/%s times.",
                        module.name,
                        str(e).replace("\n", ""),
                        try_number,
                        5,
                    )
                    if _(
                        "One or more of the selected modules have already been "
                        "uninstalled, if you believe this to be an error, you "
                        "may try again later or contact support."
                    ) in str(e):
                        try_number = 5
                    time.sleep(10)
        finally:
            # the pending removal was cancelled before the uninstallation: if
            # the uninstallation did not even start, the module would silently
            # stop being a candidate and the final dump would be written as if
            # it had been removed
            self._restore_pending_removal(module)

    def _restore_pending_removal(self, module):
        """Make a module cancelled before a failed uninstall pending again.

        Losing the candidate would make the final dump look complete, so fall
        back to SQL when the session is dead (a timed-out uninstallation).
        """
        if self._get_migrated_module_state(module.name) != "installed":
            return
        try:
            module.write({"state": "to remove"})
        except Exception:
            logger.warning(
                "Unable to write module %s through the RPC: setting it back "
                "to 'to remove' with SQL.",
                module.name,
                exc_info=True,
            )
            self._set_migrated_modules_to_remove([module.name])

    def install_uninstall_module(self, module_name, config_id, install=True):
        """Install or uninstall a module in the migration instance.

        Module operations are always executed by the migration instance, on
        ``<working database>_migrate``. ``odoo_connect`` refuses to connect
        when the RPC port is served by the working instance and the exact
        ``-d <db>_migrate`` process matching prevents stopping the working
        instance, so its PostgreSQL queries and writes are not impacted.
        """
        self._lock_migrated_db()
        logger.info(
            f"{'Installing' if install else 'Uninstalling'} module %s in Odoo."
            % module_name
        )
        odoo_client = self.odoo_connect(config_id)
        if not odoo_client:
            return False
        migrated_db = self._get_migrated_db_name()
        if odoo_client.env.db != migrated_db:
            logger.error(
                "Refusing to modify module %s: the RPC session is bound to "
                "database %s instead of %s.",
                module_name,
                odoo_client.env.db,
                migrated_db,
            )
            return False
        # a previous module operation (possibly left running by a killed or
        # retried driver request) must finish before touching the module and
        # model tables again: its ir.model unlink deletes mail_message rows
        self._wait_migrated_db_idle(migrated_db)
        module_obj = odoo_client.env["ir.module.module"]
        # Modules already marked 'to remove': Odoo uninstalls all of them in the
        # registry reload of this call, so their records are cleaned up with the
        # one of the requested module after the uninstallation.
        scheduled_to_remove_ids = set(module_obj.search([("state", "=", "to remove")]))
        # A module marked 'to remove' cannot be uninstalled again:
        # ``button_uninstall`` raises for any state but 'installed'/'to upgrade'.
        # Cancel the pending removal of the requested module only: the other
        # 'to remove' modules stay candidates if this uninstallation fails.
        for module_to_remove in module_obj.browse(sorted(scheduled_to_remove_ids)):
            if module_to_remove.name == module_name:
                module_to_remove.button_uninstall_cancel()
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
                    self._wait_migrated_db_idle(migrated_db)
                    try:
                        module.button_immediate_install()
                    except Exception as e:
                        if _is_rpc_transport_failure(e):
                            self._wait_after_rpc_timeout(migrated_db, module.name)
                        raise
                elif module.state in ["installed", "to upgrade", "uninstallable"]:
                    # modules removed by the registry reload of this
                    # uninstallation: the requested module, its downstream
                    # dependencies and the modules already scheduled for
                    # removal. Their module records are cleaned up together once
                    # the reload is done.
                    cleanup_ids = (
                        {module.id}
                        | set(module.downstream_dependencies() or [])
                        | scheduled_to_remove_ids
                    )
                    self._uninstall_module(
                        module, module_obj, cleanup_ids, migrated_db, config_id
                    )
            return modules
        else:
            logger.info("Module %s not found" % module_name)
            return False
