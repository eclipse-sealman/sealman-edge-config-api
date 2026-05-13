import pkgutil
import importlib


def auto_import_repositories():
    package_name = __name__

    for _, module_name, _ in pkgutil.walk_packages(__path__, package_name + "."):
        importlib.import_module(module_name)