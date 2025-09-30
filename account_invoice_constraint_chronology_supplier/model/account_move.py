# Copyright 2015-2019 ACSONE SA/NV (<http://acsone.eu>)
# Copyright 2020-2023 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    bypass_duplicate_vendor_ref = fields.Boolean()

    @api.model
    def _prepare_later_invoices_domain(self, invoice):
        domain = [
            ("journal_id", "=", invoice.journal_id.id),
            ("date", ">", invoice.date),
            ("name", "<", invoice.name),
            ("name", "!=", "/"),
        ]
        # check until last date of registration year date_range only
        registration_fy = self.env["account.fiscal.year"].search(
            [
                ("date_from", "<=", invoice.date),
                ("date_to", ">=", invoice.date),
                ("company_id", "=", invoice.company_id.id),
            ]
        )
        if registration_fy:
            domain.append(("date", "<=", registration_fy.date_to))
        # check on same move_type if sequence for refund is separate
        if invoice.journal_id.refund_sequence:
            domain.append(("move_type", "=", invoice.move_type))
        return domain

    def write(self, vals):
        if vals.get("state") != "posted":
            return super().write(vals)
        previously_validated = self.filtered(lambda m: m.name and m.name != "/")
        newly_posted = self.filtered(lambda m: m.state != "posted")
        res = super().write(vals)
        for inv in newly_posted & self.filtered("journal_id.check_chronology"):
            if inv.move_type in ("in_invoice", "in_refund"):
                if inv.invoice_date and inv.date and inv.invoice_date > inv.date:
                    raise UserError(
                        _(
                            "Supplier invoice date %s cannot be later than "
                            "the date of registration %s!"
                            % (
                                inv.invoice_date.strftime("%d/%m/%Y"),
                                inv.date.strftime("%d/%m/%Y"),
                            )
                        )
                    )
            if inv not in previously_validated:
                invoices = self.search(
                    self._prepare_later_invoices_domain(inv), order="date desc", limit=1
                )
                if invoices:
                    raise UserError(
                        _(
                            "Chronology Error. Post the invoice with an equal "
                            "or greater date than {invoice_date}."
                        ).format(invoice_date=invoices[0].date.strftime("%d/%m/%Y"))
                    )
        return res

    def _check_duplicate_supplier_reference(self):
        for invoice in self:
            # refuse to validate a vendor bill/credit note if there already exists one
            # with the same reference for the same partner for the same year (presuming
            # fiscal year of vendor is 1/1-31/12),
            # because it's probably a double encoding of the same bill/credit note
            # todo create a boolean to force bypass of this check
            if (
                invoice.move_type in ("in_invoice", "in_refund")
                and invoice.ref
                and not invoice.bypass_duplicate_vendor_ref
            ):
                first_year_date = (
                    invoice.invoice_date
                    and invoice.invoice_date.replace(month=1, day=1)
                    or fields.Date.today().replace(month=1, day=1)
                )
                last_year_date = first_year_date.replace(month=12, day=31)
                if self.search(
                    [
                        ("invoice_date", ">=", first_year_date),
                        ("invoice_date", "<=", last_year_date),
                        ("move_type", "=", invoice.move_type),
                        ("ref", "=", invoice.ref),
                        ("company_id", "=", invoice.company_id.id),
                        (
                            "commercial_partner_id",
                            "=",
                            invoice.commercial_partner_id.id,
                        ),
                        ("id", "!=", invoice.id),
                    ]
                ):
                    raise UserError(
                        _(
                            "Duplicated vendor reference detected. You probably encoded"
                            " twice the same vendor bill/credit note."
                        )
                    )
