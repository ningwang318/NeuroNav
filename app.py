"""NeuroNav launcher kept at the project root: ``python app.py [options]``.

The application lives in the ``neuronav`` package; see neuronav/__main__.py.
"""
import sys

from neuronav.__main__ import main


if __name__ == '__main__':
    sys.exit(main())
