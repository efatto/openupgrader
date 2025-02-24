import os
import subprocess


def _get_env_for_subprocess(folder, py_version):
    env_for_subprocess = os.environ.copy()
    env_for_subprocess["VIRTUAL_ENV"] = folder
    env_for_subprocess["PYTHONPATH"] = folder
    env_for_subprocess["PATH"] = ":".join(
        [
            folder,
            os.path.join(folder, "bin"),
            "/bin/pip",
            "/usr/bin",
        ]
    )
    env_for_subprocess["PWD"] = folder
    python_root = os.path.join(
        folder, "lib", f"python{'.'.join(py_version.split('.')[:2])}"
    )
    if os.path.isdir(python_root):
        env_for_subprocess["LIBRARY_ROOTS"] = python_root
    return env_for_subprocess


def _create_python_venv(venv_path, py_version):
    # create virtualenv
    subprocess_env = _get_env_for_subprocess(venv_path, py_version)
    if not os.path.isdir(venv_path):
        subprocess.Popen([f"mkdir -p {venv_path}"], shell=True).wait()
        # do not recreate virtualenv as it regenerate file with bug in split()
    subprocess.Popen([f"pyenv install -s {py_version}"], shell=True).wait()
    pyenv_path = os.path.join(
        os.path.expanduser("~"),
        ".pyenv",
        "versions",
        py_version,
    )
    subprocess.Popen(
        [f"{pyenv_path}/bin/pip install virtualenv"],
        cwd=venv_path,
        env=subprocess_env,
        shell=True,
    ).wait()
    subprocess.Popen(
        [f"{pyenv_path}/bin/virtualenv -p {pyenv_path}/bin/python {venv_path}"],
        cwd=venv_path,
        env=subprocess_env,
        shell=True,
    ).wait()
    return subprocess_env
