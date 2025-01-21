# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models


class StockDeliveryNoteCreateWizard(models.TransientModel):
    _inherit = "stock.delivery.note.create.wizard"

    name = fields.Char()

    def confirm(self):
        self.check_compliance(self.selected_picking_ids)

        sale_order_ids = self.mapped("selected_picking_ids.sale_id")
        sale_order_id = (
            sale_order_ids and sale_order_ids[0] or self.env["sale.order"].browse()
        )
        values = self._prepare_delivery_note_vals(sale_order_id)
        values.update(
            {
                "partner_id": self.partner_id.id,
                "delivery_method_id": self.partner_id.property_delivery_carrier_id.id,
            }
        )
        if self.name:
            values.update({"name": self.name})
        delivery_note = self.env["stock.delivery.note"].create(values)

        self.selected_picking_ids.write({"delivery_note_id": delivery_note.id})

        if self.user_has_groups("l10n_it_delivery_note.use_advanced_delivery_notes"):
            return delivery_note.goto()

    def _prepare_delivery_note_vals(self, sale_order_id):
        res = super()._prepare_delivery_note_vals(sale_order_id=sale_order_id)
        carrier_tracking_ref = ", ".join(
            {
                pick.carrier_tracking_ref
                for pick in self.selected_picking_ids.filtered(
                    lambda x: x.carrier_tracking_ref
                )
            }
        )
        if carrier_tracking_ref:
            res["carrier_tracking_ref"] = carrier_tracking_ref
        return res
