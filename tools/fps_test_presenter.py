#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""FPS verification aid: a Direct3D 11 window that presents at a fixed rate.

Run it, then choose `python.exe` (or the frozen name) as the FPS process in
Prism Monitor, or focus this window in automatic mode. The widget should read
close to the requested rate, independent of the monitor's refresh rate.

    python tools/fps_test_presenter.py --fps 48 --seconds 30
"""
import argparse
import os
import sys

os.environ.setdefault('QSG_RHI_BACKEND', 'd3d11')

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

QML = b'''
import QtQuick
Rectangle {
    width: 360; height: 200; color: "#141821"
    property real angle: 0
    Rectangle { anchors.centerIn: parent; width: 90; height: 90; radius: 12; color: "#7c9cff"; rotation: parent.angle }
    Text { anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; anchors.bottomMargin: 10
           color: "#eef1f8"; text: "FPS test presenter: " + fpsTarget + " fps requested" }
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fps', type=float, default=48)
    parser.add_argument('--seconds', type=float, default=30)
    args = parser.parse_args()
    app = QGuiApplication(sys.argv[:1])
    view = QQuickView()
    view.rootContext().setContextProperty('fpsTarget', args.fps)
    path = os.path.join(os.environ.get('TEMP', '.'), 'prism_fps_test.qml')
    with open(path, 'wb') as f:
        f.write(QML)
    view.setSource(QUrl.fromLocalFile(path))
    view.setTitle('FPS test presenter')
    view.show()
    root = view.rootObject()
    timer = QTimer(interval=max(1, round(1000 / args.fps)))
    timer.setTimerType(Qt.PreciseTimer)
    timer.timeout.connect(lambda: root.setProperty('angle', (root.property('angle') + 3) % 360))
    timer.start()
    QTimer.singleShot(int(args.seconds * 1000), app.quit)
    print(f'pid {os.getpid()} presenting at ~{args.fps} fps for {args.seconds} s', flush=True)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
