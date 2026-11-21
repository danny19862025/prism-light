// SPDX-License-Identifier: GPL-3.0-only
// Prism Light info blocks for iCUE pump LCD widgets (round 480x480).
// Original designs ported from prism/aio.py. Live data comes from iCUE's
// Sensors and FPS data-provider plugins; without them (e.g. in a browser
// preview) demo values are shown so the layout can be checked.
'use strict';

const PRISM = { cpuTemp: null, gpuTemp: null, cpuLoad: null, gpuLoad: null, fps: null, app: '' };
const LOADED = performance.now();
// iCUE injects window.plugins and the property globals a moment after the
// page starts, so "inside iCUE" must be decided late, never at load time.
// Showing preview numbers is only for a plain browser with no iCUE at all.
function insideIcue() {
	return !!window.plugins || typeof window.iCUE_initialized !== 'undefined' ||
		typeof window.pluginSensorsdataprovider_initialized !== 'undefined';
}
function demoMode() {
	return !insideIcue() && performance.now() - LOADED > 3000;
}
const SENSOR_KEYS = { cpuTempSensor: 'cpuTemp', gpuTempSensor: 'gpuTemp', cpuLoadSensor: 'cpuLoad', gpuLoadSensor: 'gpuLoad' };

// ------------------------------------------------------------ iCUE data
function prop(name, fallback) {
	try {
		const value = window[name];
		return value === undefined || value === null || value === '' ? fallback : value;
	} catch (e) { return fallback; }
}

let requestId = 1000;
const pending = {};

function readSensors() {
	const api = window.plugins && window.plugins.Sensorsdataprovider;
	if (!api) return;
	for (const [property, key] of Object.entries(SENSOR_KEYS)) {
		const id = prop(property, '');
		if (!id) { PRISM[key] = null; continue; }
		const req = requestId++;
		pending[req] = key;
		api.getSensorValue(req, id);
	}
}

function number(value) {
	const n = parseFloat(value);
	return Number.isFinite(n) ? n : null;
}

let sensorsHooked = false, fpsHooked = false;

function onSensorsReady() {
	const api = window.plugins && window.plugins.Sensorsdataprovider;
	if (!api || sensorsHooked) return;
	sensorsHooked = true;
	api.asyncResponse.connect(function (id, value) {
		const key = pending[id];
		if (key) { PRISM[key] = number(value); delete pending[id]; }
	});
	api.sensorValueChanged.connect(function (sensorId, value) {
		for (const [property, key] of Object.entries(SENSOR_KEYS)) {
			if (prop(property, '') === sensorId) PRISM[key] = number(value);
		}
	});
	readSensors();
	setInterval(readSensors, 2000);   // safety net in case a change signal is missed
}

function onFpsReady() {
	const api = window.plugins && window.plugins.Fpsdataprovider;
	if (!api || fpsHooked) return;
	fpsHooked = true;
	api.fpsUpdated.connect(function (fps) { PRISM.fps = fps > 0 ? fps : null; });
	api.fpsAvailabilityChanged.connect(function (on) { if (!on) PRISM.fps = null; });
	api.processChanged.connect(function (name) { PRISM.app = name || ''; });
	const req = requestId++;
	api.asyncResponse.connect(function (id, value) { if (id === req) PRISM.fps = value > 0 ? value : null; });
	api.getCurrentFps(req);
}

window.icueEvents = { onICUEInitialized: readSensors, onDataUpdated: readSensors };
window.pluginSensorsdataproviderEvents = { onInitialized: onSensorsReady };
window.pluginFpsdataproviderEvents = { onInitialized: onFpsReady };

function hookPlugins() {
	// Keep trying: the plugins can become ready before or after this script,
	// and a missed "initialized" event must not leave the widget frozen.
	try { if (window.plugins && window.plugins.Sensorsdataprovider) onSensorsReady(); } catch (e) {}
	try { if (window.plugins && window.plugins.Fpsdataprovider) onFpsReady(); } catch (e) {}
}
setInterval(hookPlugins, 1000);

// ------------------------------------------------------------ drawing kit
const TAU = Math.PI * 2;
const START = 135 * Math.PI / 180;        // gauge starts bottom-left
const SWEEP = 270 * Math.PI / 180;

