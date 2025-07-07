from odoo import models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    def _get_supplierinfo(self):
        super()._get_supplierinfo()
        supplierinfos = self.product_id.seller_ids.filtered(
            lambda seller: seller.name == self.move_id.supplier_partner_id
        )
        valid_supplierinfos = self.env["product.supplierinfo"]
        for supplierinfo in supplierinfos:
            # excludes supplierinfo expired
            if (
                supplierinfo.date_end
                and supplierinfo.date_end < self.move_id.invoice_date
            ):
                continue
            # excludes supplierinfo not yet valid
            if (
                supplierinfo.date_start
                and supplierinfo.date_start > self.move_id.invoice_date
            ):
                continue
            valid_supplierinfos |= supplierinfo
        return valid_supplierinfos and valid_supplierinfos[0] or False
