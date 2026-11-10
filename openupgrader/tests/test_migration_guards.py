import os
import socket
import subprocess
import tempfile
import types
from unittest.mock import Mock, patch

import odoorpc

from odoo.exceptions import UserError
from odoo.tests.common import SavepointCase, tagged

from ..models import openupgrader_migration as migration_module
from ..models import tools as tools_module


def _activity_transaction(
    pid=1, state="active", wait_event_type="", age=10, state_age=1
):
    """One row as returned by ``_get_migrated_db_open_transactions``."""
    return {
        "pid": pid,
        "state": state,
        "wait_event_type": wait_event_type,
        "wait_event": "",
        "xact_age": age,
        "state_age": state_age,
        "query": "DELETE FROM mail_message WHERE model in (...)",
    }


class _FakeRecordSet(list):
    """Minimal stand-in for a one2many union (``a | b``)."""

    def __or__(self, other):
        return _FakeRecordSet(list(self) + list(other))


class _FakeAutoInstallModule:
    def __init__(self, name, module_to_install_name):
        self.name = name
        self.module_to_install_name = module_to_install_name


class _FakeModule:
    """Stand-in for ``ir.module.module`` records of the migrated instance."""

    def __init__(self, name, error=None):
        self.name = name
        self.error = error
        self.install_calls = 0

    def button_immediate_install(self):
        self.install_calls += 1
        if self.error:
            raise self.error


class _FakeUninstallModule:
    """Stand-in for an uninstalled ``ir.module.module`` record."""

    def __init__(self, name, state):
        self.name = name
        self.state = state
        self.id = abs(hash(name)) % 1000
        self.install_cancelled = 0
        self.uninstall_cancelled = 0
        self.unlinked = 0
        self.written_states = []

    def button_install_cancel(self):
        self.install_cancelled += 1
        self.state = "uninstalled"

    def button_uninstall_cancel(self):
        self.uninstall_cancelled += 1
        self.state = "installed"

    def unlink(self):  # pylint: disable=method-required-super
        self.unlinked += 1

    def write(self, values):  # pylint: disable=method-required-super
        self.written_states.append(values.get("state"))
        self.state = values.get("state", self.state)


class _FakeEnv(dict):
    """Minimal stand-in for the ``odoorpc`` environment (dict + ``db``)."""

    def __init__(self, db):
        super().__init__()
        self.db = db


class _FakeConfig:
    """Stand-in for the target ``openupgrader.config`` of the step."""

    def __init__(self, modules, install_failed_modules=None):
        self.name = "18.0"
        self.module_auto_install_ids = _FakeRecordSet(modules)
        self.renamed_module_ids = _FakeRecordSet()
        self.install_failed_modules = dict(install_failed_modules or {})
        self.saved_install_failed_modules = []
        self.pip_installed = []

    def _compute_module_installed_ids(self):
        return None

    def get_install_failed_modules(self):
        return dict(self.install_failed_modules)

    def set_install_failed_modules(self, install_failed_modules):
        self.install_failed_modules = dict(install_failed_modules)
        self.saved_install_failed_modules.append(dict(install_failed_modules))

    def install_pip_modules(self, module_name):
        self.pip_installed.append(module_name)


