from odoo import models, api, fields
import odoo.addons.decimal_precision as dp


class MrpProductionSyncPriceLine(models.TransientModel):
    _name = 'mrp.sync.price.line'
    _description = 'Wizard line to update move price related to production'

    wizard_id = fields.Many2one(
        comodel_name='mrp.sync.price', required=True,
        ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Product')
    move_id = fields.Many2one(
        comodel_name='stock.move', required=True)
    current_price = fields.Float(
        related='move_id.price_unit',
        string='Move Price',
        digits=dp.get_precision('Product Price'),
        readonly=True)
    current_product_price = fields.Float(
        related='product_id.standard_price',
        string='Product Price',
        digits=dp.get_precision('Product Price'),
        readonly=True)
    new_price = fields.Float(
        string='New Price',
        digits=dp.get_precision('Product Price'),
        required=True)
    date_new_price = fields.Datetime(
        string='New Price Date', readonly=True)
    origin_new_price = fields.Char(
        string='New Price Origin', readonly=True)
    move_price_variation = fields.Float(
        string='Variation (%)',  # compute='_compute_move_price_variation',
        digits=dp.get_precision('Discount'))

    @api.depends('current_price', 'new_price')
    @api.multi
    def _compute_move_price_variation(self):
        self.write({'move_price_variation': False})
        for line in self.filtered(
                lambda x: x.current_price and x.quantity_done):
            line.move_price_variation = 100 *\
                (line.new_price - line.current_price) / line.current_price

    def _prepare_price_unit(self):
        self.ensure_one()
        return {
            'price_unit': self.new_price,
        }
