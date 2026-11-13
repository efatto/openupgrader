"""Batched purge of the mail and attachment data of the removed models.

Odoo 18 ``IrModel.unlink`` deletes every ``mail_message``, ``mail_followers``,
``mail_activity`` and ``ir_attachment`` row of the models of an uninstalled
module in one unbounded statement inside the uninstall transaction: on a
production database that runs for hours, the driver RPC times out and the work
is rolled back. Deleting the rows here in committed batches keeps the progress.

Deleting the rows with SQL bypasses ``ir.attachment._file_delete``, so the
``store_fname`` of each deleted attachment is written to the instance's
filestore checklist exactly like ``ir.attachment._mark_for_gc``; the garbage
collector then removes the files.

The module has no Odoo import: the migration model calls it, and it can also be
run standalone on the migrated database, e.g. to finish a purge without
restarting the whole step:

    python purge.py -d <db>_migrate --modules old_module \\
        --checklist-dir <folder>/data_dir/filestore/<db>_migrate/checklist

``--models`` purges the data of explicit model names, ``--dry-run`` only prints
what would be deleted, ``--no-index`` skips the temporary foreign key indexes
(the slow path, kept for comparison). ``PGHOST``/``PGPORT``/``PGUSER``/
``PGPASSWORD`` are read from the environment when not passed on the command
line; ``PGOPTIONS`` can carry PostgreSQL settings (used by the benchmark).
"""

import argparse
import logging
import os
import re
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

# Rows deleted (and committed) per batch, and the number of batches after which
# the purge stops instead of looping (rows inserted during the purge?).
MAIL_PURGE_BATCH_SIZE = 5000
MAIL_PURGE_MAX_BATCHES = 10000
ATTACHMENT_PURGE_BATCH_SIZE = 5000
ATTACHMENT_PURGE_MAX_BATCHES = 10000

# Tables purged in batches: the temporary indexes of purge_fk_indexes cover the
# foreign keys referencing them.
PURGE_TABLES = ("mail_activity", "mail_followers", "mail_message", "ir_attachment")

# Models of mail: Odoo skips their mail data on unlink, purging them would
# delete the mail data of every model.
MAIL_MODELS_NOT_PURGED = frozenset(
    ("mail.activity", "mail.activity.type", "mail.followers", "mail.message")
)


def connection_variables(host=None, port=None, user=None, password=None):
    """Return the shell prefix exporting the PostgreSQL connection settings."""
    values = {
        "PGHOST": host or os.environ.get("PGHOST", ""),
        "PGPORT": port or os.environ.get("PGPORT", ""),
        "PGUSER": user or os.environ.get("PGUSER", ""),
        "PGPASSWORD": password or os.environ.get("PGPASSWORD", ""),
    }
    return " && ".join(f"export {key}={value}" for key, value in values.items()) + " "


def _sql_quote(value):
    """Quote a value for a single quoted SQL literal."""
    return "'%s'" % str(value).replace("'", "''")


