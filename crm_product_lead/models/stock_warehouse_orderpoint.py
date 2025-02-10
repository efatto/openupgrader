from odoo import fields, models


class StockWarehouseOrderpoint(models.Model):
    _inherit = "stock.warehouse.orderpoint"

    lead_product_min_qty = fields.Float(
        string="Min qty from CRM lead",
        digits="Product Unit of Measure",
        required=True,
        default=0.0,
    )
    lead_product_max_qty = fields.Float(
        string="Max qty from CRM lead",
        digits="Product Unit of Measure",
        required=True,
        default=0.0,
    )
