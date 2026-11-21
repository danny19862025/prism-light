// SPDX-License-Identifier: GPL-3.0-only
// Prism Monitor for GNOME Shell: draws the Poster that the Prism Monitor program publishes to
// $XDG_RUNTIME_DIR/prism-monitor/widget.json (see prism_monitor/publish.py) on the desktop,
// behind windows. The program reads the sensors; this extension only draws.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const MARGIN = 48;          // from the top-right corner of the primary monitor

function alpha(hex, a) {
    const m = /^#([0-9a-f]{6})$/i.exec(hex || '');
    if (!m)
        return `rgba(255,255,255,${a})`;
    const n = parseInt(m[1], 16);
    return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
}

function label(text, styleClass, style = '') {
    return new St.Label({text: text ?? '', style_class: styleClass, style});
}

export default class PrismMonitorExtension extends Extension {
    enable() {
        this._file = Gio.File.new_for_path(
            GLib.build_filenamev([GLib.get_user_runtime_dir(), 'prism-monitor', 'widget.json']));
        this._actor = new St.BoxLayout({style_class: 'prism-poster', vertical: true, reactive: false});
        Main.layoutManager._backgroundGroup.add_child(this._actor);
        this._monitorsId = Main.layoutManager.connect('monitors-changed', () => this._place());
        this._signature = null;
        this._render(null);
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
        this._refresh();
    }

    disable() {
        if (this._timer)
            GLib.source_remove(this._timer);
        this._timer = null;
        if (this._monitorsId)
            Main.layoutManager.disconnect(this._monitorsId);
        this._monitorsId = null;
        this._actor?.destroy();
        this._actor = null;
        this._file = null;
    }

    _place() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._actor)
            return;
        const [, width] = this._actor.get_preferred_width(-1);
        this._actor.set_position(monitor.x + monitor.width - width - MARGIN, monitor.y + MARGIN);
    }

    _refresh() {
        const file = this._file;
        file.load_contents_async(null, (f, result) => {
            if (file !== this._file)
                return;      // disabled meanwhile
            let doc = null;
            try {
                const [, bytes] = f.load_contents_finish(result);
                doc = JSON.parse(new TextDecoder().decode(bytes));
                const maxAge = Math.max(5, 4 * (doc.interval || 1));
                if (doc.version !== 1 || Date.now() / 1000 - doc.time > maxAge)
                    doc = null;
            } catch (e) {
                doc = null;  // no file: the program is not running
            }
            this._render(doc);
        });
    }

    _render(doc) {
        const box = this._actor;
        if (!box)
            return;
        box.destroy_all_children();
        if (!doc) {
            box.add_child(label('Prism Monitor is not running.\nStart it from the app menu.', 'prism-idle',
                'color: white;'));
            this._place();
            return;
        }
        const ink = doc.text || '#ffffff';
        const accent = doc.accent || '#8fd8ff';

        // hero: vertical caption, headline over subline and rule, then the big faded number
        const hero = new St.BoxLayout({vertical: false});
        const caption = label(doc.caption, 'prism-caption', `color: ${alpha(ink, 0.9)};`);
        const captionBin = new St.Widget({width: 18, height: 110});
        caption.set_pivot_point(0, 0);
        caption.rotation_angle_z = -90;
        caption.set_position(0, 110);
        captionBin.add_child(caption);
        hero.add_child(captionBin);

        const left = new St.BoxLayout({vertical: true, style: 'margin-left: 14px; padding-top: 16px;'});
        left.add_child(label(doc.headline, 'prism-headline', `color: ${ink};`));
        left.add_child(label(doc.subline, 'prism-subline', `color: ${ink};`));
        left.add_child(new St.Widget({style_class: 'prism-line', width: 56,
            style: `background-color: ${alpha(ink, 0.85)};`}));
        hero.add_child(left);

        const number = label(doc.number, 'prism-number', `color: ${alpha(accent, 0.55)}; margin-left: 8px;`);
        number.y_align = Clutter.ActorAlign.START;
        hero.add_child(number);
        if (doc.unit)
            hero.add_child(label(doc.unit, 'prism-unit', `color: ${alpha(accent, 0.95)};`));
        box.add_child(hero);

        // rows: SYSTEM (CPU/GPU, memory, swap), FANS, BATTERIES
        const rows = new St.BoxLayout({vertical: true, style: 'margin-left: 32px;'});
        for (const group of doc.groups || []) {
            rows.add_child(label(group.caption, 'prism-group', `color: ${accent};`));
            for (const row of group.rows) {
                const line = new St.BoxLayout({vertical: false, x_expand: true});
                const name = label(row.label, 'prism-label', `color: ${alpha(ink, 0.82)};`);
                name.x_expand = true;
                name.clutter_text.ellipsize = 3;     // Pango.EllipsizeMode.END
                line.add_child(name);
                const right = new St.BoxLayout({vertical: true, style: 'margin-left: 22px;'});
                right.add_child(label(row.value, 'prism-value', `color: ${row.color || ink};`));
                if (row.level !== null && row.level !== undefined) {
                    const gauge = new St.Widget({style_class: 'prism-gauge', x_expand: true,
                        style: `background-color: ${alpha(ink, 0.2)};`});
                    const fill = new St.Widget({style_class: 'prism-gauge',
                        style: `background-color: ${row.color || ink};`});
                    gauge.add_child(fill);
                    gauge.connect('notify::width', () =>
                        fill.set_width(gauge.width * Math.max(0, Math.min(1, row.level))));
                    right.add_child(gauge);
                }
                line.add_child(right);
                rows.add_child(line);
            }
        }
        box.add_child(rows);

        // Re-anchor to the corner only when the rows change, so the widget does not jump
        // as numbers change width.
        const signature = JSON.stringify((doc.groups || []).map(g => [g.caption, g.rows.map(r => r.label)]));
        if (signature !== this._signature) {
            this._signature = signature;
            this._place();
        }
    }
}
