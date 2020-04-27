from odoo import fields, models, api, _, exceptions


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @api.multi
    def action_assign(self):
        for move in self.mapped('move_ids_without_package'):
            if move.product_uom_qty == move.quantity_done and \
                    move.state == 'confirmed':
                move.write({'state': 'assigned'})
        return super().action_assign()