function rgba(hex, a) {
	const h = String(hex).replace('#', '');
	const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h.slice(0, 6), 16) || 0;
	return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`;
}
function mix(a, b, f) {
	const pa = parseInt(String(a).slice(1), 16), pb = parseInt(String(b).slice(1), 16);
	const c = [16, 8, 0].map(s => Math.round(((pa >> s) & 255) + (((pb >> s) & 255) - ((pa >> s) & 255)) * Math.max(0, Math.min(1, f))));
	return `rgb(${c[0]},${c[1]},${c[2]})`;
}
function val(v, suffix) { return v === null || v === undefined ? '--' : Math.round(v) + suffix; }

function text(g, s, y, size, str, color, bold = true, x = 0.5) {
	g.fillStyle = color || '#ffffff';
	g.font = `${bold ? 700 : 400} ${Math.round(s * size)}px "Segoe UI", "Inter", sans-serif`;
	g.textAlign = 'center';
	g.textBaseline = 'middle';
	g.fillText(str, s * x, s * y);
}
function gauge(g, s, radius, width, value, color) {
	g.lineCap = 'round';
	g.lineWidth = s * width;
	g.strokeStyle = 'rgba(255,255,255,0.10)';
	g.beginPath(); g.arc(s / 2, s / 2, s * radius, START, START + SWEEP); g.stroke();
	if (value === null || value === undefined) return;
	g.strokeStyle = color;
	g.beginPath(); g.arc(s / 2, s / 2, s * radius, START, START + SWEEP * Math.max(0.001, Math.min(1, value / 100))); g.stroke();
}
function rule(g, s, y, x0 = 0.33, x1 = 0.67, alpha = 0.25) {
	g.strokeStyle = `rgba(255,255,255,${alpha})`;
	g.lineWidth = Math.max(1, s * 0.004);
	g.beginPath(); g.moveTo(s * x0, s * y); g.lineTo(s * x1, s * y); g.stroke();
}
function seeded(seed) {
	let x = seed;
	return () => { x = (x * 16807) % 2147483647; return (x - 1) / 2147483646; };
}

// ------------------------------------------------------------ blocks
const BLOCKS = {
	gameHud(g, s, t, c, d) {
		gauge(g, s, 0.43, 0.045, d.fps === null ? null : d.fps * 100 / 144, c.primary);
		text(g, s, 0.235, 0.055, 'FPS', c.primary, false);
		text(g, s, 0.38, 0.22, val(d.fps, ''));
		text(g, s, 0.52, 0.04, (d.app || 'no game detected').slice(0, 18), '#8d9cb3', false);
		text(g, s, 0.63, 0.075, val(d.cpuTemp, '°C'), '#ffffff', true, 0.33);
		text(g, s, 0.7, 0.04, 'CPU', c.primary, false, 0.33);
		text(g, s, 0.63, 0.075, val(d.gpuTemp, '°C'), '#ffffff', true, 0.67);
		text(g, s, 0.7, 0.04, 'GPU', c.secondary, false, 0.67);
	},
	dualGauge(g, s, t, c, d) {
		gauge(g, s, 0.43, 0.05, d.cpuTemp, c.primary);
		gauge(g, s, 0.33, 0.04, d.gpuTemp, c.secondary);
		text(g, s, 0.335, 0.045, 'CPU TEMP', c.primary, false);
		text(g, s, 0.43, 0.11, val(d.cpuTemp, '°C'));
		rule(g, s, 0.51);
		text(g, s, 0.59, 0.11, val(d.gpuTemp, '°C'));
		text(g, s, 0.685, 0.045, 'GPU TEMP', c.secondary, false);
	},
	singleGauge(g, s, t, c, d) {
		const grad = g.createConicGradient(START, s / 2, s / 2);
		grad.addColorStop(0, c.secondary); grad.addColorStop(0.75, c.primary); grad.addColorStop(1, c.secondary);
		gauge(g, s, 0.4, 0.07, d.cpuTemp, grad);
		text(g, s, 0.46, 0.22, val(d.cpuTemp, '°'));
		text(g, s, 0.72, 0.06, 'CPU TEMP', '#d6dcf0', false);
	},
	sensorBars(g, s, t, c, d) {
		const rows = [['CPU LOAD', d.cpuLoad, '%'], ['GPU LOAD', d.gpuLoad, '%'], ['CPU TEMP', d.cpuTemp, '°C'], ['GPU TEMP', d.gpuTemp, '°C']];
		rows.forEach(([name, v, suffix], i) => {
			const y = s * (0.24 + i * 0.155);
			g.textBaseline = 'middle';
			g.font = `400 ${Math.round(s * 0.048)}px "Segoe UI", sans-serif`; g.fillStyle = '#d6dcf0'; g.textAlign = 'left';
			g.fillText(name, s * 0.14, y);
			g.font = `700 ${Math.round(s * 0.052)}px "Segoe UI", sans-serif`; g.fillStyle = '#ffffff'; g.textAlign = 'right';
			g.fillText(val(v, suffix), s * 0.86, y);
			const by = y + s * 0.05, bh = s * 0.022, bw = s * 0.72;
			g.fillStyle = 'rgba(255,255,255,0.12)';
			g.beginPath(); g.roundRect(s * 0.14, by, bw, bh, bh / 2); g.fill();
			if (v !== null && v !== undefined) {
				g.fillStyle = mix(c.primary, c.secondary, i / 3);
				g.beginPath(); g.roundRect(s * 0.14, by, Math.max(bh, bw * Math.min(100, v) / 100), bh, bh / 2); g.fill();
			}
		});
	},
	ringReadout(g, s, t, c, d) {
		g.lineWidth = Math.max(1, s * 0.004);
		for (let i = 0; i < 7; i++) {
			const r = s * (0.31 + i * 0.022);
			const start = (t * (18 + i * 9) * (i % 2 ? 1 : -1)) * Math.PI / 180;
			g.strokeStyle = `rgba(255,255,255,${(60 + i * 22) / 255})`;
			g.beginPath(); g.arc(s / 2, s / 2, r, start, start + (250 - i * 15) * Math.PI / 180); g.stroke();
		}
		text(g, s, 0.34, 0.055, 'CPU TEMP', c.primary);
		text(g, s, 0.5, 0.24, val(d.cpuTemp, ''));
		g.strokeStyle = '#ffffff'; g.strokeRect(s * 0.45, s * 0.63, s * 0.1, s * 0.07);
		text(g, s, 0.665, 0.04, '°C');
	},
	hexPanels(g, s, t, c, d) {
		const rnd = seeded(9);
		for (let i = 0; i < 70; i++) {
			const x = (rnd() * s + t * s * 0.02 * (1 + rnd())) % s, y = rnd() * s;
			g.fillStyle = rgba(c.primary, 0.15 + rnd() * 0.5);
			g.beginPath(); g.arc(x, y, s * 0.004, 0, TAU); g.fill();
		}
		const tiles = [['CPU LOAD', d.cpuLoad, '%'], ['GPU LOAD', d.gpuLoad, '%'], ['CPU TEMP', d.cpuTemp, '°C'], ['GPU TEMP', d.gpuTemp, '°C']];
		tiles.forEach(([name, v, suffix], i) => {
			const cx = s * (i % 2 ? 0.7 : 0.3), cy = s * (i < 2 ? 0.3 : 0.7), r = s * 0.15;
			g.beginPath();
			for (let k = 0; k <= 6; k++) {
				const a = (60 * k - 30) * Math.PI / 180;
				k ? g.lineTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r) : g.moveTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
			}
			g.fillStyle = 'rgba(0,0,0,0.55)'; g.fill();
			g.strokeStyle = rgba(c.primary, 0.3); g.lineWidth = r * 0.04; g.stroke();
			if (v !== null && v !== undefined) {
				g.save(); g.setLineDash([r * 6 * Math.min(100, v) / 100, r * 7]);
				g.strokeStyle = c.primary; g.lineWidth = r * 0.07; g.lineCap = 'round'; g.stroke(); g.restore();
			}
			text(g, s, cy / s - 0.035, 0.032, name, '#cfd8e8', false, cx / s);
			text(g, s, cy / s + 0.02, 0.055, val(v, suffix), c.primary, true, cx / s);
		});
		if (c.text) text(g, s, 0.5, 0.06, c.text.slice(0, 12), c.secondary);
	},
	sweepGauge(g, s, t, c, d) {
		const grad = g.createConicGradient(Math.PI / 2, s / 2, s / 2);
		grad.addColorStop(0, c.secondary); grad.addColorStop(0.5, mix(c.primary, c.secondary, 0.5)); grad.addColorStop(1, c.primary);
		g.lineCap = 'round'; g.lineWidth = s * 0.06; g.strokeStyle = grad;
		g.beginPath(); g.arc(s / 2, s / 2, s * 0.44, 115 * Math.PI / 180, 425 * Math.PI / 180); g.stroke();
		const a = (t * 25 % 360) * Math.PI / 180 - Math.PI / 2;
		g.fillStyle = c.primary; g.beginPath(); g.arc(s / 2 + Math.cos(a) * s * 0.44, s / 2 + Math.sin(a) * s * 0.44, s * 0.035, 0, TAU); g.fill();
		if (c.text) text(g, s, 0.24, 0.06, c.text.slice(0, 14).toUpperCase());
		text(g, s, 0.44, 0.2, val(d.gpuTemp, '°'), '#ffffff', true, 0.31);
		text(g, s, 0.59, 0.06, 'GPU', '#ffffff', true, 0.31);
		g.strokeStyle = 'rgba(255,255,255,0.45)'; g.lineWidth = Math.max(1, s * 0.005);
		g.beginPath(); g.moveTo(s * 0.56, s * 0.36); g.lineTo(s * 0.56, s * 0.66); g.stroke();
		text(g, s, 0.39, 0.08, val(d.cpuTemp, '°'), '#ffffff', true, 0.73);
		text(g, s, 0.46, 0.04, 'CPU', '#ffffff', true, 0.73);
		text(g, s, 0.55, 0.08, val(d.fps, ''), '#ffffff', true, 0.73);
		text(g, s, 0.62, 0.04, 'FPS', '#ffffff', true, 0.73);
	},
	dotRingClock(g, s, t, c, d) {
		const now = new Date(), count = 60, head = (t * 10) % count;
		for (let i = 0; i < count; i++) {
			const a = TAU * i / count - Math.PI / 2, behind = (head - i + count) % count;
			const r = s * (behind > 8 ? 0.012 : 0.012 + 0.012 * (1 - behind / 8));
			g.fillStyle = behind < 8 ? c.secondary : 'rgba(255,255,255,0.8)';
			g.beginPath(); g.arc(s / 2 + Math.cos(a) * s * 0.43, s / 2 + Math.sin(a) * s * 0.43, r, 0, TAU); g.fill();
		}
		const hh = String(now.getHours()).padStart(2, '0'), mm = String(now.getMinutes()).padStart(2, '0');
		text(g, s, 0.38, 0.15, `${hh}:${mm}`, c.primary);
		text(g, s, 0.505, 0.055, 'CPU LOAD', c.primary, false);
		text(g, s, 0.62, 0.12, val(d.cpuLoad, '%'), c.primary);
	},
	boldSplit(g, s, t, c, d) {
		g.fillStyle = c.primary; g.fillRect(0, 0, s, s);
		g.strokeStyle = 'rgba(255,255,255,0.6)'; g.lineWidth = s * 0.012; g.lineCap = 'round';
		for (let i = 0; i < 24; i++) {
			const a = (i * 15 + t * 12) * Math.PI / 180, r0 = s * 0.38, r1 = s * (0.44 + 0.03 * Math.sin(t * 2 + i));
			g.beginPath(); g.moveTo(s / 2 + Math.cos(a) * r0, s / 2 + Math.sin(a) * r0); g.lineTo(s / 2 + Math.cos(a) * r1, s / 2 + Math.sin(a) * r1); g.stroke();
		}
		text(g, s, 0.275, 0.045, 'CPU TEMP', '#ffffff', false);
		text(g, s, 0.375, 0.13, val(d.cpuTemp, '°C'));
		rule(g, s, 0.48, 0.28, 0.72, 0.8);
		text(g, s, 0.535, 0.045, 'GPU TEMP', '#ffffff', false);
		text(g, s, 0.635, 0.13, val(d.gpuTemp, '°C'));
	},
};

// ------------------------------------------------------------ main loop
function startPrismWidget(blockName) {
	const canvas = document.getElementById('screen');
	const g = canvas.getContext('2d');
	const started = performance.now();
	function size() {
		const dpr = window.devicePixelRatio || 1;
		const side = Math.min(window.innerWidth, window.innerHeight) || 480;
		canvas.style.width = canvas.style.height = side + 'px';
		canvas.width = canvas.height = Math.round(side * dpr);
		return canvas.width;
	}
	let s = size();
	window.addEventListener('resize', () => { s = size(); });
	hookPlugins();
	function frame(now) {
		const t = (now - started) / 1000 * (prop('speed', 50) / 50);
		const colours = { primary: prop('accentColor', '#A89BFF'), secondary: prop('secondColor', '#45E0C2'), text: String(prop('label', '')) };
		const data = demoMode() ? { cpuTemp: 62, gpuTemp: 55, cpuLoad: 34, gpuLoad: 91, fps: 144, app: 'Preview' } : PRISM;
		g.setTransform(1, 0, 0, 1, 0, 0);
		g.fillStyle = prop('backgroundColor', '#000000');
		g.fillRect(0, 0, s, s);
		try { BLOCKS[blockName](g, s, t, colours, data); } catch (e) { text(g, s, 0.5, 0.04, 'Widget error: ' + e.message, '#ff8a8a', false); }
		requestAnimationFrame(frame);
	}
	requestAnimationFrame(frame);
}
