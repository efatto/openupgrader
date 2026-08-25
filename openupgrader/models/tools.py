import json
import logging
import os
import shutil
import signal
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path
from subprocess import PIPE, Popen
from urllib.request import HTTPSHandler, Request, urlopen

import psutil
from odoorpc.rpc import CookieJar, HTTPCookieProcessor, build_opener

from odoo.modules import get_module_resource
from odoo.tools.safe_eval import safe_eval

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


def _get_odoo_process_state(dbname):
    """Check if any Odoo process is currently running."""
    odoo_pids = _get_odoo_pids(dbname)
    for odoo_pid in odoo_pids:
        if psutil.pid_exists(odoo_pid):
            return "running"
    return "stopped"


def _set_odoorc(folder, config_id):
    odoorc_path = os.path.join(folder, ".odoorc")
    odoorc_basic_path = get_module_resource("openupgrader", "data", ".odoorc")
    shutil.copyfile(odoorc_basic_path, odoorc_path)
    if float(config_id.name) > 15:
        sed_cmd = f"sed -i 's/longpolling/gevent/g' {odoorc_path}"
        Popen(sed_cmd, shell=True)
    not_auto_install_list = safe_eval(config_id.not_autoinstallable_modules)
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


def _check_oca_authorship(pkg_name, release):
    """
    Verify if the package on PyPI is owned by the 'OCA' user.
    """
    url = f"https://pypi.org/pypi/{pkg_name}/json"
    try:
        req = Request(url)
        # Use a short timeout to avoid blocking migration for too long
        with urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                ownership = data.get("ownership", {})
                roles = ownership.get("roles", [])
                releases = data.get("releases", {})
                for role_info in roles:
                    if (
                        role_info.get("user") == "OCA"
                        and role_info.get("role") == "Owner"
                        and release[:4] in [r[:4] for r in releases]
                    ):
                        return True
        logger.warning(
            "Package %s found on PyPI but not owned by OCA or not with release %s. "
            "Authorship check failed.",
            pkg_name,
            release,
        )
    except Exception as e:
        logger.error(
            "Error verifying OCA authorship for %s release %s: %s",
            pkg_name,
            release,
            e,
        )
    return False


def _init_migration_state_file(migration_state_path, config_names):
    if os.path.isfile(migration_state_path):
        with open(migration_state_path, "r") as f:
            try:
                migration_state_dict = json.load(f)
            except json.JSONDecodeError as _e:
                migration_state_dict = {}
            except Exception as _e:
                migration_state_dict = {}
        for config_name in config_names:
            if config_name not in migration_state_dict:
                migration_state_dict[config_name] = {
                    "state": None,
                    "date_started": None,
                    "date_updated": None,
                }
            else:
                migration_state_dict[config_name]["state"] = None
                migration_state_dict[config_name]["date_started"] = None
                migration_state_dict[config_name]["date_updated"] = None
    else:
        migration_state_dict = {
            config_name: {
                "state": None,
                "date_started": None,
                "date_updated": None,
            }
            for config_name in config_names
        }
    with open(migration_state_path, "w") as f:
        json.dump(migration_state_dict, f, sort_keys=True, indent=2)


def _update_migration_state_file(
    migration_state_path,
    config_name,
    state=None,
    date_started=None,
    env_state=None,
    env_update_date=None,
):
    with open(migration_state_path, "r") as f:
        try:
            migration_state_dict = json.load(f)
        except json.JSONDecodeError as _e:
            migration_state_dict = {}
        except Exception as _e:
            migration_state_dict = {}
    if migration_state_dict and migration_state_dict.get(config_name):
        if state is not None:
            migration_state_dict[config_name]["state"] = state
        if date_started is not None:
            migration_state_dict[config_name]["date_started"] = date_started
        if env_state is not None:
            migration_state_dict[config_name]["env_state"] = env_state
        if env_update_date is not None:
            migration_state_dict[config_name]["env_update_date"] = env_update_date
        migration_state_dict[config_name]["date_updated"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with open(migration_state_path, "w") as f:
            json.dump(migration_state_dict, f, sort_keys=True, indent=2)


def _get_migration_state_from_file(migration_state_path, config_names):
    """Get the migration state from the migration state file.

    :param migration_state_path: Path to the migration state file.
    :param config_names: List of config names or a single config name. When called with
    a single config name, it returns its state, env_state, and config name. When called
    with a list, it returns the latter config state, env_state, and name.
    :return: Tuple of state, env_state, and config name.
    """
    state = None
    env_state = None
    config = None
    with open(migration_state_path, "r") as f:
        try:
            migration_state_dict = json.load(f)
        except json.JSONDecodeError as _e:
            migration_state_dict = {}
        except Exception as _e:
            migration_state_dict = {}
        for config_name in sorted(config_names, reverse=True):
            if migration_state_dict.get(config_name):
                state = migration_state_dict[config_name].get("state", None)
                env_state = migration_state_dict[config_name].get("env_state", None)
                config = config_name
                if state is not None:
                    break
    return state, env_state, config
