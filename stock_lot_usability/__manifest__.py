# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    'name': 'Stock lot usability',
    'version': '12.0.1.0.0',
    'category': 'other',
    'description': """
    Stock lot qty in tree and removed groupby
    """,
    'author': 'Sergio Corato',
    'website': 'https://www.efatto.it',
    'license': 'AGPL-3',
    'depends': [
        'stock',
    ],
    'data': [
        'views/stock.xml',
    ],
    'installable': True,
}
