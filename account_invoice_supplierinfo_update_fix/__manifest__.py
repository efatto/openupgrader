# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    'name': 'Account Invoice Supplierinfo Update Fix',
    'version': '12.0.1.0.0',
    'summary': 'This module create new supplierinfo on bottom by default and '
               'add check on supplierinfo date validity, based on invoice date.',
    'author': 'Sergio Corato',
    'website': 'https://github.com/sergiocorato/efatto',
    'license': 'AGPL-3',
    'category': 'Accounting',
    'depends': [
        'account_invoice_supplierinfo_update',
    ],
    'data': [
    ],
    'installable': True,
}
