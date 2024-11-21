# Copyright 2023 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
from odoo import fields
from odoo.tests import Form

from odoo.addons.mrp_production_demo.tests.common_data import TestProductionData


class TestSaleOrderCalendarState(TestProductionData):
    def _create_sale_order_line(self, order, product, qty):
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": product.id,
                "product_uom_qty": qty,
                "price_unit": 100,
            }
        )
        line.product_id_change()
        line._convert_to_write(line._cache)
        return line

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env.ref("base.res_partner_2")
        # Acoustic Bloc Screens, 16 on hand
        cls.product1 = cls.env.ref("product.product_product_25")
        # Cabinet with Doors, 8 on hand
        cls.product2 = cls.env.ref("product.product_product_10")
        # Large Cabinet, 250 on hand
        cls.product3 = cls.env.ref("product.product_product_6")
        # Drawer Black, 0 on hand
        cls.product4 = cls.env.ref("product.product_product_16")
        cls.product1.invoice_policy = "order"
        cls.product2.invoice_policy = "order"

    def test_01_sale(self):
        order1 = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
            }
        )
        self._create_sale_order_line(order1, self.product1, 5)
        order1.action_confirm()
        self.assertEqual(order1.state, "sale")
        # do not test 'to_process' calendar state as procurement stock rule launch
        # automatically action_assign()
        # self.assertEqual(order1.calendar_state, 'to_process')
        picking = order1.picking_ids[0]
        picking.action_assign()
        self.assertEqual(order1.calendar_state, "to_evaluate")
        picking.mark_printed_for_logistic()
        self.assertEqual(order1.calendar_state, "to_pack")
        self.assertEqual(picking.state, "assigned")
        self.assertTrue(picking.is_assigned)

        blocked_form = Form(
            self.env["wizard.mark.blocked"].with_context(
                active_id=order1.id,
                active_ids=order1.ids,
                active_model=order1._name,
            )
        )
        blocked_form.note = "Blocked note text"
        wizard = blocked_form.save()
        wizard.mark_blocked()
        self.assertEqual(order1.calendar_state, "blocked")
        # TODO possible improvement: block picking validation if so is blocked?
        order1.button_mark_not_blocked()
        self.assertFalse(order1.is_blocked)
        self.assertEqual(order1.calendar_state, "to_pack")

        for sml in picking.move_lines.mapped("move_line_ids"):
            sml.qty_done = sml.product_qty
        for whs_list in picking.move_lines.mapped("whs_list_ids"):
            whs_list.write(
                {
                    "stato": "4",
                    "qtamov": whs_list.qta,
                }
            )
        picking._action_done()
        self.assertEqual(picking.state, "done")
        self.assertEqual(order1.calendar_state, "delivery_done")
        # create invoice
        order1._create_invoices()
        self.assertEqual(order1.calendar_state, "invoiced")
        # sale products partially not available
        self._create_sale_order_line(order1, self.product1, 50)

        # REMOVED on request
        # invoice = self.env['account.invoice'].browse(inv_id)
        # invoice.carrier_tracking_ref = 'TRACKING - 5555'
        # self.assertEqual(order1.calendar_state, 'shipped')

    def test_02_mrp_from_sale(self):
        order_form = Form(self.env["sale.order"])
        order_form.partner_id = self.env.ref("base.res_partner_12")
        order_form.date_order = fields.Date.today()
        order_form.picking_policy = "direct"
        with order_form.order_line.new() as line:
            line.product_id = self.top_product
            line.product_uom_qty = 20
            line.product_uom = self.top_product.uom_id
            line.price_unit = self.top_product.list_price
            line.name = self.top_product.name
        order = order_form.save()
        order.action_confirm()
        self.assertEqual(order.state, "sale")
        self.assertEqual(order.calendar_state, "to_produce")
        man_order = self.env["mrp.production"].search([("origin", "ilike", order.name)])
        self.assertTrue(man_order)
        man_order.action_assign()
        self.assertEqual(man_order.state, "confirmed")

        blocked_form = Form(
            self.env["wizard.mark.blocked"].with_context(
                active_id=man_order.id,
                active_ids=man_order.ids,
                active_model=man_order._name,
            )
        )
        blocked_form.note = "Blocked note text"
        wizard = blocked_form.save()
        wizard.mark_blocked()
        self.assertEqual(order.calendar_state, "blocked")
        self.assertTrue(man_order.is_blocked)
        man_order.button_mark_not_blocked()
        self.assertFalse(order.is_blocked)
        self.assertEqual(order.calendar_state, "to_produce")
        man_order.button_mark_to_submanufacture()
        self.assertEqual(order.calendar_state, "to_submanufacture")
        man_order.button_mark_to_assembly()
        self.assertEqual(order.calendar_state, "to_assembly")
        man_order.button_mark_to_test()
        self.assertEqual(order.calendar_state, "to_test")
        man_order.button_unmark_additional_state()

        mo_form = Form(man_order)
        mo_form.qty_producing = 2
        man_order = mo_form.save()
        self.assertEqual(order.calendar_state, "production_started")
        action = man_order.button_mark_done()
        self.assertEqual(order.calendar_state, "production_started")
        backorder = Form(
            self.env["mrp.production.backorder"].with_context(**action["context"])
        )
        backorder.save().action_backorder()
        self.assertEqual(len(man_order.procurement_group_id.mrp_production_ids), 2)
        self.assertEqual(order.calendar_state, "to_produce")
        self.assertEqual(man_order.state, "done")

        mo_backorder = man_order.procurement_group_id.mrp_production_ids[-1]
        mo_backorder_form = Form(mo_backorder)
        mo_backorder_form.qty_producing = 18
        mo_backorder = mo_backorder_form.save()
        mo_backorder.button_mark_done()

        self.assertEqual(order.calendar_state, "production_done")
