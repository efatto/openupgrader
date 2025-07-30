from odoo import fields, models


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    price_unit = fields.Float(
        related="move_id.price_unit",
        store=True,
    )
