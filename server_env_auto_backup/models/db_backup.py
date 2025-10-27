from odoo import models

from odoo.addons.server_env.models.server import running


class DbBackup(models.Model):
    _inherit = "db.backup"

    @running
    def action_backup(self):
        return super().action_backup()
