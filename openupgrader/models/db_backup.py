import json
import os
import shutil
import tempfile
import logging
from datetime import datetime
from odoo.modules import get_module_resource
from odoo.sql_db import db_connect
from odoo.tools import config, exec_pg_command
from odoo.tools.osutil import zip_dir
from odoo import fields, models, _
logger = logging.getLogger(__name__)


class DbBackup(models.Model):
    _inherit = 'db.backup'

    is_migration_backup = fields.Boolean(string='Is Migration Backup?')
    odoo_version_id = fields.Many2one(
        comodel_name="odoo.version",
        string="Odoo version",
    )

    @staticmethod
    def dump_db_manifest(cr, version_name, dbname):
        pg_version = "%d.%d" % divmod(cr._obj.connection.server_version / 100, 100)
        cr.execute(
            "SELECT name, latest_version FROM ir_module_module WHERE state = 'installed'"
        )
        modules = dict(cr.fetchall())
        manifest = {
            "odoo_dump": "1",
            "db_name": dbname,
            "version": version_name,
            "version_info": (version_name.split(".")[0], 0, 0, "", 0, ""),
            "major_version": version_name,
            "pg_version": pg_version,
            "modules": modules,
        }
        return manifest

    def action_backup_migration(self):
        """Run selected backups."""
        backup = None
        successful = self.browse()

        # Start with local storage
        version_name = self.odoo_version_id.name
        for rec in self.filtered(lambda r: r.method == "local"):
            filename = (
                f"{self.env.cr.dbname}_migrate.{version_name}."
                f"{self.filename(datetime.now(), ext=rec.backup_format)}")
            with rec.backup_log():
                # Directory must exist
                try:
                    os.makedirs(rec.folder)
                except OSError:
                    pass
                with open(os.path.join(rec.folder, filename), "wb") as destiny:
                    # Always generate new backup
                    rec.dump_db_migration(
                        db_name=f"{self.env.cr.dbname}_migrate",
                        stream=destiny,
                        backup_format=rec.backup_format,
                    )
                    backup = destiny.name
                successful |= rec

        # Ensure a local backup exists if we are going to write it remotely
        sftp = self.filtered(lambda r: r.method == "sftp")
        if sftp:
            for rec in sftp:
                filename = (
                    f"{self.env.cr.dbname}_migrate.{version_name}."
                    f"{self.filename(datetime.now(), ext=rec.backup_format)}")
                with rec.backup_log():
                    cached = rec.dump_db_migration(
                        db_name=f"{self.env.cr.dbname}_migrate",
                        stream=None,
                        backup_format=rec.backup_format,
                    )

                    with cached:
                        with rec.sftp_connection() as remote:
                            # Directory must exist
                            try:
                                remote.makedirs(rec.folder)
                            except pysftp.ConnectionException:
                                pass

                            # Copy cached backup to remote server
                            with remote.open(
                                os.path.join(rec.folder, filename), "wb"
                            ) as destiny:
                                shutil.copyfileobj(cached, destiny)
                        successful |= rec

        # Remove old files for successful backups
        successful.cleanup()

    def dump_db_migration(self, db_name, stream, backup_format="zip"):
        version_name = self.odoo_version_id.name
        cmd = ["pg_dump", "--no-owner"]
        cmd.append(db_name)
        openupgrader_migration_id = self.env["openupgrader.migration"].search([])
        openupgrader_migration_id.ensure_one()
        filestore = openupgrader_migration_id.get_filestore_path(
            version_name=version_name, migration_folder=True)
        with tempfile.TemporaryDirectory() as dump_dir:
            if os.path.exists(filestore):
                path = shutil.copytree(filestore, os.path.join(dump_dir, "filestore"))
            with open(os.path.join(dump_dir, "manifest.json"), "w") as fh:
                db = db_connect(db_name)
                with db.cursor() as cr:
                    json.dump(
                        self.dump_db_manifest(cr, version_name, db_name), fh, indent=4
                    )
            cmd.insert(-1, "--file=" + os.path.join(dump_dir, "dump.sql"))
            exec_pg_command(*cmd)
            if stream:
                zip_dir(
                    dump_dir,
                    stream,
                    include_dir=False,
                    fnct_sort=lambda file_name: file_name != "dump.sql",
                )
            else:
                t = tempfile.TemporaryFile()
                zip_dir(
                    dump_dir,
                    t,
                    include_dir=False,
                    fnct_sort=lambda file_name: file_name != "dump.sql",
                )
                t.seek(0)
                return t
