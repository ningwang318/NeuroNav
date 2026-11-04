"""OpenGL context handling shared by every pyqtgraph GLViewWidget in the app.

pyqtgraph initializes textures/display lists as soon as an item is added to a
live view. File-loading callbacks and Qt signals run outside paintGL, possibly
with no context current or with another view's context current, so resource
creation and deletion must be wrapped in the owning view's context.
"""
from contextlib import contextmanager

from PySide6 import QtGui


@contextmanager
def gl_context(view, restore_previous=True, skip_if_current=True):
    """Make ``view``'s GL context current for the block (no-op for an invalid view).

    restore_previous: afterwards re-activate the context that was current before.
    skip_if_current: do nothing when ``view``'s own context is already current.
    """
    if not view.isValid():
        yield
        return
    previous = QtGui.QOpenGLContext.currentContext() if (restore_previous or skip_if_current) else None
    if skip_if_current and previous == view.context():
        yield
        return
    surface = previous.surface() if restore_previous and previous is not None else None
    view.makeCurrent()
    try:
        yield
    finally:
        view.doneCurrent()
        if restore_previous and previous is not None and surface is not None:
            previous.makeCurrent(surface)


def delete_gl_resources(item):
    """Release an item's texture and legacy display lists (GLVolumeItem/GLImageItem).

    Call inside gl_context(view) for a valid view.
    """
    from OpenGL import GL
    texture = getattr(item, 'texture', None)
    if texture is not None:
        GL.glDeleteTextures([texture])
    for display_list in getattr(item, 'lists', {}).values():
        GL.glDeleteLists(display_list, 1)
