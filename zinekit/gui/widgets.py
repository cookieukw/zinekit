"""The parameter panel and the preset bar."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QFileDialog, QGridLayout, QGroupBox,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from .. import params as P
from .. import presets as PR
from ..i18n import tr
from .util import NoWheelComboBox, NoWheelSlider, NoWheelSpinBox, swatch_icon


class ColorButton(QPushButton):
    """A swatch with the hex value; click to pick a color."""
    colorChanged = Signal(str)

    def __init__(self, color: str = "#000000", title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._title = title
        self._color = P.normalize_color(color)
        self.clicked.connect(self._pick)
        self._refresh()

    def color(self) -> str:
        return self._color

    def set_color(self, color: str, emit: bool = False) -> None:
        color = P.normalize_color(color)
        if color == self._color:
            return
        self._color = color
        self._refresh()
        if emit:
            self.colorChanged.emit(color)

    def _refresh(self) -> None:
        self.setIcon(swatch_icon(self._color))
        self.setText(self._color)

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self._color), self, self._title or tr("Color"))
        if c.isValid():
            self.set_color(c.name(), emit=True)


class ResetLabel(QLabel):
    doubleClicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class _Row:
    """One parameter: its label and control(s)."""

    def __init__(self, panel: "ParamPanel", param: P.Param, grid: QGridLayout, row: int):
        self.panel = panel
        self.param = param
        self.tip = tr(param.tip) if param.tip else ""
        self.label = ResetLabel(tr(param.label))
        self.label.doubleClicked.connect(lambda: panel.reset_param(param.name))
        grid.addWidget(self.label, row, 0)
        self.widgets: List[QWidget] = [self.label]
        self.slider = self.spin = self.combo = self.check = self.color = None
        kind = param.kind
        if kind in ("slider", "int"):
            top = int(param.ui_max)
            self.spin = NoWheelSpinBox()
            self.spin.setRange(0, top)
            if param.suffix:
                self.spin.setSuffix(param.suffix)
            self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.spin.setMinimumWidth(68)
            self.spin.valueChanged.connect(self._from_spin)
            if kind == "slider":
                self.slider = NoWheelSlider(Qt.Orientation.Horizontal)
                self.slider.setRange(0, top)
                self.slider.setPageStep(max(1, top // 10))
                self.slider.valueChanged.connect(self._from_slider)
                grid.addWidget(self.slider, row, 1)
                grid.addWidget(self.spin, row, 2)
                self.widgets += [self.slider, self.spin]
            else:
                dice = QToolButton()
                dice.setText(tr("Random"))
                dice.clicked.connect(lambda: self.spin.setValue(random.randint(0, top)))
                box = QHBoxLayout()
                box.setContentsMargins(0, 0, 0, 0)
                box.addWidget(self.spin)
                box.addWidget(dice)
                box.addStretch(1)
                holder = QWidget()
                holder.setLayout(box)
                grid.addWidget(holder, row, 1, 1, 2)
                self.widgets += [self.spin, dice]
        elif kind == "list":
            self.combo = NoWheelComboBox()
            for opt in param.options:
                self.combo.addItem(tr(opt))
            self.combo.currentIndexChanged.connect(self._from_combo)
            grid.addWidget(self.combo, row, 1, 1, 2)
            self.widgets.append(self.combo)
        elif kind == "bool":
            self.check = QCheckBox()
            self.check.toggled.connect(self._from_check)
            grid.addWidget(self.check, row, 1, 1, 2)
            self.widgets.append(self.check)
        elif kind == "color":
            self.color = ColorButton(str(param.default), tr(param.label))
            self.color.colorChanged.connect(self._from_color)
            grid.addWidget(self.color, row, 1, 1, 2)
            self.widgets.append(self.color)
        self.set_enabled(True)

    # control -> value
    def _from_slider(self, v: int) -> None:
        if self.spin is not None and self.spin.value() != v:
            self.spin.blockSignals(True)
            self.spin.setValue(v)
            self.spin.blockSignals(False)
        self.panel.user_set(self.param.name, self.param.from_ui(v))

    def _from_spin(self, v: int) -> None:
        if self.slider is not None and self.slider.value() != v:
            self.slider.blockSignals(True)
            self.slider.setValue(v)
            self.slider.blockSignals(False)
        self.panel.user_set(self.param.name, self.param.from_ui(v))

    def _from_combo(self, i: int) -> None:
        if i >= 0:
            self.panel.user_set(self.param.name, self.param.value_of(i))

    def _from_check(self, on: bool) -> None:
        self.panel.user_set(self.param.name, 1.0 if on else 0.0)

    def _from_color(self, c: str) -> None:
        self.panel.user_set(self.param.name, c)

    # value -> control
    def show_value(self, value: P.Value) -> None:
        ui = self.param.to_ui(value)
        for w in self.widgets:
            w.blockSignals(True)
        try:
            if self.slider is not None:
                self.slider.setValue(int(round(float(ui))))
            if self.spin is not None:
                self.spin.setValue(int(round(float(ui))))
            if self.combo is not None:
                self.combo.setCurrentIndex(int(ui))
            if self.check is not None:
                self.check.setChecked(bool(ui))
            if self.color is not None:
                self.color.set_color(str(ui))
        finally:
            for w in self.widgets:
                w.blockSignals(False)

    def set_enabled(self, on: bool) -> None:
        tip = self.tip
        if not on:
            tip = (tip + "\n" if tip else "") + tr("Not used in the selected print mode.")
        tip = (tip + "\n" if tip else "") + tr("Double-click to reset.")
        for w in self.widgets:
            if w is not self.label:       # the label stays enabled so a double-click still resets
                w.setEnabled(on)
            w.setToolTip(tip)


class ParamPanel(QWidget):
    """Every Punk Zine parameter, grouped like the Kdenlive effect.  Values are in plugin units."""
    changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._values: Dict[str, P.Value] = P.defaults()
        self._rows: Dict[str, _Row] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        for group in P.GROUPS:
            items = [p for p in P.PARAMS if p.group == group]
            if not items:
                continue
            box = QGroupBox(tr(group))
            grid = QGridLayout(box)
            grid.setColumnStretch(1, 1)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(4)
            for r, p in enumerate(items):
                self._rows[p.name] = _Row(self, p, grid, r)
            lay.addWidget(box)
        lay.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._show_all()

    def values(self) -> Dict[str, P.Value]:
        return dict(self._values)

    def set_values(self, values: Dict[str, P.Value], emit: bool = True) -> None:
        self._values = P.complete(values)
        self._show_all()
        if emit:
            self.changed.emit()

    def reset_param(self, name: str) -> None:
        self.user_set(name, P.BY_NAME[name].default)
        self._rows[name].show_value(self._values[name])

    def user_set(self, name: str, value: P.Value) -> None:
        if self._values.get(name) == value:
            return
        self._values[name] = value
        if name == "mode":
            self._update_enabled()
        self.changed.emit()

    def _show_all(self) -> None:
        for name, row in self._rows.items():
            row.show_value(self._values[name])
        self._update_enabled()

    def _update_enabled(self) -> None:
        mode = P.mode_of(self._values)
        for row in self._rows.values():
            row.set_enabled(P.applies(row.param, mode))


def same_values(a: Dict[str, P.Value], b: Dict[str, P.Value]) -> bool:
    return P.diff_from_defaults(P.complete(a)) == P.diff_from_defaults(P.complete(b))


class PresetBar(QWidget):
    """Choose, save, import and export presets."""
    chosen = Signal(object)      # dict of values
    message = Signal(str)

    def __init__(self, get_values: Callable[[], Dict[str, P.Value]], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._get_values = get_values
        self._presets: List[PR.Preset] = []
        self.combo = QComboBox()
        self.combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.combo.activated.connect(self._activated)
        self.modified = QLabel(tr("(modified)"))
        self.modified.setVisible(False)
        save = QToolButton()
        save.setText(tr("Save preset…"))
        save.clicked.connect(self.save_preset)
        more = QToolButton()
        more.setText("⋯")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        for label, slot in ((tr("Delete preset"), self.delete_preset), (None, None),
                            (tr("Import preset…"), self.import_preset), (tr("Export preset…"), self.export_preset),
                            (None, None), (tr("Reset all"), self.reset_all),
                            (tr("Copy ffmpeg filter"), self.copy_ffmpeg)):
            if label is None:
                menu.addSeparator()
            else:
                menu.addAction(label).triggered.connect(slot)
        more.setMenu(menu)
        self._menu = menu
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 0)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Preset")))
        top.addWidget(self.combo, 1)
        top.addWidget(save)
        top.addWidget(more)
        lay.addLayout(top)
        lay.addWidget(self.modified, 0, Qt.AlignmentFlag.AlignRight)
        self.reload()

    # list
    def reload(self, select: Optional[str] = None) -> None:
        current = select if select is not None else self.current_name()
        self._presets = PR.all_presets()
        self.combo.blockSignals(True)
        self.combo.clear()
        for i, p in enumerate(self._presets):
            self.combo.addItem(tr(p.name) if p.builtin else p.name, i)
        self.combo.blockSignals(False)
        self.select(current or "Default")

    def current(self) -> Optional[PR.Preset]:
        i = self.combo.currentIndex()
        return self._presets[i] if 0 <= i < len(self._presets) else None

    def current_name(self) -> str:
        p = self.current()
        return p.name if p else ""

    def select(self, name: str) -> None:
        for i, p in enumerate(self._presets):
            if p.name == name:
                self.combo.setCurrentIndex(i)
                break
        self.refresh_modified()

    def refresh_modified(self) -> None:
        p = self.current()
        self.modified.setVisible(p is not None and not same_values(p.values(), self._get_values()))

    def _activated(self, i: int) -> None:
        if 0 <= i < len(self._presets):
            self.chosen.emit(self._presets[i].values())
            self.refresh_modified()

    # actions
    def save_preset(self) -> None:
        p = self.current()
        suggestion = p.name if (p and not p.builtin) else ""
        name, ok = QInputDialog.getText(self, tr("Save preset…"), tr("Preset name:"), QLineEdit.EchoMode.Normal,
                                        suggestion)
        name = (name or "").strip()
        if not ok or not name:
            return
        try:
            path = PR.save_user(name, self._get_values())
        except OSError as e:
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        self.reload(select=name)
        self.message.emit(tr("Saved %s") % path)

    def delete_preset(self) -> None:
        p = self.current()
        if p is None:
            return
        if p.builtin:
            QMessageBox.information(self, tr("Delete preset"), tr("Built-in presets cannot be deleted."))
            return
        ans = QMessageBox.question(self, tr("Delete preset"), tr("Delete preset \"%s\"?") % p.name)
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            PR.delete_user(p)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        self.reload(select="Default")

    def import_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Import preset…"), str(Path.home()),
                                              "%s (*.json);;%s (*)" % (tr("zinekit preset"), tr("All files")))
        if not path:
            return
        try:
            p = PR.load_file(path)
            PR.save_user(p.name, p.values())
        except (OSError, ValueError, json.JSONDecodeError) as e:
            QMessageBox.warning(self, tr("Error"), "%s\n%s" % (path, e))
            return
        self.reload(select=p.name)
        self.chosen.emit(p.values())
        self.refresh_modified()

    def export_preset(self) -> None:
        p = self.current()
        name = (p.name if p and not p.builtin else "") or "zine"
        path, _ = QFileDialog.getSaveFileName(self, tr("Export preset…"), str(Path.home() / (PR.slug(name) + ".json")),
                                              "%s (*.json)" % tr("zinekit preset"))
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            Path(path).write_text(PR.Preset(name, self._get_values()).to_json(), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        self.message.emit(tr("Saved %s") % path)

    def reset_all(self) -> None:
        self.select("Default")
        self.chosen.emit(P.defaults())
        self.refresh_modified()

    def copy_ffmpeg(self) -> None:
        QGuiApplication.clipboard().setText(P.ffmpeg_filter(self._get_values()))
        self.message.emit(tr("ffmpeg filter copied to the clipboard."))
