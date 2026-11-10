"""Start the Odoo shell with the migration addons paths first.

OCA CI environments can have an ``odoo/addons`` directory in the venv
site-packages that contains (or links to) the system Odoo addons, e.g.
``/opt/odoo/addons``. Odoo's ``initialize_sys_path`` appends the configured
``--addons-path`` entries after that namespace path, so the system ``web``
would shadow the target version's ``web`` and the shell would fail while
loading the wrong Odoo version.

Move the configured addons paths to the front before starting the CLI. The
paths are passed by ``openupgrader`` in ``OPENUPGRADER_ODOO_ADDONS`` (comma
separated, same order as ``--addons-path``).
"""

import os

import odoo.addons

preferred = [
    os.path.abspath(path)
    for path in os.environ.get("OPENUPGRADER_ODOO_ADDONS", "").split(",")
    if path
]
odoo.addons.__path__[:] = preferred + [
    path for path in odoo.addons.__path__ if os.path.abspath(path) not in preferred
]

import odoo.cli  # noqa: E402

odoo.cli.main()
