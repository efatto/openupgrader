from odoo import api, fields, models
from odoo.tools.safe_eval import safe_eval


class CrmLead(models.Model):
    _inherit = "crm.lead"

    is_product_lead = fields.Boolean(
        string="Is product lead",
        compute="_compute_is_product_lead",
        store=True,
        index=True,
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
    )
    product_yearly_estimated_qty = fields.Float(
        string="Product Yearly Estimated Qty",
    )
    lead_product_qty = fields.Float(
        string="Qty from CRM lead",
        digits="Product Unit of Measure",
        compute="_compute_lead_product_qty",
        store=True,
    )

    @api.depends("product_id")
    def _compute_is_product_lead(self):
        for lead in self:
            lead.is_product_lead = bool(lead.product_id)

    @api.depends("product_id", "product_yearly_estimated_qty")
    def _compute_lead_product_qty(self):
        probability_lists = safe_eval(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("crm.product.lead.probability.list", "[(50, 20), (100, 100)]")
        )
        lead_stock_ids = self.filtered(
            lambda le: le.product_id
            and le.product_yearly_estimated_qty
            and not le.product_id.categ_id.is_special
            and le.probability
        )
        leads = self - lead_stock_ids
        for lead in leads:
            lead.lead_product_qty = 0
        for lead in lead_stock_ids:
            reorder_increase = 0
            for probability_list in probability_lists:
                if lead.probability >= float(probability_list[0]):
                    reorder_increase = float(probability_list[1])
            lead.lead_product_qty = lead.product_yearly_estimated_qty * (
                reorder_increase / 100.0
            )

    def action_set_lost(self, **additional_values):
        additional_values.update(probability=0)
        res = super().action_set_lost(**additional_values)
        return res
