import base64
import time

from odoo.modules import get_module_resource
from odoo.release import version_info
from odoo.tests.common import Form, SavepointCase


class Openupgrader(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.version_obj = cls.env["odoo.version"]
        cls.migration_obj = cls.env["openupgrader.migration"]
        cls.from_version = ".".join(str(v) for v in version_info[:2])
        cls.middle_version = str(float(cls.from_version) + 1)
        cls.to_version = str(float(cls.middle_version) + 1)
        cls.future_version = str(float(cls.to_version) + 1)
        for version in [
            cls.from_version,
            cls.middle_version,
            cls.to_version,
            cls.future_version,
        ]:
            version_id = cls.version_obj.search(
                [
                    ("name", "=", version),
                ]
            )
            if not version_id:
                cls.version_obj.create(
                    {
                        "name": version,
                        "python_version": "3.8.16",
                    }
                )
        cls.from_version_id = cls.version_obj.search(
            [
                ("name", "=", cls.from_version),
            ]
        )
        cls.middle_version_id = cls.version_obj.search(
            [
                ("name", "=", cls.middle_version),
            ]
        )
        cls.to_version_id = cls.version_obj.search(
            [
                ("name", "=", cls.to_version),
            ]
        )
        cls.future_version_id = cls.version_obj.search(
            [
                ("name", "=", cls.future_version),
            ]
        )
        cls.openupgrader_migration = cls.migration_obj.search([])
        if not cls.openupgrader_migration:
            migration_form = Form(cls.migration_obj)
            migration_form.from_version_id = cls.from_version_id
            migration_form.to_version_id = cls.to_version_id
            migration_form.openupgrade_repo = (
                "https://github.com/efatto/OpenUpgrade.git"
            )
            migration_form.pg_password_var = "$PGPASSWORD"  # PGPASSWORD=odoo
            migration_form.pg_host = "$PGHOST"  # PGHOST=postgres
            migration_form.pg_user = "$PGUSER"  # PGUSER=odoo
            cls.openupgrader_migration = migration_form.save()
        else:
            cls.openupgrader_migration.button_draft()
        for version_name in [cls.from_version, cls.middle_version, cls.to_version]:
            openupgrader_config = cls.env["openupgrader.config"].search(
                [
                    ("odoo_version_id.name", "=", version_name),
                ]
            )
            version_id = cls.version_obj.search(
                [
                    ("name", "=", version_name),
                ]
            )
            if not openupgrader_config:
                openupgrader_config = cls.env["openupgrader.config"].create(
                    {
                        "odoo_version_id": version_id.id,
                    }
                )
            config_file_path = get_module_resource(
                "openupgrader", "tests", "data", "openupgrader_config.yml"
            )
            repos_file_path = get_module_resource(
                "openupgrader", "tests", "data", "openupgrader_repos.yml"
            )
            with open(config_file_path, "rb") as config_file_reader, open(
                repos_file_path, "rb"
            ) as repos_file_reader:
                config_form = Form(openupgrader_config)
                config_form.config_file = base64.b64encode(config_file_reader.read())
                config_form.repos_file = base64.b64encode(repos_file_reader.read())
                openupgrader_config = config_form.save()
            openupgrader_config.button_load_config()
            openupgrader_config.button_load_repos()
        for version in [cls.from_version_id, cls.middle_version_id, cls.to_version_id]:
            version.button_create_venv()

    @staticmethod
    def _install_module(openupgrader_migration, module_name):
        version_id = openupgrader_migration.from_version_id
        openupgrader_migration.install_pip_modules(
            version_id,
            [module_name],
        )
        openupgrader_migration.start_odoo(version_id)
        openupgrader_migration.install_uninstall_module(module_name)
        openupgrader_migration.button_stop_odoo()

    def test_openupgrader(self):
        openupgrader_migration = self.openupgrader_migration
        for version in (
            self.from_version_id | self.middle_version_id | self.to_version_id
        ):
            self.assertTrue(
                expr=version.openupgrader_repo_ids,
                msg="Repos in version %s missing" % version.name,
            )
        # add test modules to migrate
        # install additional modules to test in migration instance
        modules_to_install = ["l10n_it_account_stamp"]
        self._install_module(openupgrader_migration, "l10n_it_account_stamp")
        self.from_version_id.module_installed_ids = [
            (0, 0, {"name": module}) for module in modules_to_install
        ]
        self.assertIn(
            "l10n_it_account_stamp",
            self.from_version_id.module_installed_ids.mapped("name"),
        )
        openupgrader_migration.button_stop_odoo()
        openupgrader_migration.button_restore()
        self.assertEqual(openupgrader_migration.state, "restored")
        self.assertEqual(
            openupgrader_migration.current_version_id,
            self.from_version_id,
        )
        self.assertEqual(
            openupgrader_migration.next_version_id,
            self.middle_version_id,
        )
        openupgrader_migration.button_update_current_version()
        openupgrader_migration.button_prepare_for_migration()
        self.assertEqual(openupgrader_migration.state, "ready_for_migration")
        openupgrader_migration.button_do_migration()
        # wait until migration is stopped with threading
        while openupgrader_migration._get_odoo_migrated_state() == "running":
            time.sleep(10)
        self.assertEqual(openupgrader_migration.state, "done")
        self.assertEqual(
            openupgrader_migration.current_version_id,
            self.middle_version_id,
        )
        self.assertEqual(
            openupgrader_migration.next_version_id,
            self.to_version_id,
        )
        openupgrader_migration.button_prepare_for_migration()
        self.assertEqual(openupgrader_migration.state, "ready_for_migration")
        openupgrader_migration.button_do_migration()
        # wait until migration is stopped with threading
        while openupgrader_migration._get_odoo_migrated_state() == "running":
            time.sleep(10)
        self.assertEqual(openupgrader_migration.state, "done")
        self.assertEqual(
            openupgrader_migration.current_version_id,
            self.to_version_id,
        )
        self.assertEqual(
            openupgrader_migration.next_version_id,
            self.future_version_id,
        )
