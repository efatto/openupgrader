import os
import shutil
import subprocess
import configparser


def _get_env_for_subprocess(folder, py_version):
    env_for_subprocess = os.environ.copy()
    env_for_subprocess["VIRTUAL_ENV"] = folder
    env_for_subprocess["PYTHONPATH"] = folder
    # If there is a PIP_EXTRA_INDEX_URL in local env, put in the pyenv
    pip_extra_index_url = os.environ.get("PIP_EXTRA_INDEX_URL")
    if not pip_extra_index_url:
        pip_conf_path = os.path.join(os.path.expanduser("~"), ".pip", "pip.conf")
        if os.path.isfile(pip_conf_path):
            config = configparser.ConfigParser()
            config.read(pip_conf_path)
            pip_extra_index_url = config.get('global', 'extra-index-url')
    if pip_extra_index_url:
        env_for_subprocess["PIP_EXTRA_INDEX_URL"] = pip_extra_index_url
    pyenv_path = os.path.join(os.path.expanduser("~"), ".pyenv")
    env_for_subprocess["PATH"] = ":".join(
        [
            os.path.join(pyenv_path, "bin"),
            folder,
            os.path.join(folder, "bin"),
            "/bin",
            "/usr/bin",
        ]
    )
    env_for_subprocess["PWD"] = folder
    env_for_subprocess["PYENV_ROOT"] = pyenv_path
    python_root = os.path.join(
        folder, "lib", f"python{'.'.join(py_version.split('.')[:2])}"
    )
    if os.path.isdir(python_root):
        env_for_subprocess["LIBRARY_ROOTS"] = python_root
    return env_for_subprocess


def _create_python_venv(venv_path, py_version):
    subprocess_env = _get_env_for_subprocess(venv_path, py_version)
    if not os.path.isdir(venv_path):
        subprocess.Popen([f"mkdir -p {venv_path}"], shell=True).wait()
        # do not recreate virtualenv as it regenerates file with bug in split()
    if not os.path.isdir(os.path.join(os.path.expanduser("~"), ".pyenv")):
        subprocess.Popen(["curl -fsSL https://pyenv.run | bash"], shell=True).wait()
    # Copy some pip configuration files that could exist in local to the python venv
    subprocess.Popen(
        [f"pyenv install -s {py_version}"],
        cwd=venv_path,
        env=subprocess_env,
        shell=True,
    ).wait()
    pyenv_path = os.path.join(
        os.path.expanduser("~"),
        ".pyenv",
        "versions",
        py_version,
    )
    subprocess.Popen(
        [f"{pyenv_path}/bin/python -m venv {venv_path}"],
        cwd=venv_path,
        env=subprocess_env,
        shell=True,
    ).wait()
    return subprocess_env
