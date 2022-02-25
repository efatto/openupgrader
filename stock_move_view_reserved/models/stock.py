# Copyright 2021 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    date_expected = fields.Datetime(
        string='Expected Date',
        store=True,
        related='move_id.date_expected'
    )
    production_id = fields.Many2one(
        string='Production',
        related='move_id.production_id',
        store=True,
    )
