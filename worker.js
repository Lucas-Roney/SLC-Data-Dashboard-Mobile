// Runs Python (the dashboard script itself) off the main thread so the page stays responsive.
import { loadPyodide } from './pyodide/pyodide.mjs';
let py = null, dashboardSource = '';
const post = (type, data) => postMessage(Object.assign({ type }, data || {}));
const PACKAGES = ['numpy', 'pandas', 'scipy', 'statsmodels', 'patsy', 'narwhals', 'packaging', 'python-dateutil', 'pytz', 'six'];

async function init() {
  post('status', { text: 'Starting Python', frac: 0.03 });
  py = await loadPyodide({ indexURL: new URL('pyodide/', self.location.href).href });
  post('status', { text: 'Loading data libraries', frac: 0.10 });
  await py.loadPackage(PACKAGES, { messageCallback: () => {}, errorCallback: () => {} });
  post('status', { text: 'Loading the charts library', frac: 0.30 });
  const wheel = await (await fetch('pyodide/plotly-7.1.0-py3-none-any.whl')).arrayBuffer();
  const site = py.runPython('import site; site.getsitepackages()[0]');
  py.unpackArchive(new Uint8Array(wheel), 'zip', { extractDir: site });
  dashboardSource = await (await fetch('tutoring_dashboard.py', { cache: 'no-cache' })).text();
  py.FS.mkdirTree('/work');
  post('ready', {});
}

function clearWork() {
  for (const f of py.FS.readdir('/work')) {
    if (f !== '.' && f !== '..') { try { py.FS.unlink('/work/' + f); } catch (e) {} }
  }
}

async function build(name, bytes) {
  const log = [];
  py.setStdout({ batched: (s) => log.push(s) });
  py.setStderr({ batched: (s) => log.push(s) });
  self.tutoringProgress = (frac, label) => post('progress', { frac, label });
  clearWork();
  py.FS.writeFile('/work/input.csv', new Uint8Array(bytes));
  py.FS.writeFile('/work/tutoring_dashboard.py', dashboardSource);
  let failure = '';
  try {
    await py.runPythonAsync(`
import os, sys, gc
os.environ["TUTORING_WEB"] = "1"
sys.argv = ["tutoring_dashboard", "/work/input.csv"]
_src = open("/work/tutoring_dashboard.py", encoding="utf-8").read()
_g = {"__name__": "__main__", "__file__": "/work/tutoring_dashboard.py"}
try:
    exec(compile(_src, "/work/tutoring_dashboard.py", "exec"), _g)
except SystemExit:
    pass
finally:
    _g.clear()
    gc.collect()
`);
  } catch (e) {
    failure = String(e).split('\n').slice(-4).join(' ');
  }
  let html = null;
  for (const f of ['tutoring_dashboard_mobile.html', 'tutoring_effectiveness_mobile.html']) {
    try { html = py.FS.readFile('/work/' + f, { encoding: 'utf8' }); break; } catch (e) {}
  }
  const messages = log.filter((l) => /ERROR|❌/.test(l)).map((l) => l.replace(/\x1b\[[0-9;]*m/g, '').trim());
  if (html) post('done', { html, name });
  else post('failed', { text: messages.join(' ') || failure || 'The file could not be turned into a dashboard.' });
  clearWork();
}

onmessage = async (e) => {
  const m = e.data;
  try {
    if (m.type === 'init') { await init(); }
    else if (m.type === 'build') { await build(m.name, m.bytes); }
  } catch (err) {
    post('failed', { text: 'Something went wrong: ' + String(err).slice(0, 300) });
  }
};
