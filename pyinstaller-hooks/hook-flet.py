from PyInstaller.utils.hooks import collect_all


def _include_flet_submodules(module_name: str) -> bool:
    return not module_name.startswith("flet.testing")


datas, binaries, hiddenimports = collect_all(
    "flet",
    filter_submodules=_include_flet_submodules,
)