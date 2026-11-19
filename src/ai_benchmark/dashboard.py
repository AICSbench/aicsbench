"""Loopback-only dashboard backed by the same comparison query as the CLI."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ai_benchmark.comparison import compare_trials
from ai_benchmark.errors import BenchmarkError
from ai_benchmark.models.enums import ComparisonDimension
from ai_benchmark.models.results import TrialResult
from ai_benchmark.reporting import trial_row
from ai_benchmark.storage.base import ResultFilter
from ai_benchmark.storage.json_store import JsonResultStore

PAGE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AICSBench results</title>
<style>
  body { font: 16px system-ui, sans-serif; max-width: 1100px; margin: 2rem auto;
         padding: 0 1rem; color: #17212b; background: #f8fafc; }
  h1 { margin-bottom: .3rem; } p { color: #4b5563; }
  .controls { display: flex; flex-wrap: wrap; gap: .8rem; margin: 1.5rem 0; }
  label { display: grid; gap: .2rem; font-weight: 600; }
  select { min-width: 145px; padding: .5rem; border: 1px solid #9ca3af;
           border-radius: .35rem; background: white; font: inherit; }
  table { width: 100%; border-collapse: collapse; background: white; }
  th, td { text-align: left; padding: .7rem; border-bottom: 1px solid #e5e7eb; }
  th { background: #eaf1f8; } .bar { height: .7rem; background: #147d7e; border-radius: .3rem; }
  .track { width: 120px; background: #e5e7eb; border-radius: .3rem; }
  #message { min-height: 1.5rem; } .error { color: #a11; }
  @media (max-width: 700px) { table { font-size: .85rem; } th, td { padding: .4rem; } }
</style>
<h1>AICSBench results</h1>
<p>Compare passing agent times across harnesses, stacks or models. Success includes every trial;
  timing excludes rate-limited and failed trials.</p>
<div class="controls">
  <label>Run <select id="run"></select></label>
  <label>Compare by <select id="by">
    <option value="stack">Stack</option><option value="language">Language</option>
    <option value="framework">Framework</option><option value="harness">Harness</option>
    <option value="harness_version">Harness version</option>
    <option value="harness_model">Harness + model</option>
    <option value="agent_model">Agent + model</option>
    <option value="agent">Agent</option><option value="model">Model</option>
    <option value="task">Task</option>
  </select></label>
  <label>Agent <select id="agent"></select></label>
  <label>Harness version <select id="harness_version"></select></label>
  <label>Model <select id="model"></select></label>
  <label>Stack <select id="stack"></select></label>
  <label>Task <select id="task"></select></label>
</div>
<p id="message" role="status"></p>
<table><thead><tr><th>Group</th><th>Passed</th><th>Success</th>
  <th>Mean agent time</th><th>Std dev</th><th>Mean cost</th><th>Confidence</th>
</tr></thead><tbody id="rows"></tbody></table>
<script>
const ids = ['run', 'by', 'agent', 'harness_version', 'model', 'stack', 'task'];
const el = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
const message = document.getElementById('message');
const rows = document.getElementById('rows');
let trials = [];
let trialGeneration = 0;
let compareGeneration = 0;
async function api(path) {
  const response = await fetch(path);
  const data = await response.json();
  if (!response.ok) throw Error(data.error || 'Request failed');
  return data;
}
function option(select, value, label) {
  const item = document.createElement('option');
  item.value = value; item.textContent = label; select.append(item);
}
function fillFilter(id) {
  const select = el[id], previous = select.value;
  select.replaceChildren(); option(select, '', 'All');
  const values = [...new Set(trials.map(row => row[id]).filter(Boolean))].sort();
  values.forEach(value => option(select, value, value));
  if (values.includes(previous)) select.value = previous;
}
async function loadTrials() {
  const current = ++trialGeneration;
  compareGeneration++;  // invalidate comparisons still in flight for the old run
  rows.replaceChildren(); message.textContent = ''; message.className = '';
  try {
    const loaded = el.run.value
      ? await api('/api/trials?run=' + encodeURIComponent(el.run.value))
      : [];
    if (current !== trialGeneration) return;  // a newer run selection took over
    trials = loaded;
    ['agent', 'harness_version', 'model', 'stack', 'task'].forEach(fillFilter);
    alignFilters();
  } catch (error) {
    if (current !== trialGeneration) return;
    trials = [];  // never show the previous run's rows under a new selection
    ['agent', 'harness_version', 'model', 'stack', 'task'].forEach(fillFilter);
    message.textContent = error.message; message.className = 'error';
    return;
  }
  await update();
}
const fixedBy = {
  stack: ['agent', 'model'], language: ['agent', 'model'], framework: ['agent', 'model'],
  agent: ['model', 'stack'], harness: ['model', 'stack'],
  harness_version: ['agent', 'model', 'stack'],
  model: ['agent', 'stack'],
  agent_model: ['stack'], harness_model: ['stack'],
  task: ['agent', 'model', 'stack'],
};
function alignFilters() {
  const needs = fixedBy[el.by.value] || ['stack'];
  for (const id of needs) {
    if (el[id].value) continue;
    const subset = trials.filter(row => needs.every(other =>
      other === id || !el[other].value || row[other] === el[other].value));
    const values = [...new Set(subset.map(row => row[id]).filter(Boolean))].sort();
    if (id === 'stack') {
      values.sort((a, b) => subset.filter(row => row.stack === b).length -
                            subset.filter(row => row.stack === a).length);
    }
    if (values.length > 1) el[id].value = values[0];
  }
}
function cell(row, value) {
  const td = document.createElement('td'); td.textContent = value; row.append(td); return td;
}
async function update() {
  const current = ++compareGeneration;
  rows.replaceChildren(); message.textContent = ''; message.className = '';
  if (!el.run.value) { message.textContent = 'No runs found.'; return; }
  const query = new URLSearchParams({run: el.run.value, by: el.by.value});
  for (const id of ['agent', 'harness_version', 'model', 'stack', 'task']) {
    if (el[id].value) query.set(id, el[id].value);
  }
  try {
    const data = await api('/api/compare?' + query);
    if (current !== compareGeneration) return;  // a newer selection took over
    if (!data.groups.length) { message.textContent = 'No matching trials.'; return; }
    for (const group of data.groups) {
      const tr = document.createElement('tr');
      cell(tr, group.value);
      cell(tr, group.passed + '/' + group.trials);
      const success = cell(tr, Math.round(100 * group.passed / group.trials) + '%');
      const track = document.createElement('div'), bar = document.createElement('div');
      track.className = 'track'; bar.className = 'bar';
      bar.style.width = (100 * group.passed / group.trials) + '%';
      track.append(bar); success.append(track);
      cell(tr, group.mean_seconds == null ? 'n/a' : group.mean_seconds.toFixed(2) + ' s');
      cell(tr, group.stdev_seconds == null ? 'n/a' : group.stdev_seconds.toFixed(2) + ' s');
      cell(tr, group.mean_cost_usd == null ? 'n/a' : '$' + group.mean_cost_usd.toFixed(4));
      cell(tr, group.low_confidence ? 'Low sample' : 'OK');
      rows.append(tr);
    }
    message.textContent = data.groups.length + ' group(s); ' +
      'mean time uses passing trials without rate-limit events.';
  } catch (error) {
    if (current !== compareGeneration) return;
    message.textContent = error.message; message.className = 'error';
  }
}
async function init() {
  try {
    const runs = await api('/api/runs');
    runs.forEach(run => option(el.run, run.run_id, run.run_id + ' (' + run.trials + ' trials)'));
    await loadTrials();
  } catch (error) { message.textContent = error.message; message.className = 'error'; }
}
el.run.addEventListener('change', () => {
  loadTrials().catch(error => {
    message.textContent = error.message; message.className = 'error';
  });
});
el.by.addEventListener('change', () => {
  ['agent', 'model', 'stack'].forEach(id => { el[id].value = ''; });
  alignFilters(); update();
});
ids.filter(id => !['run', 'by'].includes(id))
  .forEach(id => el[id].addEventListener('change', update));
init();
</script>
</html>
"""


