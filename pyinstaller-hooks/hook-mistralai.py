from PyInstaller.utils.hooks import collect_all


def _include_mistralai_submodules(module_name: str) -> bool:
    # Exclude test/example directories if present
    return not module_name.startswith("mistralai.tests")


datas, binaries, hiddenimports = collect_all(
    "mistralai",
    filter_submodules=_include_mistralai_submodules,
)
