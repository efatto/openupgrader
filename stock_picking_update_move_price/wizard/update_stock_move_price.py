from odoo import fields, models


class UpdateStockMovePriceWizard(models.TransientModel):
    _name = "update.stock.move.price"
    _description = "Update stock move price"

    new_price = fields.Float(required=True, default=0)

    def update_stock_move_price(self):
        if self.env.context.get("active_model") == "stock.move.line":
            move_line_ids = self.env.context.get("active_ids", False)
            moves = self.env["stock.move.line"].browse(move_line_ids).mapped("move_id")
        else:
            move_ids = self.env.context.get("active_ids", False)
            moves = self.env["stock.move"].browse(move_ids)
        for move in moves:
            # ensure only outgoing move price is negative, leave other decisions to user
            sign = -1 if move.picking_type_id.code == "outgoing" else 1
            move.write(
                {
                    "price_unit": sign * abs(self.new_price)
                    if sign == -1
                    else self.new_price
                }
            )
