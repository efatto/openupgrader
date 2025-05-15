from odoo import api, fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    @api.model
    def _get_move_sign(self, rec):
        # 1 in all cases exept the ones specified below
        sign = 1
        # -1 if from internal to others or scrap or return
        if (
            rec.location_id.usage == "internal"
            and (
                rec.location_dest_id.usage in [
                    "customer", "inventory", "production", "supplier"]
                or rec.location_dest_id.scrap_location
                or rec.location_dest_id.return_location
            )
        ):
            sign = -1
        # 0 if not from internal to others or scrap or return
        elif (
            rec.location_id.usage != "internal"
            and (
                rec.location_dest_id.usage in [
                    "customer", "inventory", "production", "supplier"]
                or rec.location_dest_id.scrap_location
                or rec.location_dest_id.return_location
            )
        ):
            sign = 0
        # 0 if the origin location is the same as the dest location
        elif rec.location_dest_id.usage == rec.location_id.usage:
            sign = 0
        return sign

    @api.depends("product_uom_qty", "location_dest_id", "location_id")
    def _compute_sign_product_qty(self):
        for rec in self:
            sign = self.env["stock.move"]._get_move_sign(rec)
            rec.qty_signed = rec.product_uom_qty * sign

    qty_signed = fields.Float(
        compute=_compute_sign_product_qty, store=True, group_operator="sum"
    )


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    @api.depends("qty_done", "location_dest_id", "location_id")
    def _compute_sign_product_qty(self):
        for rec in self:
            sign = self.env["stock.move"]._get_move_sign(rec)
            rec.qty_signed = rec.qty_done * sign

    qty_signed = fields.Float(
        compute=_compute_sign_product_qty, store=True, group_operator="sum"
    )
