// 极简 CDP 截图: 连上 headless Chrome, 设定手机视口, 打开页面, 执行可选 JS, 截图。
// 用法: node cdp_shot.js <url> <out.png> [waitMs] [js...]
const [, , url, out, waitArg, ...steps] = process.argv;
const WAIT = +(waitArg || 1800);

async function targets() {
  const r = await fetch('http://127.0.0.1:9333/json/list');
  return r.json();
}
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  let list = [];
  for (let i = 0; i < 40; i++) {
    try { list = await targets(); if (list.length) break; } catch (e) {}
    await sleep(250);
  }
  const page = list.find(t => t.type === 'page');
  if (!page) { console.error('no page target'); process.exit(2); }
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const waits = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && waits.has(m.id)) { waits.get(m.id)(m); waits.delete(m.id); }
  };
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id; waits.set(i, (m) => m.error ? rej(new Error(method + ': ' + JSON.stringify(m.error))) : res(m.result));
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  console.log('ws open');
  await send('Page.enable');
  console.log('page enabled');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride',
    { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await send('Page.navigate', { url });
  console.log('navigated');
  await sleep(WAIT);
  for (const s of steps) {
    const r = await send('Runtime.evaluate', { expression: s, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) console.error('EVAL ERROR:', JSON.stringify(r.exceptionDetails).slice(0, 300));
    else if (r.result && r.result.value !== undefined) console.log('eval:', String(r.result.value).slice(0, 200));
    await sleep(700);
  }
  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  require('fs').writeFileSync(out, Buffer.from(shot.data, 'base64'));
  console.log('saved', out);
  ws.close();
  process.exit(0);
})();
