# Copyright 2021 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import api, fields, models


class UpdateStockMovePriceWizard(models.TransientModel):
    _name = 'update.stock.move.price'

    new_price = fields.Float(required=True, default=0)

    @api.multi
    def update_stock_move_price(self):
        move_ids = self.env.context.get('active_ids', False)
        moves = self.env['stock.move'].browse(move_ids)
        for move in moves:
            # ensure only outgoing move price is negative, leave other decisions to user
            sign = -1 if move.picking_type_id.code == 'outgoing' else 1
            move.write({
                'price_unit': sign * abs(self.new_price) if sign == -1
                else self.new_price
            })
