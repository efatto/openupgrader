# Copyright 2017-2020 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

{
    'name': 'Stock move purchase/sale ref in tree',
    'version': '12.0.1.0.2',
    'category': 'other',
    'description': """
    Show stock move sale and purchase order in tree view.
    """,
    'author': 'Sergio Corato',
    'website': 'https://github.com/sergiocorato/efatto',
    'license': 'AGPL-3',
    'depends': [
        'purchase_stock',
        'sale_stock',
    ],
    'data': [
        'views/stock.xml',
    ],
    'installable': True,
}
