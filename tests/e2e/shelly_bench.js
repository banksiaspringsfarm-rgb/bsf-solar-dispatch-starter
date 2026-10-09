// Bench for the Shelly hot-water driver: an MQTT broker + a simulated Shelly Gen2+ relay, and (optionally) a
// Select.live point file that follows the relay, so the real selectlive_bridge.py can feed the real dispatcher.
//
//   node shelly_bench.js --prefix glen-eden/booster1 --element-w 3550 [--port 1883] [--no-broker]
//                        [--point /path/point.json --scenario /path/scenario.json]
//
// The fake relay answers Switch.Set / Switch.GetStatus / Switch.SetConfig on <prefix>/rpc (replies to <src>/rpc),
// honours toggle_after as a real flip-back timer (each Set restarts it), sends NotifyStatus on <prefix>/events/rpc
// when the output changes, and prints one JSON line per event on stdout for the runner to assert on.
// scenario.json = {"pv_w":9000,"house_w":600,"soc":100,"batt_w":-100}; the point file's load_w adds element-w
// while the relay is ON (what the SP PRO would measure).
const fs = require('fs');
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf('--' + k); return i < 0 ? d : (args[i + 1] === undefined || args[i + 1].startsWith('--') ? true : args[i + 1]); };
const PORT = +opt('port', 1883), PREFIX = opt('prefix', 'shelly-test'), ELEMENT_W = +opt('element-w', 3550);
const POINT = opt('point', ''), SCENARIO = opt('scenario', '');
const log = (o) => process.stdout.write(JSON.stringify(Object.assign({ t: Date.now() }, o)) + '\n');

function startBroker() {
  const mod = require('aedes');
  const make = mod.Aedes && mod.Aedes.createBroker ? mod.Aedes.createBroker() : Promise.resolve((mod.default || mod)());   // aedes >=1 / <1
  return make.then((aedes) => new Promise((res) => {
    const srv = require('net').createServer(aedes.handle);
    srv.listen(PORT, () => { log({ ev: 'broker', port: PORT }); res(); });
  }));
}

const relay = { on: false, initial_state: 'restore_last', timer: null };
let client;
function notify() {
  client.publish(PREFIX + '/events/rpc', JSON.stringify({ src: PREFIX, dst: PREFIX + '/events', method: 'NotifyStatus',
    params: { ts: Date.now() / 1000, 'switch:0': { id: 0, output: relay.on, apower: relay.on ? ELEMENT_W : 0 } } }));
}
function setOutput(on, why) {
  const changed = relay.on !== on; relay.on = on;
  if (changed) { log({ ev: 'output', on, why }); notify(); }
}
function status() {
  return { id: 0, source: 'MQTT', output: relay.on, apower: relay.on ? ELEMENT_W : 0, voltage: 241.2, current: relay.on ? +(ELEMENT_W / 241.2).toFixed(2) : 0, temperature: { tC: 41.0 } };
}
function handleRpc(req) {
  const reply = (body) => client.publish(req.src + '/rpc', JSON.stringify(Object.assign({ id: req.id, src: PREFIX, dst: req.src }, body)));
  const p = req.params || {};
  log({ ev: 'rpc', method: req.method, params: p, src: req.src });
  if (p.id !== 0) return reply({ error: { code: -105, message: 'Argument \'id\', value ' + p.id + ' not found!' } });
  if (req.method === 'Switch.GetStatus') return reply({ result: status() });
  if (req.method === 'Switch.SetConfig') { if (p.config && p.config.initial_state) relay.initial_state = p.config.initial_state; return reply({ result: { restart_required: false } }); }
  if (req.method === 'Switch.Set') {
    const was = relay.on;
    if (relay.timer) { clearTimeout(relay.timer); relay.timer = null; }
    setOutput(!!p.on, 'Switch.Set');
    if (typeof p.toggle_after === 'number') {
      relay.timer = setTimeout(() => { relay.timer = null; log({ ev: 'flip_back', after_s: p.toggle_after }); setOutput(!relay.on, 'toggle_after'); }, p.toggle_after * 1000);
    }
    return reply({ result: { was_on: was } });
  }
  reply({ error: { code: -114, message: 'Method ' + req.method + ' failed: not implemented' } });
}

function writePoint() {
  if (!POINT || !SCENARIO) return;
  let s; try { s = JSON.parse(fs.readFileSync(SCENARIO, 'utf8')); } catch (e) { return; }
  const items = { battery_soc: s.soc, battery_w: s.batt_w, solarinverter_w: s.pv_w, shunt_w: 0, grid_w: 0,
                  load_w: s.house_w + (relay.on ? ELEMENT_W : 0), timestamp: Math.floor(Date.now() / 1000) };
  fs.writeFileSync(POINT + '.tmp', JSON.stringify({ device: { name: 'SP PRO (bench)' }, items })); fs.renameSync(POINT + '.tmp', POINT);
}

(async () => {
  if (!opt('no-broker', false)) await startBroker();
  client = require('mqtt').connect('mqtt://127.0.0.1:' + PORT, { clientId: 'fake-shelly-' + PREFIX.replace(/\//g, '-') });
  client.on('connect', () => { client.subscribe(PREFIX + '/rpc'); log({ ev: 'shelly_up', prefix: PREFIX }); });
  client.on('message', (topic, buf) => { try { handleRpc(JSON.parse(buf.toString())); } catch (e) { log({ ev: 'bad_rpc', err: String(e) }); } });
  // the runner reads relay.initial_state via this line on demand: SIGUSR2 -> dump
  process.on('SIGUSR2', () => log({ ev: 'state', on: relay.on, initial_state: relay.initial_state, timer: !!relay.timer }));
  writePoint(); setInterval(writePoint, 1000);
})();
