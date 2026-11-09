// 足跡で「希望」を書く縦型ショート (1080x1920, 30fps, 30s)
//   node render.cjs --still 26 [out.png]     ... 静止画
//   node render.cjs --video [out.mp4]        ... 動画
const TEXT = "希望";   // ← 差し替え可能。上から縦に並ぶ（KanjiVGにある字なら何でも）

const fs = require('fs'), path = require('path'), https = require('https');
const { spawn } = require('child_process');
const { chromium } = require('playwright');

const FPS = 30, DUR = 30, W = 1080, H = 1920;
const KVG_DIR = path.join(__dirname, 'kanjivg');

function get(url) {
  return new Promise((res, rej) => https.get(url, r => {
    if (r.statusCode >= 300 && r.statusCode < 400 && r.headers.location) return get(r.headers.location).then(res, rej);
    if (r.statusCode !== 200) return rej(new Error(url + ' ' + r.statusCode));
    let b = ''; r.setEncoding('utf8'); r.on('data', d => b += d); r.on('end', () => res(b));
  }).on('error', rej));
}
async function kanjivg(ch) {
  const hex = ch.codePointAt(0).toString(16).padStart(5, '0');
  const f = path.join(KVG_DIR, hex + '.svg');
  if (!fs.existsSync(f)) {
    fs.mkdirSync(KVG_DIR, { recursive: true });
    fs.writeFileSync(f, await get(`https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji/${hex}.svg`));
  }
  const svg = fs.readFileSync(f, 'utf8');
  // stroke order = document order of <path id="kvg:XXXXX-sN">
  const strokes = [];
  for (const m of svg.matchAll(/<path\b[^>]*>/g)) {
    const tag = m[0]; const id = /id="kvg:[0-9a-f]+-s(\d+)"/.exec(tag); if (!id) continue;
    strokes.push({ n: +id[1], d: /\sd="([^"]+)"/.exec(tag)[1], type: (/kvg:type="([^"]*)"/.exec(tag) || [])[1] || '' });
  }
  strokes.sort((a, b) => a.n - b.n);
  return { ch, strokes };
}

async function main() {
  const args = process.argv.slice(2);
  const mode = args.includes('--video') ? 'video' : 'still';
  const chars = [];
  for (const ch of Array.from(TEXT)) chars.push(await kanjivg(ch));
  const data = { chars, seed: 20261109 };
  const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const newPage = async () => {
    const p = await browser.newPage(); await p.setContent('<!doctype html><html><body></body></html>');
    await p.addScriptTag({ path: path.join(__dirname, 'scene.js') });
    const info = await p.evaluate(d => window.initScene(d), data); return { p, info };
  };
  if (mode === 'still') {
    const i = args.indexOf('--still'); const t = parseFloat(args[i + 1] || '26');
    const out = args.find((a, k) => k > i + 1 && !a.startsWith('--')) || path.join(__dirname, `still_${t}s.png`);
    const { p, info } = await newPage(); console.log(info);
    const url = await p.evaluate(t => window.renderFrame(t, 'image/png'), t);
    fs.writeFileSync(out, Buffer.from(url.split(',')[1], 'base64')); console.log('wrote', out);
  } else {
    const out = args.find(a => a.endsWith('.mp4')) || path.join(__dirname, 'kibou.mp4');
    const NW = +(args[args.indexOf('--workers') + 1]) || 4; const N = FPS * DUR;
    const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
      '-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-pix_fmt', 'yuv420p', '-vf', 'scale=out_color_matrix=bt709:out_range=tv',
      '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-r', String(FPS), '-movflags', '+faststart', out], { stdio: ['pipe', 'inherit', 'inherit'] });
    const pages = await Promise.all(Array.from({ length: NW }, newPage)); console.log(pages[0].info);
    const done = new Map(); let next = 0, written = 0; const t0 = Date.now();
    const flush = async () => { while (done.has(written)) { const b = done.get(written); done.delete(written); if (!ff.stdin.write(b)) await new Promise(r => ff.stdin.once('drain', r)); written++; if (written % 60 === 0) console.log(`frame ${written}/${N}  ${((Date.now() - t0) / 1000).toFixed(0)}s`); } };
    await Promise.all(pages.map(async ({ p }) => {
      while (true) { const f = next++; if (f >= N) break;
        while (f - written > 90) await new Promise(r => setTimeout(r, 20));
        const url = await p.evaluate(t => window.renderFrame(t, 'image/jpeg', 0.97), f / FPS);
        done.set(f, Buffer.from(url.split(',')[1], 'base64')); await flush(); }
    }));
    ff.stdin.end(); await new Promise(r => ff.on('close', r)); console.log('wrote', out);
  }
  await browser.close();
}
main().catch(e => { console.error(e); process.exit(1); });
