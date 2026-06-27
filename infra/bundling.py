import os
import shutil
import subprocess
import sys

import jsii
from aws_cdk import ILocalBundling


@jsii.implements(ILocalBundling)
class PipLocalBundling:
    """Installs Lambda-platform wheels with pip directly on the host, skipping Docker.

    Works because all of find_salon.py's dependencies (including lxml) publish
    manylinux wheels on PyPI, so pip can fetch a Linux-compatible build without
    compiling anything locally.
    """

    def __init__(self, source_dir, entry_files, python_version="3.12"):
        self.source_dir = source_dir
        self.entry_files = entry_files
        self.python_version = python_version

    def try_bundle(self, output_dir, **kwargs):
        requirements = os.path.join(self.source_dir, "requirements.txt")
        if os.path.exists(requirements):
            abi = "cp" + self.python_version.replace(".", "")
            subprocess.check_call([
                sys.executable, "-m", "pip", "install",
                "-r", requirements,
                "--platform", "manylinux2014_x86_64",
                "--implementation", "cp",
                "--python-version", self.python_version,
                "--abi", abi,
                "--only-binary=:all:",
                "--target", output_dir,
            ])
        for filename in self.entry_files:
            shutil.copy2(os.path.join(self.source_dir, filename), os.path.join(output_dir, filename))
        return True
