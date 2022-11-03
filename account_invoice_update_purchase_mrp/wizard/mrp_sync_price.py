from odoo import models, api, fields


class MrpSyncPrice(models.TransientModel):
    _name = 'mrp.sync.price'
    _description = 'Wizard to update production moves price'

    line_ids = fields.One2many(
        comodel_name='mrp.sync.price.line',
        inverse_name='wizard_id', string='Lines')
    mrp_id = fields.Many2one(
        comodel_name='mrp.production', required=True, readonly=True,
        ondelete='cascade')
    state = fields.Selection(
        related='mrp_id.state', readonly=True)

    @api.multi
    def update_price_unit(self):
        self.ensure_one()
        for line in self.line_ids:
            move = line.move_id
            move.write(line._prepare_price_unit())
        # Mark the mo as checked
        self.mrp_id.write({'price_sync_ok': True})

    @api.multi
    def set_price_unit_ok(self):
        self.mrp_id.write({'price_sync_ok': True})
