# -*- coding: utf-8 -*-
##############################################################################
# For copyright and license notices, see __openerp__.py file in root directory
##############################################################################
from openerp import models, fields


class StockPickingPackagePreparation(models.Model):
    _inherit = "stock.picking.package.preparation"

    ddt_number = fields.Char(states={'done': [('readonly', False)]})
