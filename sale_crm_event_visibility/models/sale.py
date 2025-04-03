
from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    event_id = fields.Many2one(
        related="opportunity_id.event_id",
        store=True,
        index=True,
    )
