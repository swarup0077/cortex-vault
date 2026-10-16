import sys

import vault


def test_package_imports() -> None:
    assert vault.__version__


def test_python_is_3_12() -> None:
    assert sys.version_info[:2] == (3, 12)
