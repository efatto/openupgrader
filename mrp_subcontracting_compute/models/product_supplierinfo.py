from odoo import api, models


class SupplierInfo(models.Model):
    _inherit = "product.supplierinfo"

    @api.depends(
        "name",
        "product_id",
        "product_tmpl_id",
        "product_id.variant_bom_ids.type",
        "product_id.variant_bom_ids.subcontractor_ids",
        "product_tmpl_id.bom_ids.type",
        "product_tmpl_id.bom_ids.subcontractor_ids",
    )
    def _compute_is_subcontractor(self):
        for supplier in self:
            boms = supplier.product_id.variant_bom_ids
            boms |= supplier.product_tmpl_id.bom_ids
            supplier.is_subcontractor = supplier.name in boms.subcontractor_ids
