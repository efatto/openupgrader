This page is the practical companion to the Configuration section: it
describes the data model, the normal UI procedure and a headless procedure
suitable for repeated test runs.

Concepts
~~~~~~~~

* one ``openupgrader.migration`` record per database to migrate;
* one ``openupgrader.config`` record **per Odoo version** involved
  (e.g. ``14.0``, ``15.0``, ``16.0``, ``17.0``, ``18.0`` for a 14 -> 18
  migration);
* every config owns one virtualenv under
  ``<migration.folder>/openupgrade<version>/`` together with its logs and,
  optionally, the dump ``database.<version>.dump``;
* the progress is stored both in the database (``state`` field) and in
  ``migration_state.json`` (per-version ``state``, ``env_state`` and retry
  counters).  The file lives in ``~/odoo_migration/<database>/`` unless
  ``folder`` is configured (see the P3 note about
  ``_default_migration_state_path``);
* the cron *OpenUpgrader: do auto migration* drives every transition; the
  ``Auto migration`` button simply activates it.

The migration record goes through::

    draft -> starting -> auto
          -> (prepare, migrate) x N versions
          -> done

The per-version state stored in the JSON file goes through::

    starting -> started -> restored -> updated
             -> ready_for_migration -> migrating -> migrated
             -> ready_to_prepare -> ... -> done

Standard procedure (UI)
~~~~~~~~~~~~~~~~~~~~~~~

1. Install ``openupgrader`` on the **source** database (14.0 in this
   example) and open *Settings -> OpenUpgrader -> OpenUpgrader Migration*.
2. Create the migration, fill database/PostgreSQL connection data and
   ``folder``, then import the YML configuration (or create the version
   configs by hand).
3. Create one config per version and press ``Create virtualenv`` on each of
   them (or ``Create all envs`` on the migration).
4. Press ``Auto migration``.
5. When the state is ``done``, restore the final dump
   ``database.<target>.dump`` on the target server.

The manual alternative, for a single version, is:
``Restore`` -> ``Update`` -> ``Prepare migration`` -> ``Migrate``, repeated
for each intermediate version.  ``Restore last dump of database`` rewinds to
the last good per-version dump (requires *Dump each database version*).

Headless procedure (recommended for tests)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The module ships a small driver kit in ``tests/migration_kit/``.  It uses
``odoo-bin shell`` and only calls the module's own methods, so it exercises
exactly the same code as the UI and the cron.

Environment::

    export OPENUPGRADER_DB=my_db14                 # driver (source) database
    export OPENUPGRADER_CONF=/etc/odoo/odoo.conf
    export OPENUPGRADER_ODOO_BIN=/opt/odoo14/odoo-bin
    export OPENUPGRADER_PY=/opt/odoo14-venv/bin/python
    export OPENUPGRADER_FOLDER=/srv/migrations/my_db14
    export OPENUPGRADER_CONFIG_YML=/srv/migrations/my_db14.yml
    export OPENUPGRADER_FROM=14.0
    export OPENUPGRADER_TO=18.0
    # optional, when the DB is remote
    export OPENUPGRADER_PG_HOST=db
    export OPENUPGRADER_PG_USER=odoo
    export OPENUPGRADER_PG_PASSWORD_VAR='$POSTGRES_PASSWORD'

Commands::

    tests/migration_kit/run_migration.sh setup    # migration + version configs from YML
    tests/migration_kit/run_migration.sh envs     # build the virtualenvs (once)
    tests/migration_kit/run_migration.sh drive    # run auto migration until done/failed
    tests/migration_kit/run_migration.sh finish   # let the cron finish pending modules
    tests/migration_kit/run_migration.sh status   # state, retries, log counters
    tests/migration_kit/run_migration.sh verify   # base version, modules, CRITICALs

Useful variations::

    # rebuild only one environment
    OPENUPGRADER_VERSIONS=17.0 OPENUPGRADER_FORCE_ENV=1 \
        tests/migration_kit/run_migration.sh envs

    # edit the YML on disk and reload it without recreating the migration
    OPENUPGRADER_CONFIG_YML=/srv/migrations/my_db14.yml \
        tests/migration_kit/run_migration.sh reload

    # rewind to the last per-version dump
    tests/migration_kit/run_migration.sh restore

    # open a shell on the driver DB
    tests/migration_kit/run_migration.sh shell

