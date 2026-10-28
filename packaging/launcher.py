"""PyInstaller entry point (absolute import; the package's __main__.py uses relative imports)."""
import sys

from wpl.app import main

if __name__ == "__main__":
    sys.exit(main())
