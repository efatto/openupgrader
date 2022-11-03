from odoo import api, models


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _get_price_with_discount(self, line, price):
        if hasattr(line, 'discount'):
            price = price * (1 - line.discount / 100.0)
        if hasattr(line, 'discount2'):
            price = price * (1 - line.discount2 / 100.0)
        if hasattr(line, 'discount3'):
            price = price * (1 - line.discount3 / 100.0)
        return price

    @api.multi
    def _get_current_price_unit(self):
        self.ensure_one()
        PurchaseOrderLine = self.env['purchase.order.line']
        price_unit = - self.price_unit
        date_price_unit = False
        origin_price_unit = False
        # todo cercare se prima del trasferimento effettivo
        # todo 0. c'è un trasferimento originato da un po
        # todo 1. c'è una fattura d'acquisto collegata ad un ordine d'acquisto
        # todo 2. c'è solo una fattura d'acquisto
        # todo 3. c'è solo un ordine d'acquisto
        lines = self.search([
            ('product_id', '=', self.product_id.id),
            ('state', '=', 'done'),
            ('date', '<=', self.date),
            ('purchase_line_id', '!=', False),
        ]).sorted(
            key=lambda l: l.date, reverse=True)
        if lines:
            last_line = lines[:1]
            invoice_lines = self.env['account.invoice.line'].search([
                ('purchase_line_id', '=', last_line.purchase_line_id.id)
            ])
            if invoice_lines:
                # get price from invoice if exists
                invoice_line = invoice_lines[0]
                price_unit = self._get_price_with_discount(
                    invoice_line, invoice_line.price_unit)
                date_price_unit = invoice_line.invoice_id.date_invoice
                origin_price_unit = invoice_line.invoice_id.number
            else:
                purchase_line = last_line.purchase_line_id
                price_unit = self._get_price_with_discount(
                    purchase_line, purchase_line.price_unit)
                date_price_unit = last_line.date
                origin_price_unit = last_line.purchase_line_id.order_id.name
            return price_unit, date_price_unit, origin_price_unit

        lines = PurchaseOrderLine.search([
            ('product_id', '=', self.product_id.id),
            ('state', 'in', ['purchase', 'done']),
            ('date_order', '<=', self.date),
        ]).sorted(
            key=lambda l: l.order_id.date_order, reverse=True)
        if lines:
            # Get most recent Purchase Order Line
            last_line = lines[:1]
            price_unit = self._get_price_with_discount(
                last_line, last_line.price_unit)
            date_price_unit = last_line.order_id.date_order
            origin_price_unit = last_line.order_id.name

        return price_unit, date_price_unit, origin_price_unit

    @api.multi
    def _is_correct_price(self):
        self.ensure_one()
        price_unit, date_price_unit, origin_price_unit = self._get_current_price_unit()
        return price_unit == - self.price_unit

    @api.multi
    def _prepare_wizard_line(self):
        # TODO per ogni riga di move_raw_ids, in base al tipo di valutazione del costo,
        #  impostare il price_unit all'ultimo costo di acquisto reale
        #  (note: se c'era della disponibilità precedente all'ultimo acquisto ignorarla,
        #  a meno che il prezzo fosse superiore?)
        self.ensure_one()
        move_price_variation = False
        price_unit, date_price_unit, origin_price_unit = self._get_current_price_unit()
        current_price = - self.price_unit
        if current_price:
            move_price_variation = 100 *\
                    (price_unit - current_price) / current_price
        # todo è utile sapere la quantità disponibile alla data dello scarico?
        #  e la quantità precedente? successiva?
        return {
            'product_id': self.product_id.id,
            'move_id': self.id,
            'current_price': current_price,
            'current_product_price': self.product_id.standard_price,
            'new_price': price_unit,
            'date_new_price': date_price_unit,
            'origin_new_price': origin_price_unit,
            'move_price_variation': move_price_variation,
        }
