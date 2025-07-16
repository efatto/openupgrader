from odoo import models, _
import logging

from odoo.exceptions import UserError

logger = logging.getLogger(__name__)

class OpenupgraderMigration(models.Model):
    _inherit = "openupgrader.migration"

    def button_migrate_l10n_it_ddt_to_l10n_it_delivery_note(self):
        # this method is executed only if the module l10n_it_ddt is installed
        if self.from_version_id.name != "12.0":
            raise UserError(_("This method is only available for Odoo 12.0.x"))
        self.start_odoo(self.from_version_id)
        odoo_client = self.odoo_connect()
        module_obj = odoo_client.env["ir.module.module"]
        if module_obj.search(
            [
                ("name", "=", "l10n_it_ddt"),
                ("state", "=", "installed"),
            ]
        ):
            logger.info("Migrating from l10n_it_ddt to l10n_it_delivery_note")
            if module_obj.search(
                [
                    ("name", "=", "l10n_it_delivery_note"),
                    ("state", "!=", "installed"),
                ]
            ):
                self.install_uninstall_module("l10n_it_delivery_note", install=True)
            self.start_odoo(
                version_id=self.from_version_id,
                extra_command=f"migrate_l10n_it_ddt ",
            )
            # todo set group_use_advanced_delivery_notes?
            # Uninstallation of the module l10n_it_ddt and children will be done in
            # after migration logic on openupgrader migration.
            # It could be done here too, if we would.
        # todo check in self.odoo_update_log_file
        #  there is "Execution completed successfully!" message
