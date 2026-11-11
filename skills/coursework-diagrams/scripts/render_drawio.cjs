// Render an existing workbook with a local Draw.io viewer bundle.
const fs = require('node:fs/promises');
const path = require('node:path');

function loadPlaywright() {
  if (process.env.DRAWIO_PLAYWRIGHT) {
    try {
      return require(process.env.DRAWIO_PLAYWRIGHT);
    } catch (error) {
      throw new Error(`Не удалось загрузить DRAWIO_PLAYWRIGHT: ${error.message}`);
    }
  }
  const candidates = [
    'playwright',
    path.resolve(path.dirname(process.execPath), '..', 'node_modules', 'playwright'),
  ];
  for (const candidate of candidates) {
    let resolved;
    try {
      resolved = require.resolve(candidate);
    } catch (error) {
      if (error.code === 'MODULE_NOT_FOUND') continue;
      throw error;
    }
    return require(resolved);
  }
  throw new Error('Playwright не найден. Используй Node.js из среды Codex или задай DRAWIO_PLAYWRIGHT путём к установленному модулю playwright.');
}

async function findBrowser(chromium, explicitPath) {
  if (explicitPath) {
    try {
      await fs.access(explicitPath);
    } catch {
      throw new Error(`Браузер не найден по указанному пути: ${explicitPath}`);
    }
    return explicitPath;
  }
  const candidates = [chromium.executablePath()];
  for (const root of [process.env.PROGRAMFILES, process.env['PROGRAMFILES(X86)'], process.env.LOCALAPPDATA].filter(Boolean)) {
    candidates.push(
      path.join(root, 'Google', 'Chrome', 'Application', 'chrome.exe'),
      path.join(root, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    );
  }
  candidates.push('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser');
  for (const candidate of candidates) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch (error) {
      if (error.code !== 'ENOENT' && error.code !== 'ENOTDIR') throw error;
    }
  }
  throw new Error('Браузер не найден. Установи Chromium для Playwright, Chrome или Edge либо передай путь четвёртым аргументом.');
}

async function main() {
  const [file, output, viewerArgument, browserArgument] = process.argv.slice(2);
  if (!file || !output) {
    throw new Error('Usage: node render_drawio.cjs FILE.drawio OUTPUT_DIR [VIEWER.js] [BROWSER_EXE]');
  }
  const viewer = viewerArgument || path.join(__dirname, '..', 'assets', 'viewer-static.min.js');
  const { chromium } = loadPlaywright();
  const xml = await fs.readFile(file, 'utf8');
  try {
    await fs.access(viewer);
  } catch {
    throw new Error(`Просмотрщик Draw.io не найден: ${viewer}. Восстанови assets/viewer-static.min.js или передай путь третьим аргументом.`);
  }
  const executablePath = await findBrowser(chromium, browserArgument);
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ executablePath, args: ['--disable-lcd-text'] });
  try {
    const context = await browser.newContext({
      viewport: { width: 2600, height: 1800 },
      serviceWorkers: 'block',
    });
    await context.route(/^https?:/i, route => route.abort());
    const page = await context.newPage();
    const count = await page.evaluate(data => {
      const root = new DOMParser().parseFromString(data, 'application/xml').documentElement;
      if (root.localName === 'mxGraphModel') return 1;
      if (root.localName !== 'mxfile') return 0;
      return Array.from(root.children).filter(child => child.localName === 'diagram').length;
    }, xml);
    if (!count) throw new Error('No Draw.io pages found');
    for (let index = 0; index < count; index++) {
      await page.goto('about:blank');
      await page.setContent(
        '<!doctype html><meta charset="utf-8"><style>body{margin:16px}.mxgraph{min-height:50px}</style>'+
        '<div class="mxgraph"></div>',
      );
      await page.evaluate(({ xml, index }) => {
        document.querySelector('.mxgraph').setAttribute('data-mxgraph', JSON.stringify({
          xml, page: index, nav: false, lightbox: false, resize: true, 'check-visible-state': false,
        }));
      }, { xml, index });
      await page.addScriptTag({ path: path.resolve(viewer) });
      const graph = page.locator('.mxgraph');
      await graph.locator('svg').first().waitFor({ state: 'attached', timeout: 15000 });
      await page.evaluate(() => document.fonts.ready);
      const size = await graph.boundingBox();
      if (!size || size.width < 1 || size.height < 1) throw new Error(`Page ${index+1} is empty`);
      const png = path.join(output, `page-${String(index+1).padStart(2, '0')}.png`);
      await graph.screenshot({ path: png });
      process.stdout.write(`Page ${index+1} -> ${png}\n`);
    }
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
