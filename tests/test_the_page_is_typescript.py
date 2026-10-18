"""The viewer's page is TypeScript, checked by the type checker in the build.

A JavaScript file in the page would be read but never checked, so a type
the rest of the page relies on could quietly stop being true there. What
stays JavaScript is the build's own tooling, which runs in Node: the steps
in ``scripts/`` and the Vite configuration.
"""

from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "gui" / "source"


def test_no_page_file_is_javascript():
    left = sorted(
        path.relative_to(SOURCE).as_posix()
        for path in SOURCE.rglob("*")
        if path.suffix in {".js", ".jsx", ".mjs"}
        and "node_modules" not in path.relative_to(SOURCE).parts
        and path.relative_to(SOURCE).parts[0] != "scripts"
        and path.name != "vite.config.js"
    )
    assert left == [], f"page files still in JavaScript: {left}"
