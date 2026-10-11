/* Optional browser check: npm install --no-save playwright in a separate dev environment. */
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require('playwright');

(async () => {
  const file = path.resolve(process.argv[2] || '.balecontext/graph.html');
  const browser = await chromium.launch({headless: true, ...(process.env.BALECONTEXT_BROWSER ? {executablePath: process.env.BALECONTEXT_BROWSER} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
    const errors = [], remote = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {if (/^https?:/.test(request.url())) remote.push(request.url());});
    await page.goto(pathToFileURL(file).href);
    await page.locator('.node').first().waitFor();
    await page.waitForTimeout(4000);
    assert.equal(await page.title(), 'BaleContext · Project atlas');
    await page.locator('.node').first().focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#details').isVisible(), true);
    await page.locator('#search').fill('zqx-no-match');
    assert.equal(await page.locator('.node').count(), 0);
    assert.equal(await page.locator('#empty').isVisible(), true);
    await page.locator('#search').fill('');
    await page.locator('[data-kind="file"]').click();
    assert.ok(await page.locator('.node').count() > 0);
    await page.locator('[data-kind="all"]').click();
    await page.locator('#zoomIn').click();
    await page.locator('#reset').click();
    await page.waitForTimeout(4000);
    const keySymbol = page.locator('.node[aria-label="Project.index"]');
    if (await keySymbol.count()) { await keySymbol.focus(); await page.keyboard.press('Enter'); }
    await page.screenshot({path: path.join(path.dirname(file), 'preview.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'Mobile viewport must not overflow horizontally');
    assert.deepEqual(errors, []);
    assert.deepEqual(remote, []);
    console.log(JSON.stringify({viewer: 'passed', consoleErrors: errors.length, networkRequests: remote.length, screenshot: path.join(path.dirname(file), 'preview.png')}));
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
