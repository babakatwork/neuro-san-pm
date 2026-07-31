"""Coded tools contributed by the PM project and installed dependencies.

Several Neuro SAN projects intentionally publish tools below the top-level
``coded_tools`` package. Extending the package path lets this project retain
its local colleague tools while loading tools supplied by dependencies, such
as ``neuro-san-coder``.
"""

from pathlib import Path
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

# neuro-san-coder intentionally uses the same top-level package name. Its
# current revision is not wheel-buildable because setuptools discovers its
# non-package config/registries directories. Keep it as a pinned submodule and
# contribute only its coded_tools directory to this package's search path.
_coder_tools = Path(__file__).resolve().parent.parent / "vendor" / "neuro-san-coder" / "coded_tools"
if _coder_tools.is_dir():
    __path__.append(str(_coder_tools))