def dashboard_handler(store: JsonResultStore) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                status, content_type, body = dashboard_response(store, self.path)
            except (BenchmarkError, ValueError, OSError) as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._send(body, content_type, status)

        def _json(self, value: object, status: int = 200) -> None:
            self._send(json.dumps(value, allow_nan=False).encode(), "application/json", status)

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Handler


def dashboard_response(store: JsonResultStore, target: str) -> tuple[int, str, bytes]:
    request = urlsplit(target)
    query = {key: values[-1] for key, values in parse_qs(request.query).items()}
    if request.path == "/":
        return 200, "text/html; charset=utf-8", PAGE.encode()
    data: object
    if request.path == "/api/runs":
        data = [
            {"run_id": run.run_id,
             "trials": sum(1 for _ in store.iter_trials(ResultFilter(run_id=run.run_id)))}
            for run in store.list_runs()
        ]
    elif request.path == "/api/trials":
        data = [trial_row(item) for item in _trials(store, query)]
    elif request.path == "/api/compare":
        dimension = ComparisonDimension(query.get("by", "stack"))
        groups = compare_trials(_trials(store, query), dimension)
        data = {"compare_by": dimension.value, "groups": [asdict(group) for group in groups]}
    else:
        return 404, "application/json", b'{"error":"not found"}'
    return 200, "application/json", json.dumps(data, allow_nan=False).encode()


def _trials(store: JsonResultStore, query: dict[str, str]) -> list[TrialResult]:
    run_id = query.get("run")
    if not run_id:
        raise ValueError("choose a run")
    trials = list(store.iter_trials(ResultFilter(run_id=run_id)))
    fields: dict[str, Callable[[TrialResult], str]] = {
        "agent": lambda item: item.agent.name,
        "harness_version": lambda item: item.agent.version or "",
        "model": lambda item: item.agent.model_label,
        "stack": lambda item: item.environment.stack,
        "task": lambda item: item.task_id,
    }
    for key, get_value in fields.items():
        if query.get(key):
            trials = [item for item in trials if get_value(item) == query[key]]
    return trials


def serve_dashboard(results_dir: Path, port: int = 8765) -> None:
    with ThreadingHTTPServer(
        ("127.0.0.1", port), dashboard_handler(JsonResultStore(results_dir))
    ) as server:
        print(f"AICSBench dashboard: http://127.0.0.1:{server.server_port}")
        server.serve_forever()
