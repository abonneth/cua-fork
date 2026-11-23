import FreeCAD as App
from PySide import QtCore

doc = App.newDocument("Churn")
ticks = [0]


def tick():
    ticks[0] += 1
    if ticks[0] % 2:
        for i in range(20):
            doc.addObject("Part::Box", f"B{ticks[0]}_{i}")
    else:
        for obj in list(doc.Objects):
            doc.removeObject(obj.Name)
    doc.recompute()


App.ab_timer = QtCore.QTimer()
App.ab_timer.timeout.connect(tick)
App.ab_timer.start(700)
