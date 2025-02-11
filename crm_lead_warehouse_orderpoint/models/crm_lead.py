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

    @api.depends("product_id")
    def _compute_is_product_lead(self):
        for lead in self:
            lead.is_product_lead = bool(lead.product_id)

    def _increase_ordepoint(self):
        probability_lists = safe_eval(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("crm.product.lead.probability.list", [])
        )
        for lead in self.filtered(
            lambda le: le.product_id
            and le.product_yearly_estimated_qty
            and not le.product_id.categ_id.is_special
            and le.probability
        ):
            reorder_increase = 0
            for probability_list in probability_lists:
                if lead.probability >= float(probability_list[0]):
                    reorder_increase = float(probability_list[1])
            # add to product_id reorder rules an extra qty on probability list value
            # store increased qty to remove if lead is lost or closed
            # do for all the orderpoints as only one is used to compute procurement
            orderpoint_ids = lead.product_id.orderpoint_ids
            for orderpoint_id in orderpoint_ids:
                lead_product_min_qty = lead.product_yearly_estimated_qty * (
                    reorder_increase / 100.0
                )
                delta_lead_product_min_qty = (
                    lead_product_min_qty - orderpoint_id.lead_product_min_qty
                )
                lead_product_max_qty = lead.product_yearly_estimated_qty * (
                    reorder_increase / 100.0
                )
                delta_lead_product_max_qty = (
                    lead_product_max_qty - orderpoint_id.lead_product_max_qty
                )
                orderpoint_id.write(
                    {
                        "product_min_qty": orderpoint_id.product_min_qty
                        + delta_lead_product_min_qty,
                        "lead_product_min_qty": lead_product_min_qty,
                        "product_max_qty": orderpoint_id.product_max_qty
                        + delta_lead_product_max_qty,
                        "lead_product_max_qty": lead_product_max_qty,
                    }
                )

    def write(self, vals):
        res = super().write(vals)
        self._increase_ordepoint()
        return res

    def action_set_lost(self, **additional_values):
        additional_values.update(probability=0)
        res = super().action_set_lost(**additional_values)
        return res
