import subprocess
import types
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import SavepointCase, tagged

from ..models import openupgrader_migration as migration_module
from ..models import tools as tools_module


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
                self.assertIs(
                    tools_module._process_is_odoo_instance(1), expected
                )

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
                self.assertIs(
                    tools_module._process_is_batch_update(1), expected
                )

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
        ), patch.object(
            tools_module.psutil, "process_iter", return_value=processes
        ):
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
