"""Command-line entry point: ``python -m neuronav`` or ``python app.py``."""
import argparse
import sys

from PySide6 import QtCore, QtWidgets as Qt


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='neuronav')
    parser.add_argument('--offline', action='store_true', help='Open without auto-connecting')
    parser.add_argument('--use-saved-reference', action='store_true', help='Explicitly apply the saved XYZ reference on opening')
    parser.add_argument('--camera-close-up', action='store_true', help='Open a close-up view of the camera bar')
    parser.add_argument('--mri-file', help='Load a local NIfTI volume')
    parser.add_argument('--mri-demo', action='store_true', help='Show a clearly labelled synthetic MRI phantom')
    parser.add_argument('--smoke-seconds', type=float, default=0, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    # pyqtgraph caches shader programs globally. Share resources with the
    # separate transducer-preview dialog as well as the main scene widgets.
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts)
    app = Qt.QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    from neuronav.ui.main_window import MainWindow
    from neuronav.ui.style import STYLE
    app.setStyleSheet(STYLE)
    window = MainWindow(auto_connect=not args.offline)
    window.camera_closeup_pending = args.camera_close_up
    if args.use_saved_reference:
        window.use_saved_reference()
    if args.mri_file:
        window.mri_workspace.load_path(args.mri_file)
    elif args.mri_demo:
        from neuronav.imaging.mri_volume import demo_volume
        window.mri_workspace.set_volume(demo_volume())
    window.show()
    if args.smoke_seconds:
        QtCore.QTimer.singleShot(int(args.smoke_seconds * 1000), window.close)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
