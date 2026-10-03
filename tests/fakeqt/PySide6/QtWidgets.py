from ._core import QObject, _Any, _AnyMeta, _Size  # noqa: F401
from .QtGui import QGuiApplication


class QWidget(QObject):
    def __init__(self, *a, **k):
        super().__init__()
        self._enabled = True
        self._visible = True
        self._tip = ""

    def setEnabled(self, on):
        self._enabled = bool(on)

    def isEnabled(self):
        return self._enabled

    def setVisible(self, on):
        self._visible = bool(on)

    def isVisible(self):
        return self._visible

    def isHidden(self):
        return not self._visible

    def setToolTip(self, t):
        self._tip = t

    def toolTip(self):
        return self._tip

    def update(self, *a):
        pass

    def isActiveWindow(self):
        return True

    def _event(self, event):
        pass

    closeEvent = paintEvent = mousePressEvent = mouseMoveEvent = mouseDoubleClickEvent = wheelEvent = _event
    keyPressEvent = keyReleaseEvent = dragEnterEvent = dragMoveEvent = dropEvent = resizeEvent = _event
    changeEvent = _event


class QMainWindow(QWidget):
    pass


class QLabel(QWidget):
    def __init__(self, text="", *a):
        super().__init__()
        self._text = text

    def setText(self, t):
        self._text = t

    def text(self):
        return self._text


class QPushButton(QWidget):
    def __init__(self, text="", *a):
        super().__init__()
        self._text = text

    def setText(self, t):
        self._text = t

    def text(self):
        return self._text

    def click(self):
        self.clicked.emit(False)


class QToolButton(QPushButton):
    pass


class QCheckBox(QWidget):
    def __init__(self, text="", *a):
        super().__init__()
        self._checked = False

    def isChecked(self):
        return self._checked

    def setChecked(self, on):
        on = bool(on)
        if on != self._checked:
            self._checked = on
            self.toggled.emit(on)


class QAbstractSpinBox(QWidget):
    pass


class QSpinBox(QAbstractSpinBox):
    def __init__(self, *a):
        super().__init__()
        self._v, self._lo, self._hi = 0, 0, 99

    def setRange(self, lo, hi):
        self._lo, self._hi = lo, hi
        self.setValue(self._v)

    def value(self):
        return self._v

    def setValue(self, v):
        v = max(self._lo, min(self._hi, type(self._v)(v)))
        if v != self._v:
            self._v = v
            self.valueChanged.emit(v)


class QDoubleSpinBox(QSpinBox):
    def __init__(self, *a):
        super().__init__()
        self._v = 0.0


class QSlider(QSpinBox):
    pass


class QComboBox(QWidget):
    def __init__(self, *a):
        super().__init__()
        self._items = []
        self._i = -1
        self._editable = False

    def addItem(self, *a):
        text = a[0] if isinstance(a[0], str) else a[1]
        data = a[1] if isinstance(a[0], str) and len(a) > 1 else (a[2] if len(a) > 2 else None)
        self._items.append((text, data))
        if self._i < 0:
            self.setCurrentIndex(0)

    def clear(self):
        self._items = []
        self._i = -1

    def count(self):
        return len(self._items)

    def itemText(self, i):
        return self._items[i][0] if 0 <= i < len(self._items) else ""

    def itemData(self, i):
        return self._items[i][1] if 0 <= i < len(self._items) else None

    def findData(self, d):
        for i, (_t, v) in enumerate(self._items):
            if v == d:
                return i
        return -1

    def currentIndex(self):
        return self._i

    def setCurrentIndex(self, i):
        if i != self._i and -1 <= i < len(self._items):
            self._i = i
            self.currentIndexChanged.emit(i)

    def currentData(self):
        return self.itemData(self._i)

    def currentText(self):
        return self.itemText(self._i)

    def setEditable(self, on):
        self._editable = on

    def isEditable(self):
        return self._editable

    def completer(self):
        return _Any()

    def activate(self, i):          # test helper: the user picks an item
        self.setCurrentIndex(i)
        self.activated.emit(i)


class QLineEdit(QWidget):
    def __init__(self, text="", *a):
        super().__init__()
        self._text = text

    def text(self):
        return self._text

    def setText(self, t):
        if t != self._text:
            self._text = t
            self.textChanged.emit(t)


