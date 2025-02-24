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
    if not os.path.isdir(os.path.join(os.path.expanduser("~"), ".pyenv")):
        subprocess.Popen(["curl -fsSL https://pyenv.run | bash"], shell=True).wait()
    # Load pyenv automatically by appending the following text
    # for file in [".bash_profile", ".profile", ".bashrc"]:
    #     file_path = os.path.join(os.path.expanduser("~"), file)
    #     if not os.path.isfile(file_path):
    #         with open(file_path, "w") as f:
    #             f.write('export PYENV_ROOT="$HOME/.pyenv"\n')
    #             f.write(
    #                 '[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH\n')
    #             f.write('eval "$(pyenv init - bash)\n')
    # Restart your shell for the changes to take effect.
    # Load pyenv-virtualenv automatically by adding
    # the following to ~/.bashrc:
    # file_path = os.path.join(os.path.expanduser("~"), ".bashrc")
    subprocess.Popen(
        ['echo \'export PYENV_ROOT="$HOME/.pyenv"\' >> ~/.bashrc'],
        shell=True).wait()
    # eval "$(pyenv virtualenv-init -)"

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
