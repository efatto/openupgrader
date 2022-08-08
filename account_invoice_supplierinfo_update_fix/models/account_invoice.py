from odoo import _, api, models
from odoo.exceptions import UserError


class AccountInvoiceLine(models.Model):
    _inherit = 'account.invoice.line'

    @api.multi
    def _get_supplierinfo(self):
        super()._get_supplierinfo()
        if not self.invoice_id.date_invoice:
            raise UserError(_('Missing date invoice! It is required to check '
                              'supplierinfo validity.'))
        supplierinfos = self.product_id.seller_ids.filtered(
            lambda seller: seller.name == self.invoice_id.supplier_partner_id)
        valid_supplierinfos = self.env['product.supplierinfo']
        for supplierinfo in supplierinfos:
            # excludes supplierinfo expired
            if supplierinfo.date_end and \
                    supplierinfo.date_end < self.invoice_id.date_invoice:
                continue
            # excludes supplierinfo not yet valid
            if supplierinfo.date_start and \
                    supplierinfo.date_start > self.invoice_id.date_invoice:
                continue
            valid_supplierinfos |= supplierinfo
        return valid_supplierinfos and valid_supplierinfos[0] or False
