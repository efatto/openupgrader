from odoo import api, fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    @api.depends("product_uom_qty", "location_dest_id", "location_id")
    def _compute_sign_product_qty(self):
        for move in self:
            # 1 in all cases exept the ones specified below
            sign = 1
            # -1 if from inventory to a return location
            if (
                move.location_id.usage == "inventory"
                and move.location_dest_id.return_location
            ):
                sign = -1
            # -1 if from internal to customer|inventory|production|supplier
            if (
                move.location_id.usage == "internal"
                and move.location_dest_id.usage
                in ["customer", "inventory", "production", "supplier"]
            ):
                sign = -1
            # 0 if not from internal to customer|inventory|production|supplier
            elif (
                move.location_id.usage != "internal"
                and move.location_dest_id.usage
                in ["customer", "inventory", "production", "supplier"]
            ):
                sign = 0
            # 0 if the origin location is the same as the dest location
            elif move.location_dest_id.usage == move.location_id.usage:
                sign = 0
            move.qty_signed = move.product_uom_qty * sign

    qty_signed = fields.Float(
        compute=_compute_sign_product_qty, store=True, group_operator="sum"
    )


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    @api.depends("qty_done", "location_dest_id", "location_id")
    def _compute_sign_product_qty(self):
        for line in self:
            # 1 in all cases exept the ones specified below
            sign = 1
            # -1 if from inventory to a return location
            if (
                line.location_id.usage == "inventory"
                and line.location_dest_id.return_location
            ):
                sign = -1
            # -1 if from internal to customer|inventory|production|supplier
            if (
                line.location_id.usage == "internal"
                and line.location_dest_id.usage
                in ["customer", "inventory", "production", "supplier"]
            ):
                sign = -1
            # 0 if not from internal to customer|inventory|production|supplier
            elif (
                line.location_id.usage != "internal"
                and line.location_dest_id.usage
                in ["customer", "inventory", "production", "supplier"]
            ):
                sign = 0
            # 0 if the origin location is the same as the dest location
            elif line.location_dest_id.usage == line.location_id.usage:
                sign = 0
            line.qty_signed = line.qty_done * sign

    qty_signed = fields.Float(
        compute=_compute_sign_product_qty, store=True, group_operator="sum"
    )
