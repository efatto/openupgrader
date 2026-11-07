import socket
import subprocess
import types
from unittest.mock import Mock, patch

import odoorpc

from odoo.exceptions import UserError
from odoo.tests.common import SavepointCase, tagged

from ..models import openupgrader_migration as migration_module
from ..models import tools as tools_module


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
            "_count_migrated_db_open_transactions",
            side_effect=[2, 0],
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
            migration_class, "button_stop_odoo"
        ), patch.object(
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
        terminate_mock.assert_called_once_with("x_migrate")
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
        terminate_mock.assert_called_once_with("x_migrate")
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
