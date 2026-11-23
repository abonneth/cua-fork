"""A Qt window whose tree and table models reset every 300 ms, as FreeCAD's model tree and property editor do."""

import sys

try:
    from PySide6 import QtCore, QtGui, QtWidgets

    BINDING = "PySide6"
except ImportError:
    from PyQt5 import QtCore, QtGui, QtWidgets

    BINDING = "PyQt5"

app = QtWidgets.QApplication(sys.argv)
window = QtWidgets.QMainWindow()
window.setWindowTitle(f"QtChurn {BINDING} Qt {QtCore.qVersion()}")
split = QtWidgets.QSplitter()
tree, table = QtWidgets.QTreeView(), QtWidgets.QTableView()
tree_model, table_model = QtGui.QStandardItemModel(), QtGui.QStandardItemModel()
tree.setModel(tree_model)
table.setModel(table_model)
split.addWidget(tree)
split.addWidget(table)
window.setCentralWidget(split)
window.resize(1200, 800)
ticks = [0]


def churn():
    ticks[0] += 1
    tree_model.clear()
    for i in range(40):
        row = QtGui.QStandardItem(f"Body {ticks[0]}.{i}")
        for j in range(5):
            row.appendRow(QtGui.QStandardItem(f"Feature {j}"))
        tree_model.appendRow(row)
    tree.expandAll()
    table_model.clear()
    table_model.setColumnCount(2)
    for i in range(30):
        table_model.appendRow([QtGui.QStandardItem(f"Prop {i}"), QtGui.QStandardItem(str(ticks[0] * i))])
    tree.setCurrentIndex(tree_model.index(ticks[0] % 40, 0))
    table.selectRow(ticks[0] % 30)


timer = QtCore.QTimer()
timer.timeout.connect(churn)
timer.start(300)
window.show()
window.raise_()
sys.exit(app.exec() if hasattr(app, "exec") else app.exec_())
