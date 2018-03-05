# -*- coding: utf-8 -*-
##############################################################################
# For copyright and license notices, see __openerp__.py file in root directory
##############################################################################
from openerp import fields, models, exceptions, api, _


class StockInventoryLine(models.Model):
    _inherit = "stock.inventory.line"

    valuation_price_unit = fields.Float(
        'Recalculated unit price')
    valuation_price_subtotal = fields.Float(
        'Recalculated subtotal price')


class StockInventory(models.Model):
    _inherit = "stock.inventory"

    valuation_type = fields.Selection(
        [('fifo', 'FIFO'),
         ('lifo', 'LIFO'),
         ('average', 'AVERAGE'),
        ], 'Price valuation')

    @api.multi
    def product_recalculate_value(self):
        for inv in self:
            for line in inv.line_ids:
                res = self.price_calculation(line)
                price_amount = amount = 0
                for match in res:
                    price_amount += match[1] * match[2]
                    amount += match[1]
                if amount != 0.0:
                    line.write({
                        'valuation_price_unit': price_amount / amount,
                        'valuation_price_subtotal': price_amount,
                    })

    @api.multi
    def price_calculation(self, line):
        order = 'date desc, id desc'
        move_obj = self.env['stock.move']
        if self.valuation_type == 'fifo':
            # search for incoming moves
            move_in_ids = move_obj.search([
                ('company_id', '=', self.env.user.company_id.id),
                ('date', '<=', self.date_inventory),
                ('state', '=', 'done'),
                ('location_id.usage', '!=', 'internal'),
                ('location_dest_id.usage', '=', 'internal'),
                ('product_id', '=', line.product_id.id),
                ('restrict_lot_id', '=', line.prod_lot_id.id if
                line.prod_lot_id else False)
            ], order=order)
        else:
            # search for incoming and outgoing moves
            move_in_ids = move_obj.search([
                ('company_id', '=', self.env.user.company_id.id),
                ('date', '<=', self.date_inventory),
                ('state', '=', 'done'),
                '|',
                ('location_id.usage', '=', 'internal'),
                ('location_dest_id.usage', '=', 'internal'),
                ('product_id', '=', self.product_id.id),
                ('restrict_lot_id', '=', line.prod_lot_id.id if
                line.prod_lot_id else False)
            ], order=order)
        tuples = []
        qty_to_go = line.product_qty
        older_qty = line.product_qty
        for move in move_in_ids:
            # Convert to UoM of product each time
            uom_from = move.product_uom.id
            qty_from = move.product_qty
            product_qty = self.env['product.uom']._compute_qty(
                uom_from, qty_from, move.product_id.uom_id.id)
            # Get price from purchase line
            price_unit = 0
            if move.purchase_line_id:
                # get price from purchase line
                price_unit = move.purchase_line_id.price_unit
            else:
                # Get price from product, move is an inventory or not linked
                # to a purchase (income move created and even invoiced, but
                # price is not usable here)
                price_unit = move.product_id.standard_price

            if self.valuation_type == 'fifo':
                if qty_to_go - product_qty >= 0:
                    tuples.append((move.product_id.id, product_qty,
                                   price_unit, qty_from,), )
                    qty_to_go -= product_qty
                else:
                    tuples.append((
                                  move.product_id.id, qty_to_go, price_unit,
                                  qty_from * qty_to_go / product_qty,), )
                    break
            elif self.valuation_type == 'lifo':
                if move.location_id.usage == 'internal' and \
                        move.location_dest_id.usage != 'internal':  # sale
                    older_qty += product_qty
                if move.location_id.usage != 'internal' and \
                        move.location_dest_id.usage == 'internal':  # purchase
                    older_qty -= product_qty
                    if qty_to_go > older_qty > 0:
                        tuples.append((move.product_id.id,
                                       (qty_to_go - older_qty), price_unit,
                                       qty_from), )
                        qty_to_go = older_qty
                    elif qty_to_go > older_qty <= 0:
                        tuples.append((move.product_id.id, qty_to_go,
                                       price_unit,
                                       qty_from * qty_to_go / product_qty), )
                        break
        return tuples
