# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl)
from odoo.addons.mrp_production_demo.tests.common_data import TestProductionData
from odoo.tools import mute_logger, float_round
from odoo.tests import Form


class TestAccountInvoiceUpdatePurchaseMrp(TestProductionData):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor_1 = cls.env.ref('base.res_partner_4')
        supplierinfo_1 = cls.env['product.supplierinfo'].create({
            'name': cls.vendor_1.id,
            'price': 50.0,
            'currency_id': cls.env.ref('base.EUR').id,
        })
        cls.product_to_purchase_3 = cls.env['product.product'].create([{
            'name': 'Additional component product 3',
            'type': 'product',
            'default_code': 'ADDCOMP3',
            'purchase_ok': True,
            'route_ids': [
                (4, cls.env.ref('purchase_stock.route_warehouse0_buy').id),
                (4, cls.env.ref('stock.route_warehouse0_mto').id)],
            'seller_ids': [(6, 0, [supplierinfo_1.id])],
        }])
        cls.vendor = cls.env.ref('base.res_partner_3')
        supplierinfo = cls.env['product.supplierinfo'].create({
            'name': cls.vendor.id,
            'price': 100.0,
            'currency_id': cls.env.ref('base.EUR').id,
        })
        cls.product_to_purchase = cls.env['product.product'].create([{
            'name': 'Component product to purchase manually',
            'default_code': 'COMPPURCHMANU',
            'type': 'product',
            'purchase_ok': True,
            'route_ids': [
                (4, cls.env.ref('purchase_stock.route_warehouse0_buy').id),
                (4, cls.env.ref('stock.route_warehouse0_mto').id)],
            'seller_ids': [(6, 0, [supplierinfo.id])],
        }])
        cls.main_bom.write({
            'bom_line_ids': [
                (0, 0, {
                    'product_id': cls.product_to_purchase.id,
                    'product_qty': 7,
                    'product_uom_id': cls.product_to_purchase.uom_id.id,
                })
            ]
        })

    def test_01_mo_purchase_invoice_after_done(self):
        # check that price_unit for move_raw_ids is equal to purchase cost from invoice
        # vendor, if cost valuation is standard, and average if average
        product_qty = 5
        self.main_bom.routing_id = self.routing1
        man_order = self.env['mrp.production'].create({
            'name': 'MO-Test',
            'product_id': self.top_product.id,
            'product_uom_id': self.top_product.uom_id.id,
            'product_qty': product_qty,
            'bom_id': self.main_bom.id,
        })
        # check procurement has created RDP
        with mute_logger('odoo.addons.stock.models.procurement'):
            self.procurement_model.run_scheduler()
        po_ids = self.env['purchase.order'].search([
            ('origin', '=', man_order.name),
            ('state', '=', 'draft'),
        ])
        self.assertTrue(po_ids)
        self.assertEqual(len(po_ids), 1)
        po_lines = po_ids.order_line.filtered(
            lambda x: x.product_id == self.product_to_purchase)
        self.assertEqual(sum(po_line.product_qty for po_line in po_lines),
                         7 * product_qty)
        self.assertEqual(len(po_lines), 1)
        po_line = po_lines[0]
        self.assertAlmostEqual(po_line.price_unit, 100.0)
        # change po_line price and discount
        po_line.price_unit = 67.88
        po_line.discount = 15.0
        # confirm purchase order
        po_ids.button_confirm()
        # create workorder to add relative costs
        man_order.action_assign()
        man_order.button_plan()
        # produce partially
        produce_form = Form(
            self.env['mrp.product.produce'].with_context(
                active_id=man_order.id,
                active_ids=[man_order.id],
            )
        )
        produced_qty = 2.0
        produce_form.product_qty = produced_qty
        wizard = produce_form.save()
        wizard.do_produce()

        # check price_unit of stock_move of components is 0
        mo_raw_moves = man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase)
        self.assertEqual(len(mo_raw_moves), 1)
        mo_move = mo_raw_moves[0]
        self.assertAlmostEqual(mo_move.price_unit, 0.0)

        # aggiungere delle righe extra-bom, in stato confermato come da ui
        man_order.action_toggle_is_locked()
        man_order.write({
            'move_raw_ids': [
                (0, 0, {
                    'name': self.product_to_purchase_3.name,
                    'product_id': self.product_to_purchase_3.id,
                    'product_uom': self.product_to_purchase_3.uom_id.id,
                    'product_uom_qty': 10,
                    'location_id': man_order.location_src_id.id,
                    'location_dest_id': man_order.location_dest_id.id,
                    'state': 'confirmed',
                    'raw_material_production_id': man_order.id,
                    'picking_type_id': man_order.picking_type_id.id,
                }),
            ]
        })
        man_order.action_toggle_is_locked()
        move_raw = man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase_3
        )
        # complete production
        move_raw.write({'quantity_done': 3})
        self.assertEqual(move_raw.quantity_done, 3)
        produce_form.product_qty = 3.0
        produced_qty += produce_form.product_qty
        wizard_1 = produce_form.save()
        wizard_1.do_produce()
        man_order.button_mark_done()
        self.assertEqual(man_order.state, 'done')

        # re-check stock move price is always 0
        mo_raw_moves = man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase)
        self.assertEqual(len(mo_raw_moves), 1)
        mo_move = mo_raw_moves[0]
        self.assertAlmostEqual(mo_move.price_unit, 0.0)

        # complete purchase # todo se si acquista parzialmente a diversi prezzi???
        picking = po_ids[0].picking_ids[0]
        picking.action_confirm()
        picking.move_lines.write({'quantity_done': 35.0})
        picking.button_validate()
        # launch wizard to update stock move price
        wizard_data = man_order.check_raw_moves_price_unit()
        update_price_form = Form(
            self.env['mrp.sync.price'].with_context(
                wizard_data['context']
            )
        )
        update_price_wizard = update_price_form.save()
        update_price_wizard.update_price_unit()
        # check price_unit of mo raw move is equal to po line
        mo_raw_moves = man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase)
        self.assertEqual(len(mo_raw_moves), 1)
        mo_move = mo_raw_moves[0]
        self.assertAlmostEqual(
            mo_move.price_unit,
            float_round(
                po_line.price_unit * (1 - po_line.discount / 100.0),
                self.env['decimal.precision'].precision_get('Product Price')
            )
        )
