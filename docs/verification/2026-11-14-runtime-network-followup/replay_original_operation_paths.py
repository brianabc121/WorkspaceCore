"""Pytest-only replay of two original methods; never import in a running service.

The current central enqueue guard stays active. Only the original save_template
and rebuild definitions are restored inside this one pytest process, allowing
the new PostgreSQL regressions to demonstrate the original partial writes.
"""

import ast
import subprocess


def pytest_configure(config):
    import app.domain.runner as module

    source = subprocess.check_output(
        ["git", "show", "180806b45c763079f9a575a1a93ac78a3743a12e:services/api/app/domain/runner.py"],
        text=True,
    )
    cls = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "Runner"
    )
    namespace = dict(vars(module))
    for name in ("save_template", "rebuild"):
        method = next(
            node for node in cls.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == name
        )
        tree = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        exec(compile(tree, "original-180806b-runner", "exec"), namespace)
        setattr(module.Runner, name, namespace[name])
