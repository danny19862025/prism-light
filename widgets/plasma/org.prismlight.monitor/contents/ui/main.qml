// SPDX-License-Identifier: GPL-3.0-only
// Prism Monitor for KDE Plasma: draws the Poster that the Prism Monitor program publishes to
// $XDG_RUNTIME_DIR/prism-monitor/widget.json (see prism_monitor/publish.py). The program does
// the sensor reading (hwmon, amdgpu, the FPS layer, UPower); this widget only draws.
import QtQuick
import QtQuick.Effects
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

import "command.js" as Command

PlasmoidItem {
    id: root

    property var doc: null          // the last fresh document, or null
    property bool live: false

    readonly property string command:
        'cat "${XDG_RUNTIME_DIR:-$HOME/.cache}/prism-monitor/widget.json" 2>/dev/null'

    Plasmoid.backgroundHints: PlasmaCore.Types.ShadowBackground | PlasmaCore.Types.ConfigurableBackground
    preferredRepresentation: Plasmoid.formFactor === PlasmaCore.Types.Planar ? fullRepresentation : compactRepresentation
    toolTipMainText: 'Prism Monitor'
    toolTipSubText: !live || !doc ? 'Prism Monitor is not running' : (doc.headline + ' ' + doc.number + doc.unit)

    // Menu commands go to the running Prism Monitor (monitor_main.py --do ACTION), which applies
    // them like its own menu. A left click opens its Settings. Plasma's press-and-hold (or Edit
    // Mode) still moves and resizes the widget.
    P5Support.DataSource {
        id: launcher
        engine: 'executable'
        onNewData: (source) => disconnectSource(source)
    }
    function run(action) {
        if (Command.monitor)
            launcher.connectSource(Command.monitor + ' --do ' + action)
        else    // not installed by Install-Linux.sh: at least open the app
            launcher.connectSource('gtk-launch prism-monitor')
    }
    function openSettings(tab) { run(tab ? 'settings:' + tab : 'settings') }

    readonly property var menuState: doc && doc.state ? doc.state : ({})
    function sectionOn(name) { return !!(menuState.sections && menuState.sections[name]) }

    Plasmoid.contextualActions: [
        PlasmaCore.Action { isSeparator: true; text: 'Show' },
        PlasmaCore.Action { text: 'FPS'; checkable: true; checked: root.sectionOn('fps')
            onTriggered: root.run('section:fps:' + (checked ? 'on' : 'off')) },
        PlasmaCore.Action { text: 'CPU / GPU / memory'; checkable: true; checked: root.sectionOn('system')
            onTriggered: root.run('section:system:' + (checked ? 'on' : 'off')) },
        PlasmaCore.Action { text: 'Fans'; checkable: true; checked: root.sectionOn('fans')
            onTriggered: root.run('section:fans:' + (checked ? 'on' : 'off')) },
        PlasmaCore.Action { text: 'Batteries'; checkable: true; checked: root.sectionOn('battery')
            onTriggered: root.run('section:battery:' + (checked ? 'on' : 'off')) },
        PlasmaCore.Action { isSeparator: true; text: 'Big number' },
        PlasmaCore.Action { text: 'CPU temperature'; checkable: true; checked: root.menuState.hero !== 'gpu'
            onTriggered: { checked = true; root.run('hero:cpu') } },
        PlasmaCore.Action { text: 'GPU temperature'; checkable: true; checked: root.menuState.hero === 'gpu'
            onTriggered: { checked = true; root.run('hero:gpu') } },
        PlasmaCore.Action { isSeparator: true },
        PlasmaCore.Action { text: 'Colours and fonts…'; icon.name: 'preferences-desktop-color'
            onTriggered: root.openSettings('Appearance') },
        PlasmaCore.Action { text: 'Keep awake…'; icon.name: 'system-suspend-inhibit'
            onTriggered: root.openSettings('awake') },
        PlasmaCore.Action { text: 'Start when I sign in'; checkable: true; checked: !!root.menuState.launch_at_login
            onTriggered: root.run('login:' + (checked ? 'on' : 'off')) },
        PlasmaCore.Action { text: 'Prism Monitor Settings…'; icon.name: 'configure'
            onTriggered: root.openSettings('') }
    ]
    // Plasma's own Configure dialog would be empty: the settings live in Prism Monitor.
    Component.onCompleted: {
        const configure = Plasmoid.internalAction('configure')
        if (configure)
            configure.visible = false
    }

    P5Support.DataSource {
        id: reader
        engine: 'executable'
        onNewData: (source, data) => {
            disconnectSource(source)
            try {
                const d = JSON.parse(data['stdout'])
                const maxAge = Math.max(5, 4 * (d.interval || 1))
                const fresh = d.version === 1 && Date.now() / 1000 - d.time <= maxAge
                if (fresh)
                    root.doc = d          // before `live`, so bindings never see live with no doc
                root.live = fresh
            } catch (e) {
                root.live = false     // no file: the program is not running
            }
        }
    }
    Timer {
        interval: 1000; running: true; repeat: true; triggeredOnStart: true
        onTriggered: if (!reader.connectedSources.length) reader.connectSource(root.command)
    }

    FontLoader { id: barlowBold; source: '../fonts/Barlow-Bold.ttf' }
    FontLoader { id: barlowSemi; source: '../fonts/Barlow-SemiBold.ttf' }
    readonly property string family: barlowBold.status === FontLoader.Ready ? barlowBold.name : Kirigami.Theme.defaultFont.family

    compactRepresentation: MouseArea {
        Layout.minimumWidth: compact.implicitWidth + Kirigami.Units.smallSpacing * 2
        onClicked: root.expanded = !root.expanded
        Text {
            id: compact
            anchors.centerIn: parent
            text: root.live ? root.doc.headline + ' ' + root.doc.number + root.doc.unit : '--'
            color: Kirigami.Theme.textColor
            font.family: root.family
            font.weight: Font.Bold
            font.pixelSize: Math.max(10, parent.height * 0.5)
        }
    }

    fullRepresentation: Item {
        id: full
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            cursorShape: Qt.PointingHandCursor
            onClicked: root.openSettings('')
        }
        Layout.preferredWidth: poster.implicitWidth
        Layout.preferredHeight: poster.implicitHeight
        Layout.minimumWidth: poster.implicitWidth * 0.4
        Layout.minimumHeight: poster.implicitHeight * 0.4

        Text {
            visible: !root.live
            anchors.centerIn: parent
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            text: 'Prism Monitor is not running.\nStart it from the app menu.'
            color: 'white'
            style: Text.Outline; styleColor: '#80000000'
            font.family: root.family
            font.pixelSize: 15
        }

        // The Poster is laid out at its natural size and scaled to fit the widget, so resizing
        // the widget on the desktop resizes the lettering.
        Item {
            visible: root.live
            width: poster.implicitWidth
            height: poster.implicitHeight
            scale: Math.min(full.width / Math.max(1, width), full.height / Math.max(1, height))
            transformOrigin: Item.TopLeft
            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: '#aa000000'
                shadowBlur: 0.5
                shadowVerticalOffset: 1.2
                shadowHorizontalOffset: 0
            }
            Poster { id: poster; doc: root.doc; family: root.family }
        }
    }

    component Poster: Item {
        id: p
        property var doc
        property string family
        readonly property real m: 46        // the Poster's "main size", as in poster.py
        readonly property real pad: 10
        readonly property color ink: doc && doc.text ? doc.text : '#ffffff'
        readonly property color accent: doc && doc.accent ? doc.accent : '#8fd8ff'
        readonly property var groups: doc ? doc.groups : []

        implicitWidth: Math.max(hero.x + hero.width, rows.x + rows.width) + pad
        implicitHeight: rows.y + rows.height + pad

        TextMetrics { id: eight; font: number.font; text: '8' }
        TextMetrics { id: wide; font: number.font; text: '88' }
        FontMetrics { id: numFm; font: number.font }

        // vertical caption, reading bottom to top
        Item {
            id: capBox
            x: p.pad; y: p.pad
            width: cap.implicitHeight + p.m * 0.22
            height: Math.max(numberBox.height, cap.implicitWidth)
            Text {
                id: cap
                text: p.doc ? p.doc.caption : ''
                color: p.ink; opacity: 0.9
                font.family: p.family; font.weight: Font.DemiBold
                font.pixelSize: p.m * 0.2; font.letterSpacing: p.m * 0.2 * 0.85
                rotation: -90
                transformOrigin: Item.TopLeft
                y: parent.height
            }
        }

        Item {
            id: hero
            x: capBox.x + capBox.width + p.m * 0.3
            y: p.pad
            width: numberBox.x + numberBox.width
            height: Math.max(numberBox.height, capBox.height, line.y + line.height)

            Text {
                id: headline
                text: p.doc ? p.doc.headline : ''
                color: p.ink
                font.family: p.family; font.weight: Font.Bold; font.pixelSize: p.m * 0.72
                y: numberBox.height * 0.38 - (headFm.ascent - headFm.capHeight) - 2
                FontMetrics { id: headFm; font: headline.font }
            }
            Text {
                id: subline
                text: p.doc ? p.doc.subline : ''
                color: p.ink
                font.family: p.family; font.weight: Font.Bold
                font.pixelSize: Math.max(11, p.m * 0.3); font.letterSpacing: 1.2
                y: headline.y + headline.height + p.m * 0.05
            }
            Rectangle {
                id: line
                y: subline.y + subline.height + p.m * 0.12
                width: Math.max(p.m * 1.1, subline.implicitWidth * 0.9)
                height: 1.6
                color: p.ink; opacity: 0.85
            }

            // big number, fading towards its base
            Item {
                id: numberBox
                x: Math.max(p.m * 1.4, headline.implicitWidth + p.m * 0.14)
                width: wide.advanceWidth + (unit.text ? unit.implicitWidth + p.m * 0.06 : 0)
                height: eight.tightBoundingRect.height
                Text {
                    id: number
                    visible: false
                    layer.enabled: true
                    text: p.doc ? p.doc.number : ''
                    color: p.accent
                    font.family: p.family; font.weight: Font.Bold; font.pixelSize: p.m * 2.2
                    x: (wide.advanceWidth - implicitWidth) / 2
                    y: -(numFm.ascent + eight.tightBoundingRect.y)
                }
                Rectangle {
                    id: fade
                    visible: false
                    layer.enabled: true
                    anchors.fill: number
                    gradient: Gradient {
                        GradientStop { position: (numFm.ascent + eight.tightBoundingRect.y) / fade.height; color: '#ffffffff' }
                        GradientStop { position: (numFm.ascent + eight.tightBoundingRect.y + 0.45 * numberBox.height) / fade.height; color: '#e1ffffff' }
                        GradientStop { position: numFm.ascent / fade.height; color: '#1cffffff' }
                    }
                }
                MultiEffect {
                    source: number
                    anchors.fill: number
                    maskEnabled: true
                    maskSource: fade
                }
                Text {
                    id: unit
                    text: p.doc ? p.doc.unit : ''
                    color: p.accent; opacity: 0.95
                    font.family: p.family; font.weight: Font.Bold; font.pixelSize: p.m * 0.62
                    x: (wide.advanceWidth + number.implicitWidth) / 2 + p.m * 0.06
                    y: -(unitFm.ascent + unitC.tightBoundingRect.y)   // cap top level with the number's top
                    FontMetrics { id: unitFm; font: unit.font }
                    TextMetrics { id: unitC; font: unit.font; text: 'C' }
                }
            }
        }

        // rows: SYSTEM (CPU/GPU, memory, swap), FANS, BATTERIES
        Column {
            id: rows
            x: hero.x
            y: hero.y + hero.height + p.m * 0.35
            width: Math.max(widest, hero.width)
            property real widest: 0
            spacing: p.m * 0.22

            Repeater {
                model: p.groups.length
                delegate: Column {
                    id: groupItem
                    required property int index
                    readonly property var group: p.groups[index] || {caption: '', rows: []}
                    width: rows.width
                    spacing: 4
                    Text {
                        text: groupItem.group.caption
                        color: p.accent
                        font.family: p.family; font.weight: Font.Bold
                        font.pixelSize: p.m * 0.22; font.letterSpacing: p.m * 0.22 * 0.6
                    }
                    Repeater {
                        model: groupItem.group.rows.length
                        delegate: Item {
                            required property int index
                            readonly property var modelData: groupItem.group.rows[index] || {label: '', value: '', color: null, level: null}
                            width: rows.width
                            height: Math.max(label.implicitHeight, value.implicitHeight) + 3 + (modelData.level !== null ? 4 : 0)
                            readonly property real need: Math.min(label.implicitWidth, 13 * p.m * 0.3) + 22 + value.implicitWidth
                            onNeedChanged: if (need > rows.widest) rows.widest = need
                            Component.onCompleted: if (need > rows.widest) rows.widest = need
                            Text {
                                id: label
                                text: modelData.label
                                color: p.ink; opacity: 0.82
                                elide: Text.ElideRight
                                width: Math.min(implicitWidth, parent.width - value.implicitWidth - 16)
                                font.family: p.family; font.weight: Font.DemiBold
                                font.pixelSize: p.m * 0.3; font.letterSpacing: 1.4
                                anchors.baseline: value.baseline
                            }
                            Text {
                                id: value
                                anchors.right: parent.right
                                text: modelData.value
                                color: modelData.color || p.ink
                                font.family: p.family; font.weight: Font.Bold
                                font.pixelSize: p.m * 0.34; font.letterSpacing: 0.6
                            }
                            Rectangle {     // battery gauge under the value
                                visible: modelData.level !== null
                                anchors.right: parent.right
                                y: value.baselineOffset + 3
                                width: value.implicitWidth; height: 1.4
                                color: Qt.alpha(p.ink, 0.2)
                                Rectangle {
                                    width: parent.width * Math.max(0, Math.min(1, modelData.level || 0))
                                    height: parent.height
                                    color: modelData.color || p.ink
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