def _psql(db, sql, conn):
    """Run ``sql`` on ``db`` and return its output (None on error)."""
    # the identifiers of format('%I') may need double quotes: escape them so
    # the statement survives the double quoted shell string
    escaped = (
        str(sql)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    result = subprocess.run(
        [f'{conn} && psql -v ON_ERROR_STOP=1 -d {db} -t -A -c "{escaped}"'],
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if result.returncode:
        logger.warning(
            "Unable to run %r on %s: %s",
            sql,
            db,
            (result.stderr or result.stdout or "").strip(),
        )
        return None
    return result.stdout or ""


def count_rows(db, sql, conn):
    """Rows matching ``sql``; 0 (no percentage in the log) on failure."""
    try:
        return int((_psql(db, sql, conn) or "").strip().splitlines()[-1])
    except (IndexError, ValueError):
        return 0


def get_module_model_names(db, module_names, conn):
    """Return the models that Odoo removes with ``module_names``.

    Mirrors ``ir_model.data._module_data_uninstall``: models still referenced
    by another module are kept by Odoo (and skipped here).
    """
    module_names = sorted(
        name
        for name in set(module_names or [])
        if re.fullmatch(r"[a-zA-Z0-9_.\-]+", name or "")
    )
    if not module_names:
        return []
    in_clause = ",".join(_sql_quote(name) for name in module_names)
    sql = (
        "SELECT DISTINCT im.model FROM ir_model_data d "
        "JOIN ir_model im ON im.id = d.res_id "
        f"WHERE d.model = 'ir.model' AND d.module IN ({in_clause}) "
        "AND NOT EXISTS ("
        "SELECT 1 FROM ir_model_data o "
        "WHERE o.model = 'ir.model' AND o.res_id = d.res_id "
        f"AND o.module NOT IN ({in_clause}) "
        "AND o.module <> '__cloc_exclude__')"
        ";"
    )
    result = _psql(db, sql, conn)
    if result is None:
        logger.error(
            "Unable to list the models of the modules to uninstall %s", module_names
        )
        return []
    model_names = [line.strip() for line in result.splitlines() if line.strip()]
    # the names are embedded in a psql command line: never trust the table
    invalid_names = [
        name
        for name in model_names
        if not re.fullmatch(r"[a-zA-Z0-9_.]+", name) or name in MAIL_MODELS_NOT_PURGED
    ]
    if invalid_names:
        logger.error(
            "Not purging the data of the models %s (invalid name or model of "
            "the mail module itself).",
            invalid_names,
        )
        model_names = [name for name in model_names if name not in invalid_names]
    return model_names


def fk_index_statements(db, conn, drop=False):
    """Return the statements that (re)build or drop the temporary FK indexes.

    Deleting a row makes PostgreSQL scan every referencing table without an
    index on its FK column once per deleted row (``ON DELETE SET NULL`` and
    ``CASCADE``): the attachment purge then takes hours on a big
    ``account_move``. The indexes are temporary and named
    ``openupgrader_fk_<constraint oid>``.
    """
    if drop:
        sql = (
            "SELECT format('DROP INDEX IF EXISTS %I.%I;', n.nspname, c.relname) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'i' AND c.relname ~ '^openupgrader_fk_[0-9]+$'"
        )
    else:
        names = ", ".join(_sql_quote(table) for table in PURGE_TABLES)
        sql = (
            "SELECT format('CREATE INDEX IF NOT EXISTS %I ON %s (%I);', "
            "'openupgrader_fk_' || c.oid, c.conrelid::regclass, a.attname) "
            "FROM pg_constraint c JOIN pg_attribute a "
            "ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
            "WHERE c.contype = 'f' AND cardinality(c.conkey) = 1 "
            "AND c.confrelid IN (SELECT oid FROM pg_class "
            f"WHERE relname IN ({names})) "
            "AND NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class ic "
            "ON ic.oid = i.indexrelid WHERE i.indrelid = c.conrelid "
            "AND i.indisvalid AND i.indisready "
            "AND ic.relam = (SELECT oid FROM pg_am WHERE amname = 'btree') "
            "AND (i.indkey::int2[])[0:0] = c.conkey "
            "AND (i.indpred IS NULL OR pg_get_expr(i.indpred, i.indrelid) "
            "= format('(%I IS NOT NULL)', a.attname)))"
        )
    commands = ("DROP INDEX", "CREATE INDEX")
    return [
        line.strip()
        for line in (_psql(db, sql, conn) or "").splitlines()
        if line.strip().startswith(commands)
    ]


def purge_fk_indexes(db, conn, drop=False):
    """Create or drop the temporary indexes of the references to the purged tables."""
    statements = fk_index_statements(db, conn, drop=drop)
    if not statements:
        return
    started = time.monotonic()
    prefix = "SET lock_timeout = '60s'; "
    done = sum(
        1 for statement in statements if _psql(db, prefix + statement, conn) is not None
    )
    log = logger.info if done == len(statements) else logger.warning
    log(
        "%s %s/%s temporary foreign key index(es) of the purged tables in %ss.",
        "Dropped" if drop else "Built",
        done,
        len(statements),
        round(time.monotonic() - started),
    )


def log_progress(table, label, deleted, total, started):
    """Log the percentage and the estimated time left of a batched purge."""
    elapsed = max(time.monotonic() - started, 1)
    percent = min(round(100.0 * deleted / total), 100) if total else "?"
    eta = (
        round(max(total - deleted, 0) * elapsed / deleted / 60)
        if total and deleted
        else "?"
    )
    logger.info(
        "Purged %s/%s %s row(s) for modules %s: %s%% done, ~%sm left.",
        deleted,
        total or "?",
        table,
        label,
        percent,
        eta,
    )


def mark_attachments_for_gc(checklist_dir, store_fnames):
    """Add ``store_fnames`` to the instance filestore checklist.

    Mirrors ``ir.attachment._mark_for_gc``: the file is removed later by the
    garbage collector, which also checks that no other attachment references
    it.
    """
    checklist_dir = os.path.abspath(checklist_dir)
    for store_fname in store_fnames:
        # same normalization as ir.attachment._mark_for_gc
        fname = re.sub(r"[.]", "", store_fname).strip("/\\")
        if not fname:
            continue
        full_path = os.path.abspath(os.path.join(checklist_dir, fname))
        if os.path.commonpath([checklist_dir, full_path]) != checklist_dir:
            logger.warning(
                "Not marking attachment file %s for garbage collection: invalid "
                "store name.",
                store_fname,
            )
            continue
        try:
            dirname = os.path.dirname(full_path)
            if not os.path.isdir(dirname):
                os.makedirs(dirname, exist_ok=True)
            with open(full_path, "ab"):
                pass
        except OSError:
            logger.warning(
                "Unable to mark attachment file %s for garbage collection.",
                full_path,
                exc_info=True,
            )


def purge_attachments(
    db,
    model_names,
    conn,
    checklist_dir,
    batch_size=ATTACHMENT_PURGE_BATCH_SIZE,
    max_batches=ATTACHMENT_PURGE_MAX_BATCHES,
):
    """Delete the ``ir_attachment`` rows of ``model_names`` in batches.

    :return: the number of rows deleted.
    """
    if not model_names:
        return 0
    models = ",".join(_sql_quote(name) for name in model_names)
    logger.info(
        "Purging the attachments of the models %s (%s models, %s rows per " "commit).",
        model_names,
        len(model_names),
        batch_size,
    )
    total_rows = count_rows(
        db, f"SELECT count(*) FROM ir_attachment WHERE res_model IN ({models})", conn
    )
    started = time.monotonic()
    deleted_rows = 0
    failed = False
    batches = 0
    while True:
        batches += 1
        if batches > max_batches:
            logger.error(
                "Stopped purging ir_attachment for models %s after %s batches: "
                "rows are still being inserted?",
                model_names,
                batches,
            )
            failed = True
            break
        sql = (
            "SET lock_timeout = '60s'; "
            "WITH batch AS ("
            "SELECT id, store_fname FROM ir_attachment "
            f"WHERE res_model IN ({models}) LIMIT {batch_size}), "
            "deleted AS ("
            "DELETE FROM ir_attachment a USING batch b "
            "WHERE a.id = b.id RETURNING b.store_fname) "
            "SELECT count(*), coalesce(string_agg(store_fname, ','), '') "
            "FROM deleted;"
        )
        output = (_psql(db, sql, conn) or "").strip().splitlines()
        try:
            deleted, store_fnames = output[-1].split("|", 1)
            deleted = int(deleted)
        except (IndexError, ValueError):
            logger.error(
                "Unable to purge ir_attachment for models %s: %s. The "
                "uninstallation will delete the remaining rows in one statement.",
                model_names,
                (output or [""])[-1],
            )
            failed = True
            break
        if not deleted:
            break
        deleted_rows += deleted
        mark_attachments_for_gc(
            checklist_dir, [fname for fname in store_fnames.split(",") if fname]
        )
        log_progress("ir_attachment", model_names, deleted_rows, total_rows, started)
    logger.info(
        "Attachment purge for models %s done: %s row(s) deleted%s.",
        model_names,
        deleted_rows,
        " (incomplete, see the previous errors)" if failed else "",
    )
    return deleted_rows


def purge_data(
    db,
    model_names,
    conn,
    checklist_dir,
    label=None,
    create_indexes=True,
    batch_size=ATTACHMENT_PURGE_BATCH_SIZE,
):
    """Purge the mail and attachment data of ``model_names`` in batches.

    :param label: module names for the log (defaults to ``model_names``)
    :param create_indexes: build (and drop) the temporary FK indexes first
    :return: the number of mail rows deleted
    """
    label = label or model_names
    if not model_names:
        return 0
    models = ",".join(_sql_quote(name) for name in model_names)
    # (table, count statement, batched delete statement): the count is the
    # estimate the progress log compares the deleted rows with
    statements = [
        (
            "mail_activity",
            "SELECT count(*) FROM mail_activity WHERE res_model_id IN "
            f"(SELECT id FROM ir_model WHERE model IN ({models}))",
            "DELETE FROM mail_activity WHERE id IN ("
            "SELECT id FROM mail_activity WHERE res_model_id IN "
            f"(SELECT id FROM ir_model WHERE model IN ({models})) "
            f"LIMIT {MAIL_PURGE_BATCH_SIZE})",
        ),
        (
            "mail_followers",
            f"SELECT count(*) FROM mail_followers WHERE res_model IN ({models})",
            "DELETE FROM mail_followers WHERE id IN ("
            f"SELECT id FROM mail_followers WHERE res_model IN ({models}) "
            f"LIMIT {MAIL_PURGE_BATCH_SIZE})",
        ),
        (
            "mail_message",
            f"SELECT count(*) FROM mail_message WHERE model IN ({models})",
            "DELETE FROM mail_message WHERE id IN ("
            f"SELECT id FROM mail_message WHERE model IN ({models}) "
            f"LIMIT {MAIL_PURGE_BATCH_SIZE})",
        ),
    ]
    logger.info(
        "Purging the mail data of the models of the modules to uninstall %s (%s "
        "models, %s rows per commit).",
        label,
        len(model_names),
        MAIL_PURGE_BATCH_SIZE,
    )
    if create_indexes:
        # indexes left behind by an interrupted purge must not stay behind
        purge_fk_indexes(db, conn, drop=True)
        purge_fk_indexes(db, conn)
    try:
        deleted_rows = 0
        failed = False
        for table, count_statement, statement in statements:
            # an estimate taken once per table: the percentage and the
            # remaining time assume the rows go away at the average rate
            total_rows = count_rows(db, count_statement, conn)
            started = time.monotonic()
            table_deleted_rows = 0
            batches = 0
            while True:
                batches += 1
                if batches > MAIL_PURGE_MAX_BATCHES:
                    logger.error(
                        "Stopped purging %s for modules %s after %s batches: "
                        "rows are still being inserted?",
                        table,
                        label,
                        batches,
                    )
                    failed = True
                    break
                sql = (
                    "SET lock_timeout = '60s'; "
                    f"WITH d AS ({statement} RETURNING 1) SELECT count(*) FROM d;"
                )
                result = _psql(db, sql, conn)
                try:
                    deleted = int((result or "").strip().splitlines()[-1])
                except (IndexError, ValueError):
                    logger.error(
                        "Unable to purge %s for modules %s: %s. The "
                        "uninstallation will delete the remaining rows in one "
                        "statement.",
                        table,
                        label,
                        (result or "").strip(),
                    )
                    failed = True
                    break
                if not deleted:
                    break
                deleted_rows += deleted
                table_deleted_rows += deleted
                log_progress(table, label, table_deleted_rows, total_rows, started)
        logger.info(
            "Mail data purge for modules %s done: %s row(s) deleted%s.",
            label,
            deleted_rows,
            " (incomplete, see the previous errors)" if failed else "",
        )
        # same order as IrModel.unlink: the mail rows (and their attachment
        # relations) go first, then the attachment rows
        purge_attachments(
            db,
            model_names,
            conn,
            checklist_dir,
            batch_size=batch_size,
        )
        return deleted_rows
    finally:
        if create_indexes:
            purge_fk_indexes(db, conn, drop=True)


def main(argv=None):
    """Command line entry point (see the module docstring)."""
    parser = argparse.ArgumentParser(
        description="Purge the mail and attachment data of removed models."
    )
    parser.add_argument("-d", "--db", required=True, help="migrated database name")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--modules", help="comma separated module names")
    target.add_argument("--models", help="comma separated model names")
    parser.add_argument(
        "--checklist-dir",
        help="filestore checklist directory of the migrated database",
    )
    parser.add_argument("--host", help="default: $PGHOST")
    parser.add_argument("--port", help="default: $PGPORT")
    parser.add_argument("--user", help="default: $PGUSER")
    parser.add_argument("--password", help="default: $PGPASSWORD")
    parser.add_argument("--batch-size", type=int, default=ATTACHMENT_PURGE_BATCH_SIZE)
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="do not build the temporary foreign key indexes",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="only print what would be purged"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    conn = connection_variables(args.host, args.port, args.user, args.password)
    if args.modules:
        model_names = get_module_model_names(args.db, args.modules.split(","), conn)
        label = args.modules.split(",")
    else:
        model_names = [name for name in args.models.split(",") if name]
        label = model_names
    if not model_names:
        logger.info("Nothing to purge.")
        return 0
    models = ",".join(_sql_quote(name) for name in model_names)
    if args.dry_run:
        for table, where in (
            (
                "mail_activity",
                f"res_model_id IN (SELECT id FROM ir_model WHERE model IN ({models}))",
            ),
            ("mail_followers", f"res_model IN ({models})"),
            ("mail_message", f"model IN ({models})"),
            ("ir_attachment", f"res_model IN ({models})"),
        ):
            logger.info(
                "%s: %s row(s)",
                table,
                count_rows(
                    args.db, f"SELECT count(*) FROM {table} WHERE {where}", conn
                ),
            )
        if not args.no_index:
            for statement in fk_index_statements(args.db, conn):
                logger.info("would run: %s", statement)
        return 0
    try:
        purge_data(
            args.db,
            model_names,
            conn,
            args.checklist_dir or "",
            label=label,
            create_indexes=not args.no_index,
            batch_size=args.batch_size,
        )
    except KeyboardInterrupt:
        logger.warning("Interrupted: the committed batches are kept.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
