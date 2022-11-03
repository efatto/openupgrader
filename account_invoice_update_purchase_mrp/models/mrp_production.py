from odoo import api, fields, models, _


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    price_sync_ok = fields.Boolean(string='Prices Ok')

    @api.multi
    def _get_update_lines(self):
        self.ensure_one()
        lines = []
        for line in self.move_raw_ids.filtered(
                lambda x: x.raw_material_production_id and x.quantity_done):
            if line._is_correct_price():
                continue
            lines.append((0, 0, line._prepare_wizard_line()))
        return lines

    @api.multi
    def check_raw_moves_price_unit(self):
        self.ensure_one()
        lines_for_update = self._get_update_lines()
        if lines_for_update:
            ctx = {
                'default_line_ids': lines_for_update,
                'default_mrp_id': self.id,
            }

            view_form = self.env.ref(
                'account_invoice_update_purchase_mrp.mrp_production_sync_cost_form')
            return {
                'name': _("Update price unit of raw moves"),
                'type': 'ir.actions.act_window',
                'view_mode': 'form',
                'res_model': 'mrp.sync.price',
                'views': [(view_form.id, 'form')],
                'view_id': view_form.id,
                'target': 'new',
                'context': ctx,
            }
        else:
            self.write({'price_sync_ok': True})
