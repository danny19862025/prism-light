# SPDX-License-Identifier: GPL-3.0-only
"""Make a test page that behaves like iCUE: plugins and properties arrive late
and sensor values change over time. Used to verify widgets update live."""
import sys
from pathlib import Path

FAKE = r'''<script>
// Like iCUE: property globals and plugins appear after the page has started.
setTimeout(function () {
  function Signal() { var fns = []; return { connect: function (f) { fns.push(f); }, emit: function () { var a = arguments; fns.forEach(function (f) { f.apply(null, a); }); } }; }
  var values = { 'cpu-id': 40, 'gpu-id': 30, 'cpuload-id': 10, 'gpuload-id': 20 };
  var S = { asyncResponse: Signal(), sensorValueChanged: Signal(), sensorDataChanged: Signal(), sensorAdded: Signal(), sensorRemoved: Signal(), sensorUnitsChanged: Signal(),
    getSensorValue: function (req, id) { setTimeout(function () { S.asyncResponse.emit(req, String(values[id])); }, 5); } };
  var F = { asyncResponse: Signal(), fpsUpdated: Signal(), fpsAvailabilityChanged: Signal(), processChanged: Signal(),
    getCurrentFps: function (req) { setTimeout(function () { F.asyncResponse.emit(req, 90); }, 5); } };
  window.cpuTempSensor = 'cpu-id'; window.gpuTempSensor = 'gpu-id'; window.cpuLoadSensor = 'cpuload-id'; window.gpuLoadSensor = 'gpuload-id';
  window.plugins = { Sensorsdataprovider: S, Fpsdataprovider: F };
  window.iCUE_initialized = true;
  var tick = 0;
  setInterval(function () {
    tick++;
    values['cpu-id'] = 40 + tick; values['gpu-id'] = 30 + tick * 2;
    S.sensorValueChanged.emit('cpu-id', values['cpu-id']);   // live push, like iCUE
    F.fpsUpdated.emit(90 + tick); F.processChanged.emit('Harness Game');
    window.__harnessTick = tick;
  }, 500);
}, 1500);
</script>
'''

source, target = Path(sys.argv[1]), Path(sys.argv[2])
html = source.read_text(encoding='utf-8').replace('<body>', '<body>\n' + FAKE, 1)
target.write_text(html, encoding='utf-8')
print('harness written')
