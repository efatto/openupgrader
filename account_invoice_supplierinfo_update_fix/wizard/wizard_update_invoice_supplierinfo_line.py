from odoo import api, models


class WizardUpdateInvoiceSupplierinfoLine(models.TransientModel):
    _inherit = 'wizard.update.invoice.supplierinfo.line'

    @api.multi
    def _prepare_supplierinfo_update(self):
        res = super()._prepare_supplierinfo_update()
        sequence = max(self.product_id.seller_ids.mapped('sequence')) + 1
        # This work only when user manually set sequences. Anyway, if supplierinfo
        # exists, it is better to do not increment sequence.
        existing_supplierinfo = self.product_id.seller_ids.filtered(
            lambda x: x.name == self.supplierinfo_id.name
        )
        if existing_supplierinfo:
            # use the same sequence value as esisting supplierinfo
            sequence = existing_supplierinfo.sequence
        res['sequence'] = sequence
        return res
