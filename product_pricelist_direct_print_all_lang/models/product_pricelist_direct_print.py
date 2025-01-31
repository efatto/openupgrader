from odoo import _, models


class ProductPricelistXlsx(models.AbstractModel):
    _inherit = "report.product_pricelist_direct_print.product_pricelist_xlsx"

    def _add_extra_header(self, sheet, book, next_col, header_format):
        next_col = super()._add_extra_header(sheet, book, next_col, header_format)
        if book.show_all_langs:
            for lang in self.env["res.lang"].search([]):
                next_col += 1
                sheet.write(5, next_col, _(lang.name), header_format)
            # Override first header column with default code
            sheet.write(5, 0, _("Default Code"), header_format)
        return next_col

    def _add_extra_info(self, sheet, book, product, row, next_col):
        next_col = super()._add_extra_info(sheet, book, product, row, next_col)
        if book.show_all_langs:
            for lang in self.env["res.lang"].search([]):
                next_col += 1
                sheet.write(row, next_col, product.with_context(lang=lang.code).name)
            # Override first column with default code
            sheet.write(row, 0, product.default_code or "")
        return next_col
