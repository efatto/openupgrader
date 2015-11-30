# -*- coding: utf-8 -*-
#
#
#    Copyright (C) 2015 SimplERP srl (<http://www.simplerp.it>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#
{
    'name': 'Configurazione ricevute bancarie',
    'version': '3.0.0.7',
    'category': 'Localisation/Italy',
    'description': """
    Ri.ba. sbf configuration for Italy.
""",
    'author': 'Corato Sergio',
    'website': 'http://www.simplerp.it',
    'license': 'AGPL-3',
    "depends": [
#         'account',
#         'base_vat',
#         'account_chart',
#         'base_iban',
#         'l10n_it_base',
        'l10n_it_ricevute_bancarie',
#         'account_cancel',
    ],
    "data": [
        'data/payment_data.xml',
    ],
    "installable": True
}
