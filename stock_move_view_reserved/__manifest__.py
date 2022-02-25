# Copyright 2017-2 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

{
    'name': 'Stock move reserved qty in tree',
    'version': '12.0.1.0.2',
    'category': 'other',
    'description': """
    Show stock move reserved quantity and date in tree view.
    """,
    'author': 'Sergio Corato',
    'website': 'https://www.efatto.it',
    'license': 'AGPL-3',
    'depends': [
        'mrp',
        'stock_move_view_sale_purchase',
    ],
    'data': [
        'views/stock.xml',
    ],
    'installable': True,
}
