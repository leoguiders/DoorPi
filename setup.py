#!/usr/bin/env python3

import sys
from pathlib import Path

from setuptools import setup, find_packages
from setuptools.command.install import install


BASE_PATH = Path(__file__).resolve().parent
ETC = "/etc" if sys.prefix == "/usr" else "etc"


class InstallHook(install):
    """Hook for ``install`` command that processes template files (*.in)"""
    def run(self):
        datapath = BASE_PATH / "data"
        package = self.distribution.metadata.name.lower()
        substkeys = {
            "package": package,
            "project": self.distribution.metadata.name,
            "prefix": self.prefix,
            "cfgdir": (
                f"{self.prefix if sys.prefix == '/usr' else ''}/etc/{package}"),
        }
        for file in datapath.iterdir():
            if file.suffix != ".in": continue
            content = file.read_text()
            for key, val in substkeys.items():
                content = content.replace(f"!!{key}!!", val)
            file.with_suffix("").write_text(content)
        super().run()


setup(
    cmdclass={"install": InstallHook},
    data_files=[
        # init script and systemd service
        (f"{ETC}/init.d", ["data/doorpi.sh"]),
        ("lib/systemd/system", ["data/doorpi.service", "data/doorpi.socket"]),
    ],
)