YML configuration reference
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The file is a mapping ``version -> list of recipe dicts``.  A recipe can
contain::

    "18.0":
      - python_version: "3.12.11"
      - pip_requirements:            # pip packages for the venv
          - odoorpc
          - openupgradelib@git+https://github.com/OCA/openupgradelib.git@master#egg=openupgradelib
      - odoo_pip_requirements:       # Odoo addons installed from pip
          - module_change_auto_install
      - odoo: "https://github.com/OCA/OCB.git 18.0"   # "<url> <branch>"
      - after_migration_to_this_version_sql_command:
          - UPDATE ...
      - before_migration_to_next_version_sql_command:
          - DELETE ...
      - after_migration_to_this_version_python_command: []
      - before_migration_to_next_version_python_command: []
      - renamed_modules:  ["old_name new_name"]      # one rename per line
      - auto_install:     ["installed_mod new_mod"]  # install new_mod
      - delete:           ["module_to_drop"]
      - obsolete:         ["module_no_longer_exists"]
      - not_autoinstallable_modules: ["iap", "mail_bot"]
      - uninstall_after_migration_to_this_version:  ["mod"]
      - uninstall_before_migration_to_next_version: ["mod"]

Notes:

* Odoo addon wheels declare their same-repository module dependencies as
  ``odoo*-addon-*`` requirements. They are installed by pip/uv together with
  the requested module and are persisted in the project, so a later
  ``uv sync`` does not remove them;
* ``renamed_modules``, ``auto_install``, ``delete`` and ``obsolete`` are
  merged with the ``apriori.py`` of OpenUpgrade when the version checkout is
  available;
* ``obsolete`` modules are uninstalled as part of the post-migration
  cleanup, so listing a module there is usually enough;
* SQL/python commands run through the module's own mechanisms, which makes
  data workarounds reproducible and keeps them out of the migration code;
* the YML is loaded when a config is created; use
  ``config_file_path`` + ``button_reload_config_file`` to reload after an
  edit (the path takes precedence over the uploaded file).

Making repeated tests fast
~~~~~~~~~~~~~~~~~~~~~~~~~~

#. **Build each virtualenv once.**  ``button_create_env`` returns immediately
   when ``env_state`` is ``updated``.  A full 14 -> 18 test needs five
   environments: keep the ``folder`` between runs and only rebuild the
   version whose configuration really changed
   (``OPENUPGRADER_FORCE_ENV=1 OPENUPGRADER_VERSIONS=17.0``).
#. **Install addons in one batch.**  Configure the private index through
   ``PIP_EXTRA_INDEX_URL``/``UV_INDEX`` so that
   ``_install_modules_batch`` resolves all modules in a single
   ``uv pip install`` instead of one call per module.
#. **Dump each version.**  Keep ``dump_each_version_database`` enabled: a
   broken step can be resumed with ``button_restore_last_database()``
   instead of replaying 14 -> 15 -> 16 again.
#. **Reload the configuration, do not recreate it.**
   ``mig.config_file_path = "/path/config.yml"`` then
   ``mig.button_reload_config_file()`` (or the *Reload config file* button)
   re-applies the YML to every config.
#. **Let the cron retry.**  A failed step is retried up to
   ``MAX_MIGRATION_RETRY`` (3) times, cleaning the step logs before each
   retry.  After the limit the migration is marked ``failed``: fix the
   cause, reset the counter and continue, instead of restarting from 14.0.
#. **Suppress noisy auto-installs.**  Put modules that break the automatic
   installation in ``not_autoinstallable_modules`` for the target version.
#. **Keep logs small.**  The shell driver uses ``--log-level=warn``; the
   module still writes ``migrate.log``/``update.log`` in
   ``<folder>/openupgrade<version>/``, which can be inspected with
   ``status``.

Recovery cheat sheet
~~~~~~~~~~~~~~~~~~~~

``mig = env["openupgrader.migration"].search([], limit=1)``

* Odoo process left running / state stuck:
  ``mig.button_stop_odoo()``.
* Stale logs re-mark a step as failed:
  ``mig.button_clean_logs(config)``.
* Broken or incomplete virtualenv:
  ``config.button_recreate_env()``.
* Resume from the last good per-version dump:
  ``mig.button_restore_last_database()``.
* Retry limit reached (``state == failed``): fix the cause, then
  ``config.reset_migration_retry_number()``,
  ``config.update_migration_state_file("ready_for_migration")`` and
  ``mig.button_continue_auto_migration()``.
* YML edited on disk:
  ``mig.config_file_path = path; mig.button_reload_config_file()``.
* State file inconsistent: ``mig.init_migration_state_file()``.

Handy methods (all callable from ``odoo-bin shell``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``mig.button_do_auto_migration()``
    start the cron-driven migration;
``mig.button_continue_auto_migration()``
    re-activate the cron after a fix;
``mig.button_do_migration()``
    run one migration step (used by the cron);
``mig.button_prepare_for_migration()``
    run the pre-migration SQL/python fixes, uninstalls and cleanup;
``mig.button_dump_current_database()``
    dump the migrated DB for the current version;
``mig.button_restore_last_database()``
    restore the last per-version dump;
``config.button_create_env()`` / ``config.button_recreate_env()``
    create / rebuild the version virtualenv;
``config.action_load_config()``
    re-apply the YML recipe to an existing config.
