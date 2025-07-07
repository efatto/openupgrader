from odoo import fields
from odoo.tests.common import Form, SavepointCase
from odoo.tools import mute_logger
from odoo.tools.date_utils import relativedelta


class AccountInvoiceSupplierinfoUpdateFix(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.user_model = cls.env["res.users"].with_context(no_reset_password=True)
        cls.wizard_obj = cls.env["wizard.update.invoice.supplierinfo"]
        expenses = cls.env.ref("account.data_account_type_expenses").id
        cls.invoice_line_account_id = (
            cls.env["account.account"]
            .search([("user_type_id", "=", expenses)], limit=1)
            .id
        )
        cls.vendor = cls.env.ref("base.res_partner_3")
        cls.vendor1 = cls.env.ref("base.res_partner_4")
        cls.vendor2 = cls.env.ref("base.res_partner_12")
        supplierinfo = cls.env["product.supplierinfo"]
        for vendor in cls.vendor | cls.vendor1 | cls.vendor2:
            supplierinfo |= cls.env["product.supplierinfo"].create(
                [
                    {
                        "price": 60.0,
                        "name": vendor.id,
                    }
                ]
            )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Product Test",
                "type": "consu",
                "standard_price": 50.0,
                "seller_ids": [(6, 0, supplierinfo.ids)],
            }
        )
        cls.test_user = cls.env["res.users"].create(
            {
                "name": "John",
                "login": "test",
            }
        )
        cls.used_date = fields.Date.from_string("2025-06-23")
        cls.tax = cls.env["account.tax"].create(
            {"name": "Tax 20", "type_tax_use": "sale", "amount": 20}
        )
        cls.invoice = cls.env["account.move"].create(
            [
                {
                    "partner_id": cls.vendor.id,
                    "date": "2025-06-23",
                    "invoice_date": cls.used_date + relativedelta(days=-5),
                    "move_type": "in_invoice",
                    "invoice_line_ids": [
                        (
                            0,
                            0,
                            {
                                "name": "test",
                                "product_id": cls.product.id,
                                "quantity": 10.0,
                                "price_unit": cls.product.standard_price,
                                "account_id": cls.invoice_line_account_id,
                                "tax_ids": [(6, 0, cls.tax.ids)],
                            },
                        )
                    ],
                }
            ]
        )
        cls.invoice._onchange_invoice_line_ids()
        cls.invoice.action_post()

    @mute_logger("odoo.models", "odoo.models.unlink", "odoo.addons.base.ir.ir_model")
    def test_00_supplierinfo_update(self):
        invoice_line = self.invoice.invoice_line_ids
        self.assertEqual(invoice_line.product_id, self.product)
        supplierinfo = self.product.seller_ids.filtered(lambda x: x.name == self.vendor)
        self.assertAlmostEqual(supplierinfo.price, 60.0)

        # changed from v. 12.0: it is no more needed to check if it is raised an error
        # if we try to check prices before setting the invoice date, as is it not
        # possible to validate a purchase invoice without invoice date
        wizard = self.wizard_obj.with_context(
            self.invoice.check_supplierinfo()["context"]
        ).create({})
        line = wizard.line_ids.filtered(lambda x: x.product_id == self.product)
        self.assertTrue(line)
        wizard.update_supplierinfo()
        self.assertAlmostEqual(supplierinfo.price, self.product.standard_price)

    def test_01_supplierinfo_create(self):
        invoice_line = self.invoice.invoice_line_ids
        self.assertEqual(invoice_line.product_id, self.product)
        supplierinfo = self.product.seller_ids.filtered(lambda x: x.name == self.vendor)
        self.assertAlmostEqual(supplierinfo.price, 60.0)

        # Set supplierinfo expired to force the creation of a new supplierinfo
        supplierinfo.date_end = self.used_date + relativedelta(days=-10)
        self.invoice.button_cancel()
        self.invoice.button_draft()
        invoice_form = Form(self.invoice)
        invoice_form.supplierinfo_ok = False
        invoice_form.invoice_date = self.used_date + relativedelta(days=-5)
        with invoice_form.invoice_line_ids.edit(0) as invoice_line:
            invoice_line.price_unit = 11.0
        invoice_form.save()
        wizard = self.wizard_obj.with_context(
            self.invoice.check_supplierinfo()["context"]
        ).create({})
        line = wizard.line_ids.filtered(lambda x: x.product_id == self.product)
        self.assertTrue(line)
        wizard.update_supplierinfo()

        supplierinfos = self.product.seller_ids.filtered(
            lambda x: x.name == self.vendor
        )
        supplierinfo1 = supplierinfos - supplierinfo
        self.assertTrue(supplierinfo1)
        self.assertAlmostEqual(supplierinfo1.price, 11.0)

    def test_02_supplierinfo_update_past(self):
        invoice_line = self.invoice.invoice_line_ids
        self.assertEqual(invoice_line.product_id, self.product)
        supplierinfo = self.product.seller_ids.filtered(lambda x: x.name == self.vendor)
        self.assertAlmostEqual(supplierinfo.price, 60.0)
        # Set supplierinfo valid in the past and invoice dated in the past to match
        supplierinfo.date_start = self.used_date + relativedelta(days=-100)
        supplierinfo.date_end = self.used_date + relativedelta(days=-10)
        self.invoice.button_cancel()
        self.invoice.button_draft()
        invoice_form = Form(self.invoice)
        invoice_form.supplierinfo_ok = False
        invoice_form.invoice_date = self.used_date + relativedelta(days=-20)
        with invoice_form.invoice_line_ids.edit(0) as invoice_line:
            invoice_line.price_unit = 22.0
        invoice_form.save()
        wizard = self.wizard_obj.with_context(
            self.invoice.check_supplierinfo()["context"]
        ).create({})
        line = wizard.line_ids.filtered(lambda x: x.product_id == self.product)
        self.assertTrue(line)
        wizard.update_supplierinfo()

        supplierinfos = self.product.seller_ids.filtered(
            lambda x: x.name == self.vendor
        )
        # changed from v. 12.0: check there is only only supplierinfo, as the invoice is
        # the same, with a changed price
        self.assertEqual(len(supplierinfos), 1)
        self.assertAlmostEqual(supplierinfos.price, 22.0)
