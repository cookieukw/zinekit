from ._core import (QBuffer, QByteArray, QEvent, QIODevice, QObject, QPointF, QRectF, QSettings, Qt, QTimer,  # noqa
                    QUrl, Signal, Slot, _Any, _AnyMeta, _Size)


def __getattr__(name):
    return type(name, (QObject,), {})
