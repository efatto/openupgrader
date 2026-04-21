import base64
import time
from subprocess import PIPE, Popen

from odoo.modules import get_module_resource
from odoo.release import version_info
from odoo.tests.common import Form, SavepointCase, tagged


@tagged("post_install", "-at_install")
class OpenUpgrader(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config_obj = cls.env["openupgrader.config"]
        cls.migration_obj = cls.env["openupgrader.migration"]
        cls.from_version = ".".join(str(v) for v in version_info[:2])
        cls.middle_version = str(float(cls.from_version) + 1)
        cls.middle_version1 = str(float(cls.from_version) + 2)
        cls.middle_version2 = str(float(cls.from_version) + 3)
        cls.to_version = str(float(cls.from_version) + 4)
        versions = {
            "from_version": cls.from_version,
            "middle_version": cls.middle_version,
            "middle_version1": cls.middle_version1,
            "middle_version2": cls.middle_version2,
            "to_version": cls.to_version,
        }
        cls.from_config_id = cls.config_obj
        cls.middle_config_id = cls.config_obj
        cls.middle1_config_id = cls.config_obj
        cls.middle2_config_id = cls.config_obj
        cls.to_config_id = cls.config_obj
        cls.openupgrader_migration = cls.migration_obj.search([])
        if not cls.openupgrader_migration:
            migration_form = Form(cls.migration_obj)
            migration_form.openupgrade_repo = (
                "https://github.com/efatto/OpenUpgrade.git"
            )
            migration_form.pg_password_var = "$PGPASSWORD"  # PGPASSWORD=odoo
            migration_form.pg_host = "$PGHOST"  # PGHOST=postgres
            migration_form.pg_user = "$PGUSER"  # PGUSER=odoo
            cls.openupgrader_migration = migration_form.save()
        else:
            cls.openupgrader_migration.button_draft()
        config_file_path = get_module_resource(
            "openupgrader", "tests", "data", "openupgrader_config.yml"
        )
        with open(config_file_path, "rb") as config_file_reader:
            cls.openupgrader_migration.config_file = base64.b64encode(
                config_file_reader.read()
            )
        for version in versions:
            openupgrader_config = cls.config_obj.search(
                [
                    ("name", "=", versions[version]),
                ]
            )
            if not openupgrader_config:
                openupgrader_config_form = Form(cls.config_obj)
                openupgrader_config_form.name = versions[version]
                openupgrader_config_form.openupgrader_migration_id = (
                    cls.openupgrader_migration
                )
                openupgrader_config = openupgrader_config_form.save()
                setattr(cls, f"{version}_id", openupgrader_config)
                openupgrader_config.button_recreate_venv()
        cls.from_config_id = cls.config_obj.search(
            [
                ("name", "=", cls.from_version),
            ]
        )
        cls.middle_config_id = cls.config_obj.search(
            [
                ("name", "=", cls.middle_version),
            ]
        )
        cls.middle_config1_id = cls.config_obj.search(
            [
                ("name", "=", cls.middle_version1),
            ]
        )
        cls.middle_config2_id = cls.config_obj.search(
            [
                ("name", "=", cls.middle_version2),
            ]
        )
        cls.to_config_id = cls.config_obj.search(
            [
                ("name", "=", cls.to_version),
            ]
        )
        cls.openupgrader_migration.from_version_id = cls.from_config_id
        cls.openupgrader_migration.to_version_id = cls.to_config_id

    def _check_installed_module(self, openupgrader_migration, module):
        module_installed = False
        conn_vars = openupgrader_migration._get_db_connection_variables()
        sql = f"SELECT state FROM ir_module_module WHERE name = '{module}'"
        process = Popen(
            [f'{conn_vars} && psql -d {self.env.cr.dbname}_migrate -c "{sql}"'],
            shell=True,
            stdout=PIPE,
        )
        has_stdout = True
        while has_stdout:
            one_line_output = process.stdout.readline()
            if one_line_output:
                try:
                    if (
                        b"installed" in one_line_output
                        and b"uninstalled" not in one_line_output
                    ):
                        module_installed = True
                        break
                except (ValueError, IndexError):
                    continue
            else:
                has_stdout = False
        self.assertTrue(module_installed)

    def _test_openupgrader_manual(self, initial_module=False, final_module=False):
        openupgrader_migration = self.openupgrader_migration
        openupgrader_migration.button_clean_logs()
        self.assertEqual(
            self.openupgrader_migration.to_version_id,
            self.to_config_id,
        )
        self.assertEqual(
            self.openupgrader_migration.from_version_id,
            self.from_config_id,
        )
        openupgrader_migration.button_stop_odoo()
        openupgrader_migration.button_restore()
        self.assertEqual(openupgrader_migration.state, "restored")
        self.assertEqual(
            openupgrader_migration.current_version_id,
            self.from_config_id,
        )
        self.assertEqual(
            openupgrader_migration.next_version_id,
            self.middle_config_id,
        )
        openupgrader_migration.button_update_current_version()
        openupgrader_migration.button_update_current_version()
        if initial_module:
            openupgrader_migration.install_pip_modules(
                self.from_config_id, [initial_module]
            )
            openupgrader_migration.start_odoo(self.from_config_id)
            openupgrader_migration.install_uninstall_module(initial_module)
            openupgrader_migration.button_stop_odoo()
            self._check_installed_module(openupgrader_migration, initial_module)
        for config_id in [
            self.middle_config_id,
            self.middle_config1_id,
            self.middle_config2_id,
        ]:
            openupgrader_migration.button_prepare_for_migration()
            self.assertEqual(openupgrader_migration.state, "ready_for_migration")
            openupgrader_migration.button_do_migration()
            # wait until migration is stopped with threading
            while openupgrader_migration._get_odoo_migrated_state() == "running":
                time.sleep(10)
            openupgrader_migration.button_do_migration()
            self.assertEqual(openupgrader_migration.state, "migrated")
            self.assertEqual(
                openupgrader_migration.current_version_id,
                config_id,
            )
        self.assertEqual(
            openupgrader_migration.next_version_id,
            self.to_config_id,
        )
        openupgrader_migration.button_prepare_for_migration()
        openupgrader_migration.button_do_migration()
        # wait until migration is stopped with threading
        while openupgrader_migration._get_odoo_migrated_state() == "running":
            time.sleep(10)
        openupgrader_migration.button_do_migration()
        self.assertEqual(openupgrader_migration.state, "done")
        self.assertEqual(openupgrader_migration.is_migration_done, True)
        self.assertEqual(
            openupgrader_migration.current_version_id,
            self.to_config_id,
        )
        if final_module:
            self._check_installed_module(openupgrader_migration, final_module)

    def _test_00_openupgrader_manual(self):
        self._test_openupgrader_manual(
            initial_module="l10n_it_ricevute_bancarie",
            final_module="l10n_it_riba_oca",
        )

    def test_01_openupgrader_manual(self):
        self._test_openupgrader_manual()

    def test_02_openupgrader_auto(self):
        openupgrader_migration = self.openupgrader_migration
        openupgrader_migration.button_clean_logs()
        self.assertEqual(
            self.openupgrader_migration.to_version_id,
            self.to_config_id,
        )
        self.assertEqual(
            self.openupgrader_migration.from_version_id,
            self.from_config_id,
        )
        openupgrader_migration.button_stop_odoo()
        openupgrader_migration.button_do_all()
        self.assertEqual(openupgrader_migration.state, "done")
        self.assertEqual(openupgrader_migration.is_migration_done, True)
        self.assertEqual(
            openupgrader_migration.current_version_id,
            self.to_config_id,
        )
