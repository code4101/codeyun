from pathlib import Path

from fastapi import FastAPI

from backend.core.private_modules import register_private_modules


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_register_private_modules_loads_package_with_relative_import(tmp_path):
    modules_dir = tmp_path / "private_modules"
    module_dir = modules_dir / "alpha_private"
    write_text(module_dir / "helper.py", "MODULE_NAME = 'alpha-private'\n")
    write_text(
        module_dir / "__init__.py",
        (
            "from .helper import MODULE_NAME\n\n"
            "def register(app):\n"
            "    app.state.private_modules = getattr(app.state, 'private_modules', [])\n"
            "    app.state.private_modules.append(MODULE_NAME)\n"
        ),
    )

    app = FastAPI()

    loaded = register_private_modules(app, base_dir=modules_dir)

    assert loaded == ("alpha_private",)
    assert app.state.private_modules == ["alpha-private"]


def test_register_private_modules_skips_package_without_register(tmp_path):
    modules_dir = tmp_path / "private_modules"
    write_text(modules_dir / "beta_private" / "__init__.py", "VALUE = 1\n")

    app = FastAPI()

    loaded = register_private_modules(app, base_dir=modules_dir)

    assert loaded == ()
