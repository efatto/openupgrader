from odoo.tests.common import SavepointCase
from odoo.tools import mute_logger
from odoo.tools.date_utils import relativedelta, date
from odoo.exceptions import UserError


class AccountInvoiceSupplierinfoUpdateFix(SavepointCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.user_model = cls.env['res.users'].with_context(no_reset_password=True)
        cls.wizard_obj = cls.env['wizard.update.invoice.supplierinfo']
        payable = cls.env.ref('account.data_account_type_payable').id
        expenses = cls.env.ref('account.data_account_type_expenses').id
        cls.invoice_account_id = cls.env['account.account'].search(
            [('user_type_id', '=', payable)], limit=1).id
        cls.invoice_line_account_id = cls.env['account.account'].search(
            [('user_type_id', '=', expenses)], limit=1).id
        cls.vendor = cls.env.ref('base.res_partner_3')
        cls.vendor1 = cls.env.ref('base.res_partner_4')
        cls.vendor2 = cls.env.ref('base.res_partner_12')
        supplierinfo = cls.env['product.supplierinfo']
        for vendor in cls.vendor | cls.vendor1 | cls.vendor2:
            supplierinfo |= cls.env['product.supplierinfo'].create([{
                'price': 60.0,
                'name': vendor.id,
            }])
        cls.product = cls.env['product.product'].create({
            'name': 'Product Test',
            'type': 'consu',
            'standard_price': 50.0,
            'seller_ids': [(6, 0, supplierinfo.ids)],
        })
        cls.test_user = cls.env['res.users'].create({
            'name': 'John',
            'login': 'test',
        })
        cls.invoice = cls.env['account.invoice'].create([{
            'partner_id': cls.vendor.id,
            'type': 'in_invoice',
            'account_id': cls.invoice_account_id,
            'invoice_line_ids': [
                (0, 0, {
                    'name': 'test',
                    'product_id': cls.product.id,
                    'quantity': 10.0,
                    'price_unit': cls.product.standard_price,
                    'account_id': cls.invoice_line_account_id,
                })
            ]
        }])

    @mute_logger(
        'odoo.models', 'odoo.models.unlink', 'odoo.addons.base.ir.ir_model'
    )
    def test_00_supplierinfo_update(self):
        today = date.today()
        invoice_line = self.invoice.invoice_line_ids
        self.assertEqual(invoice_line.product_id, self.product)
        supplierinfo = self.product.seller_ids.filtered(
            lambda x: x.name == self.vendor
        )
        self.assertAlmostEqual(supplierinfo.price, 60.0)

        with self.assertRaises(UserError):
            self.wizard_obj.with_context(
                self.invoice.check_supplierinfo()['context']).create({})
        self.invoice.date_invoice = today + relativedelta(days=-5)
        wizard = self.wizard_obj.with_context(
            self.invoice.check_supplierinfo()['context']).create({})
        line = wizard.line_ids.filtered(
            lambda x: x.product_id == self.product)
        self.assertTrue(line)
        wizard.update_supplierinfo()
        self.assertAlmostEqual(supplierinfo.price, self.product.standard_price)

    def test_01_supplierinfo_create(self):
        today = date.today()
        invoice_line = self.invoice.invoice_line_ids
        self.assertEqual(invoice_line.product_id, self.product)
        supplierinfo = self.product.seller_ids.filtered(
            lambda x: x.name == self.vendor
        )
        self.assertAlmostEqual(supplierinfo.price, 60.0)

        # Set supplierinfo expired to force the creation of a new supplierinfo
        supplierinfo.date_end = today + relativedelta(days=-10)
        self.invoice.write({'supplierinfo_ok': False})
        invoice_line.write({'price_unit': 11.0})
        self.invoice.date_invoice = today + relativedelta(days=-5)
        wizard = self.wizard_obj.with_context(
            self.invoice.check_supplierinfo()['context']).create({})
        line = wizard.line_ids.filtered(
            lambda x: x.product_id == self.product)
        self.assertTrue(line)
        wizard.update_supplierinfo()

        supplierinfos = self.product.seller_ids.filtered(
            lambda x: x.name == self.vendor
        )
        supplierinfo1 = supplierinfos - supplierinfo
        self.assertTrue(supplierinfo1)
        self.assertAlmostEqual(supplierinfo1.price, 11.0)

    def test_02_supplierinfo_update_past(self):
        today = date.today()
        invoice_line = self.invoice.invoice_line_ids
        self.assertEqual(invoice_line.product_id, self.product)
        supplierinfo = self.product.seller_ids.filtered(
            lambda x: x.name == self.vendor
        )
        self.assertAlmostEqual(supplierinfo.price, 60.0)
        # Set supplierinfo valid in the past and invoice dated in the past to match
        supplierinfo.date_start = today + relativedelta(days=-100)
        supplierinfo.date_end = today + relativedelta(days=-10)
        self.invoice.write({'supplierinfo_ok': False,
                            'date_invoice': today + relativedelta(days=-20)})
        invoice_line.write({'price_unit': 22.0})
        self.invoice.date_invoice = today + relativedelta(days=-5)
        wizard = self.wizard_obj.with_context(
            self.invoice.check_supplierinfo()['context']).create({})
        line = wizard.line_ids.filtered(
            lambda x: x.product_id == self.product)
        self.assertTrue(line)
        wizard.update_supplierinfo()

        supplierinfos = self.product.seller_ids.filtered(
            lambda x: x.name == self.vendor
        )
        supplierinfo1 = supplierinfos - supplierinfo
        self.assertTrue(supplierinfo1)
        self.assertAlmostEqual(supplierinfo1.price, 22.0)
