from ._core import QObject, _Size


class QImage(QObject):
    def __init__(self, *a, **k):
        super().__init__()
        if len(a) >= 3 and isinstance(a[1], int):
            self._w, self._h = a[1], a[2]
        elif len(a) >= 2 and isinstance(a[0], int):
            self._w, self._h = a[0], a[1]
        else:
            self._w = self._h = 0

    def convertToFormat(self, fmt):
        q = QImage()
        q._w, q._h = self._w, self._h
        return q

    def copy(self, *a):
        return self.convertToFormat(None)

    def width(self):
        return self._w

    def height(self):
        return self._h

    def size(self):
        return _Size(self._w, self._h)

    def isNull(self):
        return self._w == 0


class _Clipboard(QObject):
    def __init__(self):
        super().__init__()
        self.image_set = None
        self.text_set = None

    def setImage(self, q):
        self.image_set = q

    def setText(self, t):
        self.text_set = t

    def image(self):
        return QImage()

    def mimeData(self):
        return None


_clipboard = _Clipboard()


class QGuiApplication(QObject):
    @staticmethod
    def clipboard():
        return _clipboard


class QKeySequence(QObject):
    pass


def __getattr__(name):
    return type(name, (QObject,), {})
