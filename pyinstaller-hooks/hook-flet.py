from PyInstaller.utils.hooks import collect_all


def _include_flet_submodules(module_name: str) -> bool:
    if module_name.startswith("flet.testing"):
        return False
    if module_name.startswith("flet.security"):
        return False
    return True


datas, binaries, hiddenimports = collect_all(
    "flet",
    filter_submodules=_include_flet_submodules,
)