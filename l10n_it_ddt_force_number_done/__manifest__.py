# Copyright 2018-2020 Sergio Corato <https://github.com/sergiocorato>
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).
{
    'name': "Change DDT Number on done state",
    'version': '12.0.1.0.0',
    'category': 'other',
    'description': """Allows ddt change number on done state. 
    """,
    'author': "Sergio Corato",
    'website': 'http://www.efatto.it',
    'license': 'AGPL-3',
    'depends': [
        'l10n_it_ddt_force_number',
    ],
    'data': [
        'views/stock.xml',
    ],
    'installable': True,
}
