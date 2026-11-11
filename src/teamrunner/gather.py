# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""`teamrunner gather` (D94): make a package safe to hand over.

Links (symbolic links) may point outside the package while you work; a
copy of the package would break. `gather` lists every such link, then
replaces each with a real copy of what it points to, in place. Links
inside the package travel with it and are left alone. A `.env` in the
package is warned about: it is not needed and carries keys (D99).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .keys import package_envs
from .loader import find_top, _links_outside


def outside_links(folder: Path) -> tuple[Path, list[tuple[Path, Path]]]:
    top = find_top(Path(folder))
    return top, _links_outside(top)


def replace(link: Path, target: Path) -> None:
    """Swap a link for a real copy of what it points to."""
    temporary = link.with_name(link.name + ".gathering")
    if target.is_dir():
        shutil.copytree(target, temporary, symlinks=False)
    else:
        shutil.copy2(target, temporary)
    link.unlink()
    temporary.rename(link)


def envs(top: Path) -> list[Path]:
    return package_envs(top)