@tagged("post_install", "-at_install")
class MigrationGuardTest(SavepointCase):
    """Unit tests for the migrated-database isolation/serialization guards."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = cls.env["openupgrader.migration"].create(
            {
                "folder": "/tmp/openupgrader_test_migration",
                "xmlrpc_port": "8099",
            }
        )

    def test_migrated_db_name_never_the_working_db(self):
        self.assertEqual(
            self.migration._get_migrated_db_name(),
            "%s_migrate" % self.env.cr.dbname,
        )

    def test_lock_is_reentrant(self):
        # the same transaction may take the lock more than once
        self.migration._lock_migrated_db()
        self.migration._lock_migrated_db()

    def test_process_uses_database(self):
        cases = [
            (["python", "odoo-bin", "-d", "x_migrate"], True),
            (["python", "odoo-bin", "--database=x_migrate"], True),
            (["python", "odoo-bin", "-dx_migrate"], True),
            (["python", "odoo-bin", "-d", "x"], False),
            (["psql", "-d", "x_migrate"], True),
        ]
        for cmdline, expected in cases:
            with patch.object(
                tools_module, "_get_process_cmdline", return_value=cmdline
            ):
                self.assertIs(
                    tools_module._process_uses_database(1, "x_migrate"), expected
                )

    def test_process_is_odoo_instance(self):
        cases = [
            (["python", "/venv/repos/odoo/odoo-bin", "-d", "x_migrate"], True),
            (["python", "/venv/odoo/openerp-server"], True),
            (["python", "-m", "odoo"], True),
            (["/usr/bin/psql", "-d", "x_migrate"], False),
            (["/usr/bin/pg_restore", "-d", "x_migrate"], False),
        ]
        for cmdline, expected in cases:
            with patch.object(
                tools_module, "_get_process_cmdline", return_value=cmdline
            ):
                self.assertIs(tools_module._process_is_odoo_instance(1), expected)

    def test_process_is_batch_update(self):
        cases = [
            (["python", "odoo-bin", "-d", "x_migrate", "-u", "all", "--stop"], True),
            (["python", "odoo-bin", "-d", "x_migrate", "--stop-after-init"], True),
            (["python", "odoo-bin", "-d", "x_migrate", "-uall"], True),
            (["python", "odoo-bin", "-d", "x_migrate", "--update=all"], True),
            (["python", "odoo-bin", "-d", "x_migrate", "--load=base,web"], False),
            (["python", "odoo-bin", "-d", "x_migrate", "--db_user=odoo"], False),
        ]
        for cmdline, expected in cases:
            with patch.object(
                tools_module, "_get_process_cmdline", return_value=cmdline
            ):
                self.assertIs(tools_module._process_is_batch_update(1), expected)

    def test_get_odoo_pids_only_odoo_processes(self):
        cmdlines = {
            1: ["python", "odoo-bin", "-d", "x_migrate"],
            2: ["psql", "-d", "x_migrate"],
            3: ["python", "odoo-bin", "-d", "x"],
            4: ["python", "odoo-bin", "--database=x_migrate", "-u", "all"],
        }
        processes = [types.SimpleNamespace(pid=pid) for pid in cmdlines]
        with patch.object(
            tools_module, "_get_process_cmdline", side_effect=cmdlines.get
        ), patch.object(tools_module.psutil, "process_iter", return_value=processes):
            self.assertEqual(tools_module._get_odoo_pids("x"), [1, 4])
            self.assertEqual(tools_module._get_odoo_batch_pids("x"), [4])

    def test_count_open_transactions_uses_postgres_and_xact_start(self):
        result = subprocess.CompletedProcess([], 0, "3\n", "")
        with patch.object(migration_module, "run", return_value=result) as run_mock:
            self.assertEqual(
                self.migration._count_migrated_db_open_transactions("x_migrate"),
                3,
            )
        command = run_mock.call_args[0][0][0]
        self.assertIn("psql -d postgres", command)
        self.assertIn("xact_start IS NOT NULL", command)
        self.assertIn("datname = 'x_migrate'", command)

    def test_count_open_transactions_fails_closed(self):
        result = subprocess.CompletedProcess([], 1, "", "connection refused")
        with patch.object(migration_module, "run", return_value=result):
            with self.assertRaises(UserError):
                self.migration._count_migrated_db_open_transactions("x_migrate")

    def test_count_open_transactions_fails_closed_on_bad_output(self):
        result = subprocess.CompletedProcess([], 0, "not a number\n", "")
        with patch.object(migration_module, "run", return_value=result):
            with self.assertRaises(UserError):
                self.migration._count_migrated_db_open_transactions("x_migrate")

    def test_wait_migrated_db_idle_waits_then_returns(self):
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "_check_no_migration_batch_running"
        ), patch.object(
            migration_class,
            "_get_migrated_db_open_transactions",
            side_effect=[
                [_activity_transaction(pid=1), _activity_transaction(pid=2)],
                [],
            ],
        ), patch.object(migration_module.time, "sleep"):
            self.migration._wait_migrated_db_idle("x_migrate")

    def test_migration_instance_ports_avoid_working_instance(self):
        working_ports = {"http_port": 8069, "longpolling_port": 8072}
        with patch.object(
            migration_module.config,
            "get",
            side_effect=lambda key, default=None: working_ports.get(key, default),
        ):
            self.migration.xmlrpc_port = "8099"
            self.assertEqual(
                self.migration._get_migration_instance_ports(), (8099, 8100)
            )
            # the websocket port skips the working longpolling port
            self.migration.xmlrpc_port = "8071"
            self.assertEqual(
                self.migration._get_migration_instance_ports(), (8071, 8073)
            )
            # the RPC port may never be the working instance one
            self.migration.xmlrpc_port = "8072"
            with self.assertRaises(UserError):
                self.migration._get_migration_instance_ports()

    def test_python_commands_use_the_target_odoo_source(self):
        """The shell must not follow an odoo-bin symlink to the system Odoo."""
        with tempfile.TemporaryDirectory() as folder:
            os.makedirs(os.path.join(folder, ".venv", "bin"))
            os.makedirs(os.path.join(folder, "repos", "odoo"))
            config = Mock()
            config.name = "18.0"
            config.python_version = "3.12.11"
            migration_class = type(self.migration)
            with patch.object(migration_class, "check_venv", return_value=folder):
                self.migration.folder = folder
                command, cwd, env = self.migration._get_python_command_shell_command(
                    config, "x_migrate"
                )
        self.assertEqual(cwd, folder)
        self.assertIn("odoo_shell_runner.py shell", command)
        self.assertEqual(env["PYTHONPATH"], os.path.join(folder, "repos", "odoo"))
        self.assertIn(
            os.path.join(folder, "repos", "odoo", "addons"),
            env["OPENUPGRADER_ODOO_ADDONS"],
        )

    def _run_auto_install_modules(
        self, config, modules_by_name, reconnect_fails=False, connect_fails=False
    ):
        """Run ``auto_install_modules`` with the RPC layer mocked out.

        :param reconnect_fails: make the connections after the first one fail
        :param connect_fails: make every connection fail
        :return: ``(result, connect_mock, terminate_mock)``
        """
        module_obj = Mock()
        # both searches (old module installed? module to install?) return its name
        module_obj.search.side_effect = lambda domain, limit=None: [domain[0][2]]
        module_obj.browse.side_effect = lambda ids: modules_by_name[ids[0]]
        client = Mock()
        client.env = {"ir.module.module": module_obj}
        connected = []

        def _connect(*args, **kwargs):
            connected.append(1)
            if connect_fails or (reconnect_fails and len(connected) > 1):
                return None
            return client

        migration_class = type(self.migration)
        with patch.object(migration_class, "_lock_migrated_db"), patch.object(
            migration_class, "start_odoo"
        ), patch.object(
            migration_class, "odoo_connect", side_effect=_connect
        ) as connect_mock, patch.object(
            migration_class, "_get_migrated_db_name", return_value="x_migrate"
        ), patch.object(migration_class, "_wait_migrated_db_idle"), patch.object(
            # the probe would need a real PostgreSQL connection: x_migrate is
            # empty, so the timed-out operation has nothing left to wait for
            migration_class,
            "_get_migrated_db_open_transactions",
            return_value=[],
        ), patch.object(migration_class, "button_stop_odoo"), patch.object(
            migration_class, "_terminate_migrated_db_backends"
        ) as terminate_mock:
            result = self.migration.auto_install_modules(config)
        return result, connect_mock, terminate_mock

    def test_module_install_timeout_does_not_abort_the_step(self):
        """A timed-out install is recorded, the next module is still installed."""
        timeout_module = _FakeModule("new_a", socket.timeout("timed out"))
        ok_module = _FakeModule("new_b")
        config = _FakeConfig(
            [
                _FakeAutoInstallModule("old_a", "new_a"),
                _FakeAutoInstallModule("old_b", "new_b"),
            ]
        )
        result, connect_mock, terminate_mock = self._run_auto_install_modules(
            config, {"new_a": timeout_module, "new_b": ok_module}
        )
        self.assertEqual(
            (timeout_module.install_calls, ok_module.install_calls), (1, 1)
        )
        self.assertEqual(config.install_failed_modules, {"new_a": 1})
        self.assertEqual(result["failed"], ["new_a"])
        self.assertEqual(result["not_processed"], [])
        # the server side is awaited, not killed
        terminate_mock.assert_not_called()
        self.assertEqual(connect_mock.call_count, 2)

    def test_module_install_error_does_not_abort_the_step(self):
        """A plain RPC error is recorded, the next module is still installed."""
        failing_module = _FakeModule("new_a", Exception("RPC error"))
        ok_module = _FakeModule("new_b")
        config = _FakeConfig(
            [
                _FakeAutoInstallModule("old_a", "new_a"),
                _FakeAutoInstallModule("old_b", "new_b"),
            ]
        )
        result, connect_mock, terminate_mock = self._run_auto_install_modules(
            config, {"new_a": failing_module, "new_b": ok_module}
        )
        self.assertEqual(ok_module.install_calls, 1)
        self.assertEqual(config.install_failed_modules, {"new_a": 1})
        self.assertEqual(result["failed"], ["new_a"])
        # the session is still usable: nothing is killed, nothing reconnects
        self.assertEqual(connect_mock.call_count, 1)
        terminate_mock.assert_not_called()

    def test_module_install_session_lost_reconnects(self):
        """A lost connection is handled like a timeout, not like a module error."""
        broken_module = _FakeModule("new_a", ConnectionResetError("reset"))
        ok_module = _FakeModule("new_b")
        config = _FakeConfig(
            [
                _FakeAutoInstallModule("old_a", "new_a"),
                _FakeAutoInstallModule("old_b", "new_b"),
            ]
        )
        result, connect_mock, terminate_mock = self._run_auto_install_modules(
            config, {"new_a": broken_module, "new_b": ok_module}
        )
        self.assertEqual(ok_module.install_calls, 1)
        self.assertEqual(result["failed"], ["new_a"])
        terminate_mock.assert_not_called()
        self.assertEqual(connect_mock.call_count, 2)

    def test_module_install_unreachable_instance_is_reported(self):
        """Modules never tried are reported, so the step is not marked done."""
        timeout_module = _FakeModule("new_a", socket.timeout("timed out"))
        ok_module = _FakeModule("new_b")
        config = _FakeConfig(
            [
                _FakeAutoInstallModule("old_a", "new_a"),
                _FakeAutoInstallModule("old_b", "new_b"),
            ]
        )
        result, connect_mock, _terminate_mock = self._run_auto_install_modules(
            config,
            {"new_a": timeout_module, "new_b": ok_module},
            reconnect_fails=True,
        )
        self.assertEqual(ok_module.install_calls, 0)
        self.assertEqual(result["not_processed"], ["new_b"])
        self.assertEqual(connect_mock.call_count, 2)

    def test_module_install_no_connection_is_reported(self):
        """No connection at all: every module of the step is reported."""
        config = _FakeConfig([_FakeAutoInstallModule("old_a", "new_a")])
        result, connect_mock, _terminate_mock = self._run_auto_install_modules(
            config, {"new_a": _FakeModule("new_a")}, connect_fails=True
        )
        self.assertEqual(result["not_processed"], ["new_a"])
        self.assertEqual(connect_mock.call_count, 1)

    def test_module_install_skipped_after_max_retries(self):
        """A module that already failed enough times is not retried."""
        module = _FakeModule("new_a")
        config = _FakeConfig(
            [_FakeAutoInstallModule("old_a", "new_a")],
            install_failed_modules={"new_a": migration_module.MAX_INSTALL_MODULE_RETRY},
        )
        result, _connect_mock, _terminate_mock = self._run_auto_install_modules(
            config, {"new_a": module}
        )
        self.assertEqual(module.install_calls, 0)
        self.assertEqual(result["skipped"], ["new_a"])
        self.assertEqual(config.pip_installed, [])
        self.assertEqual(config.saved_install_failed_modules, [])

    def test_module_install_failure_count_reset_on_success(self):
        """A successful installation forgets the previous failures."""
        module = _FakeModule("new_a")
        config = _FakeConfig(
            [_FakeAutoInstallModule("old_a", "new_a")],
            install_failed_modules={"new_a": 1},
        )
        self._run_auto_install_modules(config, {"new_a": module})
        self.assertEqual(module.install_calls, 1)
        self.assertEqual(config.install_failed_modules, {})

    def test_rpc_timeout_stop_does_not_raise(self):
        """Installs go on after a timeout stop, uninstalls still abort."""
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "button_stop_odoo"
        ) as stop_mock, patch.object(
            migration_class, "_terminate_migrated_db_backends"
        ) as terminate_mock:
            self.migration._stop_after_rpc_timeout("x_migrate", "new_a")
        stop_mock.assert_called_once_with(force=True)
        terminate_mock.assert_called_once_with("x_migrate")
        with patch.object(migration_class, "button_stop_odoo"), patch.object(
            migration_class, "_terminate_migrated_db_backends"
        ):
            with self.assertRaises(UserError):
                self.migration._abort_on_rpc_timeout("x_migrate", "new_a")

    def test_wait_after_rpc_timeout_waits_instead_of_killing(self):
        """A slow server-side operation is awaited, not thrown away."""
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "_wait_active_module_operation"
        ) as wait_mock, patch.object(
            migration_class, "_stop_after_rpc_timeout"
        ) as stop_mock:
            self.assertTrue(self.migration._wait_after_rpc_timeout("x_migrate", "m"))
        wait_mock.assert_called_once_with("x_migrate", "m")
        stop_mock.assert_not_called()

    def test_wait_after_rpc_timeout_stops_a_stuck_operation(self):
        """An operation that never ends is stopped, so the retry can start."""
        migration_class = type(self.migration)
        with patch.object(
            migration_class,
            "_wait_active_module_operation",
            side_effect=migration_module.MigratedDbBusyError("still busy"),
        ), patch.object(migration_class, "_stop_after_rpc_timeout") as stop_mock:
            self.assertFalse(self.migration._wait_after_rpc_timeout("x_migrate", "m"))
        stop_mock.assert_called_once_with("x_migrate", "m")

    def test_set_migrated_modules_to_remove(self):
        """The pending removal is written with SQL, without the RPC session."""
        result = subprocess.CompletedProcess([], 0, "", "")
        migration_class = type(self.migration)
        with patch.object(
            migration_module, "run", return_value=result
        ) as run_mock, patch.object(
            migration_class, "_get_migrated_db_name", return_value="x_migrate"
        ):
            self.migration._set_migrated_modules_to_remove(["b", "a"])
        command = run_mock.call_args[0][0][0]
        self.assertIn("-d x_migrate", command)
        self.assertIn("name IN ('a','b')", command)
        self.assertIn("state = 'to remove'", command)
        # only installed modules become removal candidates, and the driver
        # must not wait forever for the row lock
        self.assertIn("state = 'installed'", command)
        self.assertIn("lock_timeout", command)
        # invalid names are refused instead of being put in the shell string
        with patch.object(migration_module, "run") as run_mock:
            self.assertFalse(
                self.migration._set_migrated_modules_to_remove(["a'; DROP TABLE x"])
            )
        run_mock.assert_not_called()

    def test_server_error_mentioning_timeout_is_not_a_client_timeout(self):
        """A PostgreSQL lock timeout is an answer, not a lost session."""
        lock_timeout = odoorpc.error.RPCError("canceling statement: lock timeout")
        self.assertFalse(migration_module._is_rpc_timeout(lock_timeout))
        self.assertFalse(migration_module._is_rpc_transport_failure(lock_timeout))
        self.assertTrue(migration_module._is_rpc_timeout(socket.timeout("timed out")))
        self.assertTrue(
            migration_module._is_rpc_transport_failure(
                ConnectionResetError("connection reset by peer")
            )
        )

    def test_mail_purge_also_purges_attachments(self):
        """The big ir_attachment delete is batched before the uninstallation too."""
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "_get_module_model_names", return_value=["old.model"]
        ), patch.object(
            migration_class, "_get_migrated_db_name", return_value="x_migrate"
        ), patch.object(migration_class, "_wait_active_module_operation"), patch.object(
            migration_class, "_purge_attachment_data"
        ) as attachment_mock, patch.object(
            migration_module,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "0\n", ""),
        ):
            self.migration._purge_mail_data(["old_module"])
        attachment_mock.assert_called_once_with(["old.model"])

    def test_attachment_purge_is_batched_and_marks_the_filestore(self):
        """Deleted attachment files go to the filestore GC checklist."""
        batches = [
            subprocess.CompletedProcess([], 0, "2|ab/aaa,cd/bbb\n", ""),
            subprocess.CompletedProcess([], 0, "0|\n", ""),
        ]
        migration_class = type(self.migration)
        with tempfile.TemporaryDirectory() as folder, patch.object(
            migration_class, "_get_migrated_db_name", return_value="x_migrate"
        ), patch.object(migration_module, "run", side_effect=batches) as run_mock:
            self.migration.folder = folder
            deleted = self.migration._purge_attachment_data(["fatturapa.attachment"])
            checklist = os.path.join(
                folder, "data_dir", "filestore", "x_migrate", "checklist"
            )
            self.assertTrue(os.path.isfile(os.path.join(checklist, "ab", "aaa")))
            self.assertTrue(os.path.isfile(os.path.join(checklist, "cd", "bbb")))
        self.assertEqual(deleted, 2)
        command = run_mock.call_args_list[0][0][0][0]
        self.assertIn("DELETE FROM ir_attachment", command)
        self.assertIn("res_model IN ('fatturapa.attachment')", command)
        self.assertIn(
            "LIMIT %s" % migration_module.ATTACHMENT_PURGE_BATCH_SIZE, command
        )

    # -- uninstallation of the modules left in 'to remove' -------------------

    def test_pending_modules_are_only_the_to_remove_ones(self):
        """Leftovers of an interrupted update are never uninstalled."""
        states = {
            "to upgrade": ["leftover_upgrade"],
            "to remove": ["old_a", "old_b"],
            "to install": ["leftover_install"],
            "pending": [
                "leftover_upgrade",
                "old_a",
                "leftover_install",
                "old_b",
            ],
        }
        config = Mock()
        config.get_uninstall_modules_retry_number.return_value = 0
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "_verify_module_states", return_value=states
        ), patch.object(migration_class, "current_config_id", new=config), patch.object(
            migration_module.logger, "warning"
        ) as warn_mock:
            self.assertEqual(self.migration.get_pending_modules(), ["old_a", "old_b"])
        self.assertIn("leftover_upgrade", str(warn_mock.call_args_list))
        self.assertIn("leftover_install", str(warn_mock.call_args_list))

    def test_pending_modules_empty_resets_the_retry_counter(self):
        states = {"to upgrade": [], "to remove": [], "to install": []}
        config = Mock()
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "_verify_module_states", return_value=states
        ), patch.object(migration_class, "current_config_id", new=config):
            self.assertEqual(self.migration.get_pending_modules(), [])
        config.reset_uninstall_modules_retry_number.assert_called_once()
        config.get_uninstall_modules_retry_number.assert_not_called()

    def test_pending_modules_skipped_after_max_retries(self):
        states = {"to upgrade": [], "to remove": ["old_a"], "to install": []}
        config = Mock()
        config.get_uninstall_modules_retry_number.return_value = (
            migration_module.MAX_UNINSTALL_MODULES_RETRY
        )
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "_verify_module_states", return_value=states
        ), patch.object(migration_class, "current_config_id", new=config):
            self.assertEqual(self.migration.get_pending_modules(), [])

    def test_uninstall_pending_modules_does_not_run_a_full_update(self):
        """Pending modules are uninstalled by the running instance, no -u all."""
        config = Mock()
        config.name = "18.0"
        config.increment_uninstall_modules_retry_number.return_value = 1
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "get_pending_modules", return_value=["old_a", "old_b"]
        ), patch.object(migration_class, "current_config_id", new=config), patch.object(
            migration_class, "start_odoo"
        ) as start_mock, patch.object(
            migration_class, "install_uninstall_module"
        ) as uninstall_mock, patch.object(
            migration_class, "_purge_mail_data"
        ), patch.object(
            # the probe needs a real PostgreSQL connection: the fake x_migrate
            # database is empty, so there is nothing left to wait for
            migration_class,
            "_wait_migrated_db_idle",
        ), patch.object(migration_class, "button_stop_odoo") as stop_mock, patch.object(
            migration_class, "_get_pending_modules_to_uninstall", return_value=[]
        ), patch.object(
            migration_class, "_get_migrated_module_state", return_value="uninstalled"
        ):
            self.migration.uninstall_pending_modules()
        start_mock.assert_called_once_with(config)
        self.assertEqual(
            [call[0] for call in uninstall_mock.call_args_list],
            [("old_a", config), ("old_b", config)],
        )
        self.assertEqual(
            [call[1] for call in uninstall_mock.call_args_list],
            [{"install": False}, {"install": False}],
        )
        stop_mock.assert_called_once()
        self.assertEqual(self.migration.pending_modules, "[]")

    def test_uninstalled_module_record_deleted_after_reload_to_install(self):
        """OpenUpgrade puts a module back 'to install'; it is cancelled + deleted."""
        module = _FakeUninstallModule("old_a", "to install")
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "_get_migrated_module_state", return_value="to install"
        ):
            self.assertTrue(self.migration._remove_uninstalled_module_record(module))
        self.assertEqual((module.install_cancelled, module.unlinked), (1, 1))

    def test_uninstalled_module_record_reinstalled_is_kept(self):
        module = _FakeUninstallModule("old_a", "installed")
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "_get_migrated_module_state", return_value="installed"
        ):
            self.assertFalse(self.migration._remove_uninstalled_module_record(module))
        self.assertEqual((module.install_cancelled, module.unlinked), (0, 0))

    def test_uninstalled_module_record_deleted(self):
        module = _FakeUninstallModule("old_a", "uninstalled")
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "_get_migrated_module_state", return_value="uninstalled"
        ):
            self.assertTrue(self.migration._remove_uninstalled_module_record(module))
        self.assertEqual((module.install_cancelled, module.unlinked), (0, 1))

    def _run_uninstall_module(self, module, states, timeout=True):
        """Run ``_uninstall_module`` with the RPC layer replaced by fakes."""
        module_obj = Mock()
        module_obj.search.return_value = [module.id]
        module_obj.browse.return_value = module
        client = Mock()
        client.env = _FakeEnv("x_migrate")
        client.env["ir.module.module"] = module_obj
        migration_class = type(self.migration)
        with patch.object(migration_class, "_wait_migrated_db_idle"), patch.object(
            migration_class, "_wait_after_rpc_timeout", return_value=True
        ) as wait_mock, patch.object(
            migration_class, "odoo_connect", return_value=client
        ), patch.object(
            migration_class, "_get_migrated_module_state", side_effect=states
        ), patch.object(
            migration_class, "_remove_uninstalled_module_records"
        ) as cleanup_mock, patch.object(migration_module.time, "sleep"):
            self.migration._uninstall_module(
                module, module_obj, {module.id}, "x_migrate", Mock()
            )
        return wait_mock, cleanup_mock

    def test_uninstall_timeout_waits_for_the_finished_uninstallation(self):
        """A timed-out uninstall that did finish only leaves the records."""
        module = _FakeUninstallModule("old_a", "installed")
        module.button_immediate_uninstall = Mock(
            side_effect=socket.timeout("timed out")
        )
        wait_mock, cleanup_mock = self._run_uninstall_module(module, ["", ""])
        wait_mock.assert_called_once_with("x_migrate", "old_a")
        cleanup_mock.assert_called_once()
        # no second RPC call: the uninstallation was already done
        self.assertEqual(module.button_immediate_uninstall.call_count, 1)

    def test_uninstall_timeout_retries_when_the_module_is_still_installed(self):
        """A timed-out uninstall that did not finish is retried."""
        module = _FakeUninstallModule("old_a", "installed")
        module.button_immediate_uninstall = Mock(
            side_effect=[socket.timeout("timed out"), None]
        )
        wait_mock, cleanup_mock = self._run_uninstall_module(
            module, ["installed", "uninstalled"]
        )
        wait_mock.assert_called_once_with("x_migrate", "old_a")
        self.assertEqual(module.button_immediate_uninstall.call_count, 2)
        cleanup_mock.assert_called_once()

    def test_restore_pending_removal_falls_back_to_sql(self):
        """A dead session must not lose the removal candidate."""
        module = _FakeUninstallModule("old_a", "installed")
        module.write = Mock(side_effect=ConnectionResetError("reset"))
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "_get_migrated_module_state", return_value="installed"
        ), patch.object(migration_class, "_set_migrated_modules_to_remove") as sql_mock:
            self.migration._restore_pending_removal(module)
        sql_mock.assert_called_once_with(["old_a"])

    def test_busy_database_is_not_a_general_guard_error(self):
        """Only the busy timeout leads to a force stop."""
        migration_class = type(self.migration)
        with patch.object(
            migration_class,
            "_wait_active_module_operation",
            side_effect=UserError("a batch update is running"),
        ), patch.object(migration_class, "_stop_after_rpc_timeout") as stop_mock:
            with self.assertRaises(UserError):
                self.migration._wait_after_rpc_timeout("x_migrate", "m")
        stop_mock.assert_not_called()

    def test_migrated_module_state_is_read_with_sql(self):
        result = subprocess.CompletedProcess([], 0, "to remove\n", "")
        with patch.object(migration_module, "run", return_value=result) as run_mock:
            self.assertEqual(
                self.migration._get_migrated_module_state("old_a"), "to remove"
            )
        command = run_mock.call_args[0][0][0]
        self.assertIn("psql -d", command)
        self.assertIn("SELECT state FROM ir_module_module", command)
        self.assertIn("'old_a'", command)

    def test_uninstall_cancels_only_the_requested_module(self):
        """The other 'to remove' modules stay candidates for their own call."""
        requested = _FakeUninstallModule("old_a", "to remove")
        other = _FakeUninstallModule("old_b", "to remove")
        module_obj = Mock()
        # first search: the 'to remove' modules, second one: the requested
        # module is no longer 'to remove' after the cancel and is not found
        module_obj.search.side_effect = [[requested.id, other.id], []]
        module_obj.browse.side_effect = lambda ids: [
            {requested.id: requested, other.id: other}[module_id] for module_id in ids
        ]
        fake_env = _FakeEnv("x_migrate")
        fake_env["ir.module.module"] = module_obj
        client = Mock()
        client.env = fake_env
        migration_class = type(self.migration)
        with patch.object(migration_class, "_lock_migrated_db"), patch.object(
            migration_class, "odoo_connect", return_value=client
        ), patch.object(
            migration_class, "_get_migrated_db_name", return_value="x_migrate"
        ), patch.object(migration_class, "_wait_migrated_db_idle"):
            self.migration.install_uninstall_module("old_a", Mock(), install=False)
        self.assertEqual(
            (requested.uninstall_cancelled, other.uninstall_cancelled), (1, 0)
        )

    def test_uninstalled_module_records_cleaned_up_together(self):
        """Modules uninstalled by the same reload are all cleaned up."""
        first = _FakeUninstallModule("old_a", "to install")
        second = _FakeUninstallModule("old_b", "to install")
        with patch.object(
            type(self.migration),
            "_get_migrated_module_state",
            return_value="to install",
        ):
            self.assertEqual(
                self.migration._remove_uninstalled_module_records([first, second]),
                ["old_a", "old_b"],
            )
        self.assertEqual(
            (
                first.install_cancelled,
                first.unlinked,
                second.install_cancelled,
                second.unlinked,
            ),
            (1, 1, 1, 1),
        )

    def test_uninstalled_module_record_kept_in_unexpected_state(self):
        """Only a re-added 'to install' state is cancelled, then unlinked."""
        for state in ("installed", "to remove", "to upgrade", None):
            module = _FakeUninstallModule("old_a", state)
            with patch.object(
                type(self.migration),
                "_get_migrated_module_state",
                return_value=state,
            ):
                self.assertFalse(
                    self.migration._remove_uninstalled_module_record(module)
                )
            self.assertEqual((module.install_cancelled, module.unlinked), (0, 0), state)

    def test_uninstalled_module_record_missing_row_is_unlinked(self):
        module = _FakeUninstallModule("old_a", "")
        with patch.object(
            type(self.migration), "_get_migrated_module_state", return_value=""
        ):
            self.assertTrue(self.migration._remove_uninstalled_module_record(module))
        self.assertEqual((module.install_cancelled, module.unlinked), (0, 1))

    def test_uninstall_pending_modules_stops_the_instance_on_error(self):
        config = Mock()
        config.name = "18.0"
        config.increment_uninstall_modules_retry_number.return_value = 1
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "get_pending_modules", return_value=["old_a"]
        ), patch.object(migration_class, "current_config_id", new=config), patch.object(
            migration_class, "start_odoo"
        ), patch.object(
            migration_class,
            "install_uninstall_module",
            side_effect=UserError("uninstall failed"),
        ), patch.object(
            migration_class, "_get_migrated_db_name", return_value="x_migrate"
        ), patch.object(
            migration_class, "_wait_migrated_db_idle"
        ) as idle_mock, patch.object(migration_class, "button_stop_odoo") as stop_mock:
            with self.assertRaises(UserError):
                self.migration.uninstall_pending_modules()
        idle_mock.assert_called_once_with("x_migrate")
        stop_mock.assert_called_once()

    def test_pending_modules_without_current_version_is_empty(self):
        states = {"to upgrade": [], "to remove": ["old_a"], "to install": []}
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "_verify_module_states", return_value=states
        ), patch.object(migration_class, "current_config_id", new=False):
            self.assertEqual(self.migration.get_pending_modules(), [])

    def test_module_states_read_fails_closed(self):
        """An unreadable module state must never mean 'nothing pending'."""
        result = subprocess.CompletedProcess([], 1, "", "connection refused")
        with patch.object(migration_module, "run", return_value=result):
            with self.assertRaises(UserError):
                self.migration._verify_module_states()

    def test_failed_uninstall_leaves_the_module_pending(self):
        """A module cancelled before a failed uninstall is pending again."""
        module = _FakeUninstallModule("old_a", "installed")
        migration_class = type(self.migration)
        with patch.object(
            migration_class, "_get_migrated_module_state", return_value="installed"
        ):
            self.migration._restore_pending_removal(module)
        self.assertEqual(module.state, "to remove")

    def test_already_removed_module_is_not_pending_again(self):
        module = _FakeUninstallModule("old_a", "")
        migration_class = type(self.migration)
        for state in ("", "uninstalled", "uninstallable", "to remove", None):
            module.state = state
            module.written_states = []
            with patch.object(
                migration_class, "_get_migrated_module_state", return_value=state
            ):
                self.migration._restore_pending_removal(module)
            self.assertEqual(module.written_states, [], state)
