from odoo import fields
from odoo.tests import Form
from odoo.tools.date_utils import relativedelta

from odoo.addons.mrp_production_demo.tests.common_data import TestProductionData


class TestMrpProductionDeviation(TestProductionData):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product_2 = cls.env["product.product"].create(
            [
                {
                    "name": "Additional component product",
                    "type": "product",
                    "default_code": "ADDCOMP",
                    "standard_price": 7.0,
                    "route_ids": [
                        (4, cls.env.ref("purchase_stock.route_warehouse0_buy").id)
                    ],
                    "seller_ids": [
                        (
                            0,
                            0,
                            {
                                "name": cls.env.ref("base.res_partner_3").id,
                                "price": 5.0,
                                "min_qty": 0.0,
                                "sequence": 1,
                                "date_start": fields.Date.today()
                                - relativedelta(days=100),
                                "delay": 28,
                            },
                        ),
                    ],
                }
            ]
        )
        cls.main_bom.write(
            {
                "operation_ids": cls.operation1.ids,
            }
        )

    def get_deviation_data(self, production):
        res = self.env["mrp.production.deviation.report"].read_group(
            [("production_id", "=", production.id)],
            [
                "product_id",
                "unit_cost",
                "cost",
                "cost_expected",
                "cost_expected_rw",
                "duration_expected",
                "duration_expected_rw",
                "workorder_id",
                "quantity_expected",
                "product_qty",
                "cost_current",
            ],
            ["product_id"],
        )
        return res

    def test_01_mo_deviation_data(self):
        production_qty = 5
        # put only product_id and product_qty in the wizard data to avoid the default
        # setting of product_qty to 1
        man_order_form = Form(self.env["mrp.production"])
        man_order_form.product_id = self.top_product
        man_order_form.product_qty = production_qty
        man_order = man_order_form.save()
        self.assertEqual(man_order.product_qty, production_qty)
        self.assertEqual(man_order.bom_id, self.main_bom)
        man_order.action_assign()
        man_order.button_plan()
        self.assertTrue(man_order.workorder_ids)
        initial_duration_expected = man_order.workorder_ids.duration_expected
        deviation_data = self.get_deviation_data(man_order)
        self.assertTrue(deviation_data)
        subproduct_1_1_expected_qty = (3 * 2) + (5 * 2)
        subproduct_2_1_expected_qty = 8
        subproduct_1_1_deviation_datas = [
            x
            for x in deviation_data
            if x.get("product_id", False)
            and x["product_id"][0] == self.subproduct_1_1.id
        ]
        self.assertAlmostEqual(
            sum(x["cost"] for x in subproduct_1_1_deviation_datas), 0
        )
        self.assertAlmostEqual(
            max(x["unit_cost"] for x in subproduct_1_1_deviation_datas),
            self.subproduct_1_1.standard_price,
        )
        self.assertAlmostEqual(
            sum(x["duration_expected"] for x in subproduct_1_1_deviation_datas), 0
        )
        self.assertAlmostEqual(
            sum(x["duration_expected_rw"] for x in subproduct_1_1_deviation_datas), 0
        )
        self.assertAlmostEqual(
            sum(x["quantity_expected"] for x in subproduct_1_1_deviation_datas),
            subproduct_1_1_expected_qty * production_qty,
        )
        self.assertAlmostEqual(
            sum(x["product_qty"] for x in subproduct_1_1_deviation_datas), 0
        )
        self.assertAlmostEqual(
            sum(x["cost_expected"] for x in subproduct_1_1_deviation_datas),
            subproduct_1_1_expected_qty
            * production_qty
            * self.subproduct_1_1.standard_price,
        )
        self.assertAlmostEqual(
            sum(x["cost_expected_rw"] for x in subproduct_1_1_deviation_datas), 0
        )

        # create workorder to add relative costs
        deviation_data_1 = self.get_deviation_data(man_order)
        duration_expected = (
            (
                self.operation1.time_cycle_manual
                / (self.workcenter1.time_efficiency / 100)
            )
            * production_qty
            + self.workcenter1.time_start
            + self.workcenter1.time_stop
        )
        duration_expected_rw = self.operation1.time_cycle_manual * production_qty
        workorders_data = [x for x in deviation_data_1 if not x["product_id"]]
        self.assertAlmostEqual(
            workorders_data[0].get("duration_expected_rw"), duration_expected_rw
        )
        self.assertAlmostEqual(
            workorders_data[0].get("duration_expected"), duration_expected
        )
        self.assertAlmostEqual(
            workorders_data[0].get("cost_expected"),
            duration_expected / 60 * self.workcenter1.costs_hour,
        )
        self.assertAlmostEqual(
            workorders_data[0].get("cost_expected_rw"),
            duration_expected_rw / 60 * self.workcenter1.costs_hour,
        )
        self.assertEqual(deviation_data[0], deviation_data_1[0])
        self.assertEqual(deviation_data[1], deviation_data_1[1])
        # produce partially
        produced_qty = 2.0
        man_order.qty_producing = produced_qty
        self._auto_fill_consumed_qty(man_order.move_raw_ids)
        action = man_order.button_mark_done()
        consume_warning_form = Form(
            self.env["mrp.consumption.warning"].with_context(**action["context"])
        )
        action_backorder = consume_warning_form.save().action_confirm()
        backorder_form = Form(
            self.env["mrp.production.backorder"].with_context(
                **action_backorder["context"]
            )
        )
        backorder_form.save().action_backorder()
        self.assertEqual(len(man_order.procurement_group_id.mrp_production_ids), 2)
        self.assertEqual(man_order.state, "done")
        # switch checks on backorder production
        mo_backorder = man_order.procurement_group_id.mrp_production_ids[-1]
        mo_backorder.flush()
        self.assertEqual(mo_backorder.state, "progress")
        backorder_qty = production_qty - produced_qty
        # when doing a backorder, duration_expected of production and backorders are
        # computed with a ratio on initial product_qty, so don't compute directly
        backorder_duration_expected = initial_duration_expected * (
            1 - (produced_qty / production_qty)
        )
        backorder_duration_expected_rw = (
            self.operation1.time_cycle_manual * backorder_qty
        )
        deviation_data_2 = self.get_deviation_data(mo_backorder)
        self.assertTrue(deviation_data_2)
        subproduct_1_1_deviation_datas_2 = [
            x
            for x in deviation_data_2
            if x.get("product_id", False)
            and x["product_id"][0] == self.subproduct_1_1.id
        ]
        self.assertAlmostEqual(
            sum(x["cost"] for x in subproduct_1_1_deviation_datas_2),
            self.subproduct_1_1.standard_price
            * subproduct_1_1_expected_qty
            * backorder_qty,
        )
        self.assertAlmostEqual(
            sum(x["quantity_expected"] for x in subproduct_1_1_deviation_datas_2),
            subproduct_1_1_expected_qty * backorder_qty,
        )
        self.assertAlmostEqual(
            sum(x["product_qty"] for x in subproduct_1_1_deviation_datas_2),
            subproduct_1_1_expected_qty * backorder_qty,
        )
        self.assertAlmostEqual(
            sum(x["cost_expected"] for x in subproduct_1_1_deviation_datas_2),
            subproduct_1_1_expected_qty
            * backorder_qty
            * self.subproduct_1_1.standard_price,
        )
        self.assertAlmostEqual(
            sum(x["cost_expected_rw"] for x in subproduct_1_1_deviation_datas_2), 0
        )
        workorders_data_1 = [x for x in deviation_data_2 if not x["product_id"]]
        self.assertAlmostEqual(
            workorders_data_1[0].get("duration_expected_rw"),
            backorder_duration_expected_rw,
            self.assertAlmostEqual(
                workorders_data_1[0].get("cost_expected_rw"),
                backorder_duration_expected_rw / 60 * self.workcenter1.costs_hour,
            ),
        )
        self.assertAlmostEqual(
            workorders_data_1[0].get("duration_expected"), backorder_duration_expected
        )
        self.assertAlmostEqual(
            workorders_data_1[0].get("cost_expected"),
            backorder_duration_expected / 60 * self.workcenter1.costs_hour,
        )

        # add some extra-bom raw material to backorder production
        mo_backorder.action_toggle_is_locked()
        mo_backorder.write(
            {
                "move_raw_ids": [
                    (
                        0,
                        0,
                        {
                            "name": self.product_2.name,
                            "product_id": self.product_2.id,
                            "product_uom": self.product_2.uom_id.id,
                            "location_id": mo_backorder.location_src_id.id,
                            "location_dest_id": mo_backorder.location_dest_id.id,
                            "state": "confirmed",
                            "raw_material_production_id": mo_backorder.id,
                            "picking_type_id": mo_backorder.picking_type_id.id,
                        },
                    ),
                ]
            }
        )
        mo_backorder.action_toggle_is_locked()
        move_raw = mo_backorder.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_2
        )
        self.env["mrp.production.component.change"].with_context(
            active_id=move_raw.id,
            active_model="stock.move",
        ).create(
            [
                {
                    "product_uom_qty": 3,
                }
            ]
        ).action_done()
        self.assertEqual(len(mo_backorder.move_raw_ids), 4)
        self.assertEqual(move_raw.product_uom_qty, 3)
        self.assertEqual(move_raw.quantity_done, 0)

        # complete production, changing quantity done for additional component
        produced_qty = 3.0
        mo_backorder_form = Form(mo_backorder)
        mo_backorder_form.qty_producing = produced_qty
        mo_backorder = mo_backorder_form.save()
        self._auto_fill_consumed_qty(mo_backorder.move_raw_ids)
        action = mo_backorder.button_mark_done()
        move_raw = mo_backorder.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_2
        )
        self.assertEqual(move_raw.quantity_done, 3)
        move_raw_sub_2_1 = mo_backorder.move_raw_ids.filtered(
            lambda x: x.product_id == self.subproduct_2_1
        )
        self.assertAlmostEqual(
            sum(move_raw_sub_2_1.mapped("quantity_done")),
            subproduct_2_1_expected_qty * backorder_qty,
        )
        # how-to restore this check and what is the use case? see changes at the end of
        # the test
        # sml_ids = self.env["stock.move.line"].search(
        #     [("move_id", "in", move_raw_sub_2_1.ids)]
        # )
        # sml_ids.unlink()
        # self.assertAlmostEqual(move_raw_sub_2_1.quantity_done, 0.0)
        # end check removed
        self._auto_fill_consumed_qty(man_order.move_raw_ids)
        consume_warning_form = Form(
            self.env["mrp.consumption.warning"].with_context(**action["context"])
        )
        consume_warning_form.save().action_confirm()
        self.assertEqual(mo_backorder.state, "done")
        deviation_data_3 = self.get_deviation_data(mo_backorder)
        self.assertTrue(deviation_data_3)
        subproduct_1_1_deviation_datas_3 = [
            x
            for x in deviation_data_3
            if x.get("product_id", False)
            and x["product_id"][0] == self.subproduct_1_1.id
        ]
        self.assertAlmostEqual(
            sum(x["cost"] for x in subproduct_1_1_deviation_datas_3),
            self.subproduct_1_1.standard_price
            * subproduct_1_1_expected_qty
            * produced_qty,
        )
        self.assertAlmostEqual(
            sum(x["unit_cost"] for x in subproduct_1_1_deviation_datas_3), 10
        )
        self.assertAlmostEqual(
            sum(x["quantity_expected"] for x in subproduct_1_1_deviation_datas_3),
            subproduct_1_1_expected_qty * backorder_qty,
        )
        self.assertAlmostEqual(
            sum(x["product_qty"] for x in subproduct_1_1_deviation_datas_3),
            subproduct_1_1_expected_qty * produced_qty,
        )
        self.assertAlmostEqual(
            sum(x["cost_expected"] for x in subproduct_1_1_deviation_datas_3),
            subproduct_1_1_expected_qty
            * backorder_qty
            * self.subproduct_1_1.standard_price,
        )
        self.assertAlmostEqual(
            sum(x["cost_expected_rw"] for x in subproduct_1_1_deviation_datas_3), 0
        )

        old_standard_price = self.subproduct_1_1.standard_price
        self.subproduct_1_1.standard_price = 33.45
        deviation_data_4 = self.get_deviation_data(mo_backorder)
        subproduct_1_1_deviation_datas_4 = [
            x
            for x in deviation_data_4
            if x.get("product_id", False)
            and x["product_id"][0] == self.subproduct_1_1.id
        ]
        self.assertAlmostEqual(
            sum(x["cost"] for x in subproduct_1_1_deviation_datas_4),
            old_standard_price * subproduct_1_1_expected_qty * produced_qty,
        )
        self.assertAlmostEqual(
            sum(x["cost_current"] for x in subproduct_1_1_deviation_datas_4),
            self.subproduct_1_1.standard_price
            * subproduct_1_1_expected_qty
            * produced_qty,
        )
        self.subproduct_1_1.standard_price = 10

        # check product_2 has correct report values
        product_2_deviation_datas_4 = [
            x
            for x in deviation_data_4
            if x.get("product_id", False) and x["product_id"][0] == self.product_2.id
        ]
        self.assertAlmostEqual(
            sum(x["cost_expected"] for x in product_2_deviation_datas_4), 0
        )
        self.assertAlmostEqual(
            sum(x["quantity_expected"] for x in product_2_deviation_datas_4), 0
        )
        self.assertAlmostEqual(
            sum(x["product_qty"] for x in product_2_deviation_datas_4), 3
        )
        self.assertAlmostEqual(
            sum(x["cost_current"] for x in product_2_deviation_datas_4),
            3 * self.product_2.standard_price,
        )

        # check subproduct_2_1 has correct report values
        subproduct_2_1_deviation_datas_4 = [
            x
            for x in deviation_data_4
            if x.get("product_id", False)
            and x["product_id"][0] == self.subproduct_2_1.id
        ]
        self.assertAlmostEqual(
            sum(x["cost_expected"] for x in subproduct_2_1_deviation_datas_4),
            subproduct_2_1_expected_qty
            * produced_qty
            * self.subproduct_2_1.standard_price,
        )
        self.assertAlmostEqual(
            sum(x["quantity_expected"] for x in subproduct_2_1_deviation_datas_4),
            subproduct_2_1_expected_qty * produced_qty,
        )
        self.assertAlmostEqual(
            sum(x["product_qty"] for x in subproduct_2_1_deviation_datas_4),
            subproduct_2_1_expected_qty * produced_qty,
            # todo this will be 0 with the removed previous check
        )
        self.assertAlmostEqual(
            sum(x["cost"] for x in subproduct_2_1_deviation_datas_4),
            subproduct_2_1_expected_qty
            * produced_qty
            * self.subproduct_2_1.standard_price,
            # todo this will be 0 with the removed previous check
        )

    def test_02_mo_deviation_data_serial(self):
        production_qty = 5
        self.top_product.tracking = "serial"
        # put only product_id and product_qty in the wizard data to avoid the default
        # setting of product_qty to 1
        man_order_form = Form(self.env["mrp.production"])
        man_order_form.product_id = self.top_product
        man_order_form.product_qty = production_qty
        man_order = man_order_form.save()
        self.assertEqual(man_order.product_qty, production_qty)
        self.assertEqual(man_order.bom_id, self.main_bom)
        man_order.action_assign()
        man_order.button_plan()
        self.assertTrue(man_order.workorder_ids)
        initial_duration_expected = man_order.workorder_ids.duration_expected
        deviation_data = self.get_deviation_data(man_order)
        self.assertTrue(deviation_data)
        subproduct_1_1_expected_qty = (3 * 2) + (5 * 2)
        subproduct_2_1_expected_qty = 8
        subproduct_1_1_deviation_datas = [
            x
            for x in deviation_data
            if x.get("product_id", False)
            and x["product_id"][0] == self.subproduct_1_1.id
        ]
        self.assertAlmostEqual(
            sum(x["cost"] for x in subproduct_1_1_deviation_datas), 0
        )
        self.assertAlmostEqual(
            max(x["unit_cost"] for x in subproduct_1_1_deviation_datas),
            self.subproduct_1_1.standard_price,
        )
        self.assertAlmostEqual(
            sum(x["duration_expected"] for x in subproduct_1_1_deviation_datas), 0
        )
        self.assertAlmostEqual(
            sum(x["duration_expected_rw"] for x in subproduct_1_1_deviation_datas), 0
        )
        self.assertAlmostEqual(
            sum(x["quantity_expected"] for x in subproduct_1_1_deviation_datas),
            subproduct_1_1_expected_qty * production_qty,
        )
        self.assertAlmostEqual(
            sum(x["product_qty"] for x in subproduct_1_1_deviation_datas), 0
        )
        self.assertAlmostEqual(
            sum(x["cost_expected"] for x in subproduct_1_1_deviation_datas),
            subproduct_1_1_expected_qty
            * production_qty
            * self.subproduct_1_1.standard_price,
        )
        self.assertAlmostEqual(
            sum(x["cost_expected_rw"] for x in subproduct_1_1_deviation_datas), 0
        )

        # create workorder to add relative costs
        deviation_data_1 = self.get_deviation_data(man_order)
        duration_expected = (
            (
                self.operation1.time_cycle_manual
                / (self.workcenter1.time_efficiency / 100)
            )
            * production_qty
            + self.workcenter1.time_start
            + self.workcenter1.time_stop
        )
        duration_expected_rw = self.operation1.time_cycle_manual * production_qty
        workorders_data = [x for x in deviation_data_1 if not x["product_id"]]
        self.assertAlmostEqual(
            workorders_data[0].get("duration_expected_rw"), duration_expected_rw
        )
        self.assertAlmostEqual(
            workorders_data[0].get("duration_expected"), duration_expected
        )
        self.assertAlmostEqual(
            workorders_data[0].get("cost_expected"),
            duration_expected / 60 * self.workcenter1.costs_hour,
        )
        self.assertAlmostEqual(
            workorders_data[0].get("cost_expected_rw"),
            duration_expected_rw / 60 * self.workcenter1.costs_hour,
        )
        self.assertEqual(deviation_data[0], deviation_data_1[0])
        self.assertEqual(deviation_data[1], deviation_data_1[1])
        # produce partially, serial will force qty to 1 anyway
        produced_qty = 1.0
        man_order.qty_producing = produced_qty
        man_order.action_generate_serial()
        self.assertTrue(man_order.lot_producing_id)
        self._auto_fill_consumed_qty(man_order.move_raw_ids)
        action = man_order.button_mark_done()
        consume_warning_form = Form(
            self.env["mrp.consumption.warning"].with_context(**action["context"])
        )
        action_backorder = consume_warning_form.save().action_confirm()
        backorder_form = Form(
            self.env["mrp.production.backorder"].with_context(
                **action_backorder["context"]
            )
        )
        backorder_form.save().action_backorder()
        self.assertEqual(len(man_order.procurement_group_id.mrp_production_ids), 2)
        self.assertEqual(man_order.state, "done")
        # switch checks on backorder production
        mo_backorder = man_order.procurement_group_id.mrp_production_ids[-1]
        mo_backorder.flush()
        self.assertEqual(mo_backorder.state, "progress")
        mo_backorder.action_generate_serial()
        backorder_qty = 1
        # when doing a backorder, duration_expected of production and backorders are
        # computed with a ratio on initial product_qty, so don't compute directly
        backorder_duration_expected = initial_duration_expected * (
            1 - (produced_qty / production_qty)
        )
        backorder_duration_expected_rw = (
            self.operation1.time_cycle_manual * backorder_qty
        )
        self.assertEqual(man_order.workorder_ids.state, "done")
        deviation_data_2 = self.get_deviation_data(mo_backorder)
        self.assertTrue(deviation_data_2)
        subproduct_1_1_deviation_datas_2 = [
            x
            for x in deviation_data_2
            if x.get("product_id", False)
            and x["product_id"][0] == self.subproduct_1_1.id
        ]
        self.assertAlmostEqual(
            sum(x["cost"] for x in subproduct_1_1_deviation_datas_2),
            self.subproduct_1_1.standard_price
            * subproduct_1_1_expected_qty
            * backorder_qty,
        )
        # backorder has still total qty set to 4 until it's done
        self.assertAlmostEqual(
            sum(x["quantity_expected"] for x in subproduct_1_1_deviation_datas_2),
            subproduct_1_1_expected_qty * mo_backorder.product_uom_qty,
        )
        self.assertAlmostEqual(
            sum(x["cost_expected"] for x in subproduct_1_1_deviation_datas_2),
            subproduct_1_1_expected_qty
            * mo_backorder.product_uom_qty
            * self.subproduct_1_1.standard_price,
        )
        # different qty until here
        self.assertAlmostEqual(
            sum(x["product_qty"] for x in subproduct_1_1_deviation_datas_2),
            subproduct_1_1_expected_qty * backorder_qty,
        )
        self.assertAlmostEqual(
            sum(x["cost_expected_rw"] for x in subproduct_1_1_deviation_datas_2), 0
        )
        workorders_data_1 = [x for x in deviation_data_2 if not x["product_id"]]
        self.assertAlmostEqual(
            workorders_data_1[0].get("duration_expected_rw"),
            backorder_duration_expected_rw * mo_backorder.product_uom_qty,
        )
        self.assertAlmostEqual(
            workorders_data_1[0].get("cost_expected_rw"),
            backorder_duration_expected_rw
            / 60
            * self.workcenter1.costs_hour
            * mo_backorder.product_uom_qty,
        )
        self.assertAlmostEqual(
            workorders_data_1[0].get("duration_expected"), backorder_duration_expected
        )
        self.assertAlmostEqual(
            workorders_data_1[0].get("cost_expected"),
            backorder_duration_expected / 60 * self.workcenter1.costs_hour,
        )

        # add some extra-bom raw material to backorder production
        mo_backorder.action_toggle_is_locked()
        mo_backorder.write(
            {
                "move_raw_ids": [
                    (
                        0,
                        0,
                        {
                            "name": self.product_2.name,
                            "product_id": self.product_2.id,
                            "product_uom": self.product_2.uom_id.id,
                            "location_id": mo_backorder.location_src_id.id,
                            "location_dest_id": mo_backorder.location_dest_id.id,
                            "state": "confirmed",
                            "raw_material_production_id": mo_backorder.id,
                            "picking_type_id": mo_backorder.picking_type_id.id,
                        },
                    ),
                ]
            }
        )
        mo_backorder.action_toggle_is_locked()
        move_raw = mo_backorder.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_2
        )
        self.env["mrp.production.component.change"].with_context(
            active_id=move_raw.id,
            active_model="stock.move",
        ).create(
            [
                {
                    "product_uom_qty": 3,
                }
            ]
        ).action_done()
        self.assertEqual(len(mo_backorder.move_raw_ids), 4)
        self.assertEqual(move_raw.product_uom_qty, 3)
        self.assertEqual(move_raw.quantity_done, 0)

        # complete production, changing quantity done for additional component
        produced_qty = 1.0
        mo_backorder_form = Form(mo_backorder)
        mo_backorder_form.qty_producing = produced_qty
        mo_backorder = mo_backorder_form.save()
        mo_backorder.action_generate_serial()

        action = mo_backorder.button_mark_done()
        consume_warning_form = Form(
            self.env["mrp.consumption.warning"].with_context(**action["context"])
        )
        action_backorder = consume_warning_form.save().action_confirm()
        backorder_form = Form(
            self.env["mrp.production.backorder"].with_context(
                **action_backorder["context"],
                skip_backorder=True,
            )
        )
        backorder_form.save().action_backorder()
        self.assertEqual(len(man_order.procurement_group_id.mrp_production_ids), 3)
        self.assertEqual(man_order.state, "done")
        self.assertEqual(mo_backorder.state, "done")
        last_mo_backorder = man_order.procurement_group_id.mrp_production_ids[-1]
        self.assertTrue(last_mo_backorder)
        move_raws = mo_backorder.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_2
        )
        self.assertEqual(mo_backorder.workorder_ids.state, "done")
        self.assertEqual(sum(move_raws.mapped("quantity_done")), 3 / 4)
        move_raw_sub_2_1 = mo_backorder.move_raw_ids.filtered(
            lambda x: x.product_id == self.subproduct_2_1
        )
        self.assertAlmostEqual(
            sum(move_raw_sub_2_1.mapped("quantity_done")),
            subproduct_2_1_expected_qty * mo_backorder.product_uom_qty,
        )
        # how-to restore this check and what is the use case? see changes at the end of
        # the test
        # sml_ids = self.env["stock.move.line"].search(
        #     [("move_id", "in", move_raw_sub_2_1.ids)]
        # )
        # sml_ids.unlink()
        # self.assertAlmostEqual(move_raw_sub_2_1.quantity_done, 0.0)
        # end check removed

        deviation_data_3 = self.get_deviation_data(mo_backorder)
        self.assertTrue(deviation_data_3)
        subproduct_1_1_deviation_datas_3 = [
            x
            for x in deviation_data_3
            if x.get("product_id", False)
            and x["product_id"][0] == self.subproduct_1_1.id
        ]
        self.assertAlmostEqual(
            sum(x["cost"] for x in subproduct_1_1_deviation_datas_3),
            self.subproduct_1_1.standard_price
            * subproduct_1_1_expected_qty
            * produced_qty,
        )
        self.assertAlmostEqual(
            sum(x["unit_cost"] for x in subproduct_1_1_deviation_datas_3), 10
        )
        self.assertAlmostEqual(
            sum(x["quantity_expected"] for x in subproduct_1_1_deviation_datas_3),
            subproduct_1_1_expected_qty * backorder_qty,
        )
        self.assertAlmostEqual(
            sum(x["product_qty"] for x in subproduct_1_1_deviation_datas_3),
            subproduct_1_1_expected_qty * produced_qty,
        )
        self.assertAlmostEqual(
            sum(x["cost_expected"] for x in subproduct_1_1_deviation_datas_3),
            subproduct_1_1_expected_qty
            * backorder_qty
            * self.subproduct_1_1.standard_price,
        )
        self.assertAlmostEqual(
            sum(x["cost_expected_rw"] for x in subproduct_1_1_deviation_datas_3), 0
        )

        old_standard_price = self.subproduct_1_1.standard_price
        self.subproduct_1_1.standard_price = 33.45
        deviation_data_4 = self.get_deviation_data(mo_backorder)
        subproduct_1_1_deviation_datas_4 = [
            x
            for x in deviation_data_4
            if x.get("product_id", False)
            and x["product_id"][0] == self.subproduct_1_1.id
        ]
        self.assertAlmostEqual(
            sum(x["cost"] for x in subproduct_1_1_deviation_datas_4),
            old_standard_price * subproduct_1_1_expected_qty * produced_qty,
        )
        self.assertAlmostEqual(
            sum(x["cost_current"] for x in subproduct_1_1_deviation_datas_4),
            self.subproduct_1_1.standard_price
            * subproduct_1_1_expected_qty
            * produced_qty,
        )
        self.subproduct_1_1.standard_price = 10

        # check product_2 has correct report values
        product_2_deviation_datas_4 = [
            x
            for x in deviation_data_4
            if x.get("product_id", False) and x["product_id"][0] == self.product_2.id
        ]
        self.assertAlmostEqual(
            sum(x["cost_expected"] for x in product_2_deviation_datas_4), 0
        )
        self.assertAlmostEqual(
            sum(x["quantity_expected"] for x in product_2_deviation_datas_4), 0
        )
        self.assertAlmostEqual(
            sum(x["product_qty"] for x in product_2_deviation_datas_4), 3 / 4
        )
        self.assertAlmostEqual(
            sum(x["cost_current"] for x in product_2_deviation_datas_4),
            3 / 4 * self.product_2.standard_price,
        )

        # check subproduct_2_1 has correct report values
        subproduct_2_1_deviation_datas_4 = [
            x
            for x in deviation_data_4
            if x.get("product_id", False)
            and x["product_id"][0] == self.subproduct_2_1.id
        ]
        self.assertAlmostEqual(
            sum(x["cost_expected"] for x in subproduct_2_1_deviation_datas_4),
            subproduct_2_1_expected_qty
            * produced_qty
            * self.subproduct_2_1.standard_price,
        )
        self.assertAlmostEqual(
            sum(x["quantity_expected"] for x in subproduct_2_1_deviation_datas_4),
            subproduct_2_1_expected_qty * produced_qty,
        )
        self.assertAlmostEqual(
            sum(x["product_qty"] for x in subproduct_2_1_deviation_datas_4),
            subproduct_2_1_expected_qty * produced_qty,
            # todo this will be 0 with the removed previous check
        )
        self.assertAlmostEqual(
            sum(x["cost"] for x in subproduct_2_1_deviation_datas_4),
            subproduct_2_1_expected_qty
            * produced_qty
            * self.subproduct_2_1.standard_price,
            # todo this will be 0 with the removed previous check
        )