class QPlainTextEdit(QWidget):
    def __init__(self, *a):
        super().__init__()
        self._text = ""

    def toPlainText(self):
        return self._text

    def setPlainText(self, t):
        self._text = t
        self.textChanged.emit()

    def appendPlainText(self, t):
        self._text += ("\n" if self._text else "") + t


class QListWidgetItem(QObject):
    def __init__(self, *a):
        super().__init__()
        self._text = a[-1] if a and isinstance(a[-1], str) else ""
        self._data = {}

    def setData(self, role, v):
        self._data["d"] = v

    def data(self, role):
        return self._data.get("d")

    def text(self):
        return self._text


class QListWidget(QWidget):
    def __init__(self, *a):
        super().__init__()
        self._items = []
        self._cur = -1

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def currentRow(self):
        return self._cur

    def setCurrentRow(self, r):
        prev = self.currentItem()
        self._cur = r
        self.currentItemChanged.emit(self.currentItem(), prev)

    def currentItem(self):
        return self._items[self._cur] if 0 <= self._cur < len(self._items) else None

    def selectedItems(self):
        it = self.currentItem()
        return [it] if it is not None else []

    def row(self, item):
        return self._items.index(item)

    def takeItem(self, r):
        it = self._items.pop(r)
        if self._cur >= len(self._items):
            self.setCurrentRow(len(self._items) - 1)
        return it

    def clear(self):
        self._items = []
        self.setCurrentRow(-1)


class QTabWidget(QWidget):
    def __init__(self, *a):
        super().__init__()
        self._pages = []
        self._i = -1

    def addTab(self, w, label):
        self._pages.append(w)
        if self._i < 0:
            self._i = 0
            self.currentChanged.emit(0)

    def currentIndex(self):
        return self._i

    def setCurrentIndex(self, i):
        if 0 <= i < len(self._pages) and i != self._i:
            self._i = i
            self.currentChanged.emit(i)

    def currentWidget(self):
        return self._pages[self._i] if self._pages else None

    def setCurrentWidget(self, w):
        self.setCurrentIndex(self._pages.index(w))


class QProgressBar(QWidget):
    def __init__(self, *a):
        super().__init__()
        self._v = 0

    def setValue(self, v):
        self._v = v

    def value(self):
        return self._v


class _Dialogs:
    open_file = ""
    open_files = []
    save_file = ""
    directory = ""
    text = ("", False)
    question = None
    calls = []


class QFileDialog(QWidget):
    @staticmethod
    def getOpenFileName(*a, **k):
        return _Dialogs.open_file, ""

    @staticmethod
    def getOpenFileNames(*a, **k):
        return list(_Dialogs.open_files), ""

    @staticmethod
    def getSaveFileName(*a, **k):
        return _Dialogs.save_file, ""

    @staticmethod
    def getExistingDirectory(*a, **k):
        return _Dialogs.directory


class QInputDialog(QWidget):
    @staticmethod
    def getText(*a, **k):
        return _Dialogs.text


class QMessageBox(QWidget):
    class StandardButton:
        Yes = "yes"
        No = "no"

    @staticmethod
    def question(*a, **k):
        _Dialogs.calls.append(("question",) + a[1:])
        return _Dialogs.question or QMessageBox.StandardButton.Yes

    @staticmethod
    def warning(*a, **k):
        _Dialogs.calls.append(("warning",) + a[1:])

    @staticmethod
    def information(*a, **k):
        _Dialogs.calls.append(("information",) + a[1:])

    @staticmethod
    def critical(*a, **k):
        _Dialogs.calls.append(("critical",) + a[1:])

    @staticmethod
    def about(*a, **k):
        _Dialogs.calls.append(("about",) + a[1:])


class QColorDialog(QWidget):
    @staticmethod
    def getColor(*a, **k):
        return _Any()


class QApplication(QGuiApplication):
    _inst = None

    def __init__(self, *a):
        super().__init__()
        QApplication._inst = self

    @staticmethod
    def instance():
        return QApplication._inst

    @staticmethod
    def focusWidget():
        return None

    def exec(self):
        return 0

    @staticmethod
    def processEvents(*a):
        pass


def __getattr__(name):
    return type(name, (QWidget,), {})
