import functools
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote

from odoo.tests.common import SavepointCase, tagged

PACKAGE_NAME = "odoo-addon-sale_timesheet_invoice_description"
PACKAGE_MODULE = PACKAGE_NAME.replace("-", "_")
VERSION_SPEC = "17.0.*"
PYPI_INDEX_URL = "https://pypi.org/simple"


def _normalize_index_name(name):
    """Normalize a project name as required by PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


class PackageIndexHandler(BaseHTTPRequestHandler):
    """Serve a minimal PEP 503 index and the wheels it links to."""

    def __init__(self, *args, **kwargs):
        self.index_root = kwargs.pop("index_root")
        super().__init__(*args, **kwargs)

    def _get_file_path(self):
        path = unquote(self.path.split("?", 1)[0].split("#", 1)[0])
        if path.endswith("/"):
            path += "index.html"
        root = os.path.abspath(self.index_root)
        file_path = os.path.abspath(os.path.join(root, path.lstrip("/")))
        if file_path != root and not file_path.startswith(root + os.sep):
            return None
        return file_path

    def _serve(self, send_body):
        file_path = self._get_file_path()
        if not file_path or not os.path.isfile(file_path):
            self.send_error(404)
            return
        with open(file_path, "rb") as file_obj:
            content = file_obj.read()
        content_type = (
            "text/html" if file_path.endswith(".html") else "application/octet-stream"
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if send_body:
            self.wfile.write(content)

    def do_GET(self):
        self._serve(send_body=True)

    def do_HEAD(self):
        self._serve(send_body=False)

    def log_message(self, fmt, *args):
        pass


def _build_wheel(index_root, version, marker):
    """Create a wheel and a simple index page for it under ``index_root``."""
    package_dir = index_root / "packages"
    package_dir.mkdir(parents=True, exist_ok=True)
    wheel_filename = "%s-%s-py3-none-any.whl" % (PACKAGE_MODULE, version)
    dist_info = "%s-%s.dist-info" % (PACKAGE_MODULE, version)
    with zipfile.ZipFile(
        str(package_dir / wheel_filename), "w", zipfile.ZIP_DEFLATED
    ) as wheel:
        wheel.writestr(
            "%s/__init__.py" % PACKAGE_MODULE,
            "SOURCE = %r\nVERSION = %r\n" % (marker, version),
        )
        wheel.writestr(
            "%s/METADATA" % dist_info,
            "Metadata-Version: 2.1\nName: %s\nVersion: %s\n"
            % (PACKAGE_NAME, version),
        )
        wheel.writestr(
            "%s/WHEEL" % dist_info,
            "Wheel-Version: 1.0\n"
            "Generator: openupgrader test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
        wheel.writestr(
            "%s/RECORD" % dist_info,
            "\n".join(
                [
                    "%s/__init__.py,," % PACKAGE_MODULE,
                    "%s/METADATA,," % dist_info,
                    "%s/WHEEL,," % dist_info,
                    "%s/RECORD,," % dist_info,
                ]
            )
            + "\n",
        )
    simple_dir = index_root / "simple" / _normalize_index_name(PACKAGE_NAME)
    simple_dir.mkdir(parents=True, exist_ok=True)
    (simple_dir / "index.html").write_text(
        '<html><body><a href="../../packages/%s">%s</a></body></html>\n'
        % (wheel_filename, wheel_filename)
    )


@tagged("post_install", "-at_install")
class TestPipIndexPriority(SavepointCase):
    """Check the index priority used to install addons with uv."""

    def setUp(self):
        super().setUp()
        uv_path = shutil.which("uv")
        if not uv_path:
            local_uv = os.path.expanduser("~/.local/bin/uv")
            uv_path = local_uv if os.path.isfile(local_uv) else None
        if not uv_path:
            self.skipTest("uv is required to test the pip index priority")
        self.uv_path = uv_path
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def _start_index_server(self, root):
        handler = functools.partial(PackageIndexHandler, index_root=str(root))
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%s/simple" % server.server_address[1]

    @staticmethod
    def _clean_env():
        env = os.environ.copy()
        for name in (
            "PIP_EXTRA_INDEX_URL",
            "PIP_INDEX_URL",
            "UV_DEFAULT_INDEX",
            "UV_EXTRA_INDEX_URL",
            "UV_INDEX",
            "UV_INDEX_STRATEGY",
        ):
            env.pop(name, None)
        return env

    def _install_package(self, private_version, default_version):
        workdir = Path(self._tmpdir.name) / (
            "openupgrader-index-%s-%s" % (private_version, default_version)
        )
        private_root = workdir / "private"
        default_root = workdir / "default"
        private_root.mkdir(parents=True)
        default_root.mkdir(parents=True)
        _build_wheel(private_root, private_version, "private")
        _build_wheel(default_root, default_version, "default")
        private_url = self._start_index_server(private_root)
        default_url = self._start_index_server(default_root)

        index_options_list = self.env[
            "openupgrader.config"
        ]._get_module_index_options(private_url, True)
        self.assertEqual(len(index_options_list), 2)
        index_options = index_options_list[0]
        self.assertIn("--index %s" % private_url, index_options)
        self.assertIn("--default-index %s" % PYPI_INDEX_URL, index_options)
        self.assertIn("--index-strategy unsafe-best-match", index_options)
        # PyPI is replaced by the local default index so the test is offline
        # and deterministic. The index order and strategy are the ones built
        # by the module.
        index_options = index_options.replace(PYPI_INDEX_URL, default_url)
        self.assertIn("--default-index %s" % default_url, index_options)

        env = self._clean_env()
        venv_path = workdir / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(venv_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        venv_python = venv_path / "bin" / "python"
        command = [
            self.uv_path,
            "pip",
            "install",
            "--no-config",
            "--no-cache",
            "--python",
            str(venv_python),
            "--no-deps",
        ]
        command += shlex.split(index_options)
        # Do not pin the exact patch version: uv must choose between the
        # indexes. This is the regression case for
        # sale_timesheet_invoice_description 17.0.1.0.2.1 vs 17.0.1.0.2.
        command.append("%s==%s" % (PACKAGE_NAME, VERSION_SPEC))
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=env,
        )
        self.assertEqual(
            process.returncode,
            0,
            "uv failed to install %s==%s:\n%s\n%s"
            % (PACKAGE_NAME, VERSION_SPEC, process.stdout, process.stderr),
        )
        check = subprocess.run(
            [
                str(venv_python),
                "-c",
                "import {module}; print({module}.SOURCE, {module}.VERSION)".format(
                    module=PACKAGE_MODULE
                ),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=env,
        )
        self.assertEqual(
            check.returncode,
            0,
            "Unable to import the installed package:\n%s" % check.stderr,
        )
        return check.stdout.strip().split()

    def test_private_index_priority(self):
        """The private index wins on equal versions, the newest one otherwise."""
        scenarios = (
            (
                "same version",
                "17.0.1.0.2",
                "17.0.1.0.2",
                "private",
                "17.0.1.0.2",
            ),
            (
                "17.0.1.0.2.1 on private over 17.0.1.0.2",
                "17.0.1.0.2.1",
                "17.0.1.0.2",
                "private",
                "17.0.1.0.2.1",
            ),
            (
                "17.0.1.0.2.1 on default over 17.0.1.0.2",
                "17.0.1.0.2",
                "17.0.1.0.2.1",
                "default",
                "17.0.1.0.2.1",
            ),
        )
        for label, private_version, default_version, marker, version in scenarios:
            with self.subTest(scenario=label):
                source, installed_version = self._install_package(
                    private_version, default_version
                )
                self.assertEqual(source, marker)
                self.assertEqual(installed_version, version)
