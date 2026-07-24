import logging
import os
import shutil
import signal
import ssl
import sys
import time
from pathlib import Path
from subprocess import PIPE, Popen
from urllib.request import HTTPSHandler

from odoorpc.rpc import CookieJar, HTTPCookieProcessor, build_opener

from odoo.modules import get_module_resource

logger = logging.getLogger(__name__)


def _get_opener(verify_ssl=True, sessions=True):
    handlers = []
    if not verify_ssl:
        if (sys.version_info[0] == 2 and sys.version_info >= (2, 7, 9)) or (
            sys.version_info[0] == 3 and sys.version_info >= (3, 2, 0)
        ):
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            handlers.append(HTTPSHandler(context=context))
        else:
            logger.info(
                "verify_ssl could not be established for this "
                "python version: %s" % sys.version
            )
    if sessions:
        handlers.append(HTTPCookieProcessor(CookieJar()))
    opener = build_opener(*handlers)
    return opener


def _set_odoorc(folder, config_id):
    odoorc_path = os.path.join(folder, ".odoorc")
    if not os.path.isfile(odoorc_path):
        odoorc_basic_path = get_module_resource("openupgrader", "data", ".odoorc")
        shutil.copyfile(odoorc_basic_path, odoorc_path)
        if float(config_id.name) > 15:
            sed_cmd = f"sed -i 's/longpolling/gevent/g' {odoorc_path}"
            Popen(sed_cmd, shell=True)
        # todo move disabled modules to configuration
        not_auto_install_list = [
            "partner_autocomplete",
            "iap",
            "mail_bot",
            "account_edi_facturx",
            "account_edi_ubl",
            "l10n_it_stock_ddt",
        ]
        if float(config_id.name) <= 14:
            not_auto_install_list.extend(
                [
                    "account_edi",
                    "l10n_it_edi",
                ]
            )
        mod_not_install = (
            f"modules_auto_install_disabled = {','.join(not_auto_install_list)}"
        )
        Popen(
            [f"echo {mod_not_install} >> {odoorc_path}"],
            shell=True,
        ).wait()


def _get_migration_logs(log_file):
    critical_log = " "
    error_log = " "
    warning_log = " "
    if os.path.isfile(log_file):
        with open(log_file, "r") as f:
            for r in f.readlines():
                if r and r != "":
                    if "CRITICAL" in r:
                        c_text = r.split("CRITICAL")[1]
                        if c_text and c_text not in critical_log:
                            critical_log += c_text
                    if "ERROR" in r:
                        e_text = r.split("ERROR")[1]
                        if e_text and e_text not in error_log:
                            error_log += e_text
                    if "WARNING" in r:
                        w_text = r.split("WARNING")[1]
                        if w_text and w_text not in warning_log:
                            warning_log += w_text
    return critical_log, error_log, warning_log


def _get_log_path(folder, version_name, migrate=False):
    log_name = "migrate" if migrate else "update"
    return Path(folder) / f"openupgrade{version_name}" / f"{log_name}.log"


def _get_odoo_pids(dbname):
    pids = []
    db_name = dbname
    commands = [
        f"pgrep -a python | grep {db_name}_migrate",
        f"pgrep -a postgres | grep {db_name}_migrate",
    ]
    for command in commands:
        process = Popen(
            command,
            shell=True,
            stdout=PIPE,
        )
        has_stdout = True
        while has_stdout:
            one_line_output = process.stdout.readline()
            if one_line_output:
                pids.append(int(one_line_output.split()[0]))
            else:
                has_stdout = False
    return pids


def _stop_pid(pid=False):
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(5)
        except OSError:
            time.sleep(10)
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError as e:
                logger.info("Error %s in killing pid: %s " % (e, pid))
    logger.info("Odoo migration instance stopped.")
