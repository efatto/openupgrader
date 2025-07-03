from odoo import models


class OpenupgraderMigration(models.Model):
    _inherit = "openupgrader.migration"

    def button_prepare_for_migration(self):
        res = super().button_prepare_for_migration()
        if self.from_version_id.name == '12.0':
             self.migrate_l10n_it_ddt_to_l10n_it_delivery_note()
        return res

    def migrate_l10n_it_ddt_to_l10n_it_delivery_note(self):
        # this method is executed only if the module l10n_it_ddt is installed
        self.start_odoo(self.from_version_id)
        odoo_client = self.odoo_connect()
        module_obj = odoo_client.env["ir.module.module"]
        if module_obj.search([
            ('name', '=', 'l10n_it_ddt'),
            ('state', '=', 'installed'),
        ]):
            self.install_uninstall_module(
                'l10n_it_delivery_note', install=True
            )
            self.button_stop_odoo()
            self.start_odoo(
              version_id=self.from_version_id,
              extra_command=f'migrate_l10n_it_ddt -d {self.env.cr.dbname}_migrate')
        self.button_stop_odoo()
