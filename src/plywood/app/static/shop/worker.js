// Runs the Python planner (plywood.app.shop) with Pyodide, off the page's thread.
// A module worker: started with new Worker('worker.js', { type: 'module' }).

import { loadPyodide } from 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs';

const ready = (async () => {
  const py = await loadPyodide();
  const zip = await (await fetch('plywood.zip')).arrayBuffer();
  py.unpackArchive(zip, 'zip', { extractDir: '/home/pyodide/lib' });
  py.runPython("import sys; sys.path.insert(0, '/home/pyodide/lib'); from plywood.app import shop");
  return py;
})();

ready.then(
  () => postMessage({ ready: true }),
  (err) => postMessage({ ready: false, error: String(err) }),
);

onmessage = async (e) => {
  const { id, method, args } = e.data;
  try {
    const py = await ready;
    py.globals.set('_method', method);
    py.globals.set('_args', JSON.stringify(args));
    postMessage({ id, result: JSON.parse(py.runPython('shop.call(_method, _args)')) });
  } catch (err) {
    postMessage({ id, result: { ok: false, message: String(err) } });
  }
};
