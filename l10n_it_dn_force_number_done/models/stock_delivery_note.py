from odoo import models


class StockDeliveryNote(models.Model):
    _inherit = "stock.delivery.note"

    def _compute_boolean_flags(self):
        super()._compute_boolean_flags()
        can_change_number = self.user_has_groups(
            "l10n_it_delivery_note.can_change_number"
        )
        for note in self:
            note.can_change_number = can_change_number
