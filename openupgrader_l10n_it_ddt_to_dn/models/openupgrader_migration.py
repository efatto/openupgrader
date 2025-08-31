import logging

from odoo import _, models
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)


class OpenupgraderMigration(models.Model):
    _inherit = "openupgrader.migration"

    def button_migrate_l10n_it_ddt_to_l10n_it_delivery_note(self):
        # this method is executed only if the module l10n_it_ddt is installed
        if self.from_version_id.name != "12.0":
            raise UserError(_("This method is only available for Odoo 12.0.x"))
        dn_modules = ["l10n_it_delivery_note_base", "l10n_it_delivery_note"]
        # ensure required odoo modules are installed via pip
        self.install_pip_modules(
            self.from_version_id,
            self.from_version_id.odoo_pip_requirement_ids.mapped('name'))
        self.start_odoo(self.from_version_id)
        odoo_client = self.odoo_connect()
        module_obj = odoo_client.env["ir.module.module"]
        module_obj.update_list()
        if module_obj.search(
            [
                ("name", "=", "l10n_it_ddt"),
                ("state", "=", "installed"),
            ]
        ):
            logger.info("Migrating from l10n_it_ddt to l10n_it_delivery_note")
            for module in dn_modules:
                if module_obj.search(
                    [
                        ("name", "=", module),
                        ("state", "!=", "installed"),
                    ]
                ):
                    self.install_uninstall_module(module, install=True)
                    module_name_ids = self.env["module.name"].search(
                        [("name", "=", module)])
                    if not module_name_ids:
                        module_name_ids = self.env["module.name"].create(
                            {"name": module})
                    self.from_version_id.write(
                        {"module_installed_ids": [(4, module_name_ids[0].id)]}
                    )
                    self.to_version_id.write(
                        {"module_installed_ids": [(4, module_name_ids[0].id)]}
                    )
            self.start_odoo(
                version_id=self.from_version_id,
                extra_command=f"migrate_l10n_it_ddt ",
            )
            # todo set group_use_advanced_delivery_notes?
            # Uninstallation of the module l10n_it_ddt and children will be done in
            # after migration logic on openupgrader migration.
            # It could be done here too, if we would.
        else:
            logger.info("l10n_it_ddt is not installed")
        # todo check in self.odoo_update_log_file
        #  there is "Execution completed successfully!" message
