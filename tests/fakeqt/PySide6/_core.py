import inspect
import threading


class _Any:
    """Any attribute, any call, any flag operation."""

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Any()

    def __call__(self, *a, **k):
        return _Any()

    def __or__(self, other):
        return _Any()

    __ror__ = __and__ = __rand__ = __xor__ = __or__

    def __bool__(self):
        return False

    def __iter__(self):
        return iter(())

    def __int__(self):
        return 0

    def __index__(self):
        return 0

    def __float__(self):
        return 0.0

    def __str__(self):
        return ""


class _AnyMeta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Any()


def _arity(fn):
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    n = 0
    for p in sig.parameters.values():
        if p.kind == p.VAR_POSITIONAL:
            return None
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            n += 1
    return n


class BoundSignal:
    def __init__(self, owner=None):
        self._slots = []
        self._owner = owner

    def connect(self, fn, *a):
        self._slots.append(fn)

    def disconnect(self, fn=None):
        if fn is None:
            self._slots.clear()
        elif fn in self._slots:
            self._slots.remove(fn)

    def emit(self, *args):
        if self._owner is not None and getattr(self._owner, "_blocked", False):
            return
        for fn in list(self._slots):
            n = _arity(fn)
            fn(*(args if n is None else args[:n]))


class Signal:
    def __init__(self, *types, **k):
        self.name = None

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        key = "_sig_" + self.name
        bound = obj.__dict__.get(key)
        if bound is None:
            bound = BoundSignal(obj)
            obj.__dict__[key] = bound
        return bound


def Slot(*a, **k):
    return lambda fn: fn


SIGNALS = ("clicked", "toggled", "valueChanged", "currentIndexChanged", "activated", "textChanged", "timeout",
           "triggered", "pressed", "released", "currentChanged", "currentItemChanged", "itemSelectionChanged",
           "editingFinished", "destroyed")


class QObject(metaclass=_AnyMeta):
    def __init__(self, *a, **k):
        self._blocked = False

    def __getattr__(self, name):
        if name in SIGNALS:
            s = BoundSignal(self)
            self.__dict__[name] = s
            return s
        if name.startswith("__"):
            raise AttributeError(name)
        return _Any()

    def blockSignals(self, on):
        old = getattr(self, "_blocked", False)
        self._blocked = bool(on)
        return old

    def signalsBlocked(self):
        return getattr(self, "_blocked", False)


class _Size:
    def __init__(self, w, h):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h

    def __eq__(self, o):
        return isinstance(o, _Size) and (o._w, o._h) == (self._w, self._h)

    def __ne__(self, o):
        return not self == o

    __hash__ = None


class QTimer(QObject):
    def __init__(self, *a, **k):
        super().__init__()
        self._single = False

    def setSingleShot(self, on):
        self._single = on

    def setInterval(self, ms):
        pass

    def start(self, *a):
        self.timeout.emit()      # fires at once: no event loop here

    def stop(self):
        pass

    @staticmethod
    def singleShot(ms, fn):
        fn()


class QSettings(QObject):
    store = {}

    def __init__(self, *a, **k):
        super().__init__()

    def value(self, key, default=None):
        return QSettings.store.get(key, default)

    def setValue(self, key, value):
        QSettings.store[key] = value

    def sync(self):
        pass


class QByteArray(bytes):
    pass


class QEvent(metaclass=_AnyMeta):
    pass


class QUrl(_Any):
    @staticmethod
    def fromLocalFile(p):
        return _Any()


class QPointF(_Any):
    pass


class QRectF(_Any):
    pass


class QBuffer(_Any):
    pass


class QIODevice(metaclass=_AnyMeta):
    pass


class Qt(metaclass=_AnyMeta):
    pass


_lock = threading.Lock()
