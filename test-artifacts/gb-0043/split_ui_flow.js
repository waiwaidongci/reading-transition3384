// 真实浏览器流程：登录 → 多文集切分 → worker 完成 → 逐会话核验与导出。
// 用法：cd test-artifacts/gb-0043 && BASE=http://localhost:18247 node split_ui_flow.js
const { chromium } = require('playwright');

const BASE = process.env.BASE || 'http://localhost:18247';
const OUT = __dirname;
const PASSWORD = 'Research2026!';

async function login(page, email) {
  await page.goto(BASE);
  await page.waitForSelector('#login-form');
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('#login-form button[type="submit"]');
  await page.waitForSelector('nav');
}

function assert(condition, message) {
  if (!condition) throw new Error('断言失败：' + message);
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));

  await login(page, 'analyst@reading-transition.demo');
  await page.click('nav a[href="#splits"]');
  await page.waitForSelector('#split-form');
  const boxes = await page.locator('input[name="collection_ids"]').count();
  assert(boxes >= 2, '应能多选文集一起切分');
  // 只选三个演示文集（自然键 SY-2026...）；其他用例留下的极小文集会让切分无解。
  const labels = await page.locator('fieldset.checks label').allInnerTexts();
  for (let i = 0; i < labels.length; i++) {
    if (!labels[i].includes('SY-2026')) await page.locator('input[name="collection_ids"]').nth(i).uncheck();
  }

  await page.fill('input[name="name"]', '十一月验收切分');
  await page.click('#split-form button[type="submit"]');
  await page.waitForSelector('.run-title .status', { timeout: 20000 });
  await page.waitForTimeout(450);
  await page.screenshot({ path: OUT + '/split-runner.png', fullPage: true, animations: 'disabled' });

  for (let i = 0; i < 60; i++) {
    const state = (await page.locator('.run-title .status').innerText()).trim();
    if (['已完成', '已证实无解', '失败'].includes(state)) break;
    await page.waitForTimeout(1000);
    if (await page.locator('#refresh-split').count()) await page.click('#refresh-split');
  }
  const state = (await page.locator('.run-title .status').innerText()).trim();
  assert(state === '已完成', '切分应完成，实际 ' + state);

  const checks = await page.locator('ul.checks li').allInnerTexts();
  assert(checks.length >= 6 && checks.every(t => t.includes('通过')), '全部核验项应通过：' + checks.join(' / '));

  const perCollection = await page.locator('table').nth(0).innerText();
  for (const line of perCollection.split('\n').slice(1)) {
    const cells = line.trim().split(/\s+/);
    const train = Number(cells[cells.length - 3]);
    const validation = Number(cells[cells.length - 2]);
    assert(train >= 1 && validation >= 1, '每个文集两侧都有人：' + line);
    assert(Math.abs(train - validation) <= 1, '每个文集两侧人数最多差一：' + line);
  }

  const download = await Promise.all([page.waitForEvent('download'), page.click('#export-split')]);
  const file = await download[0].path();
  const lines = require('fs').readFileSync(file, 'utf8').trim().split(/\r?\n/);
  const header = lines[0].replace(/^﻿/, '').split(',');
  const sideIndex = header.indexOf('side_label');
  const pathIndex = header.indexOf('path');
  const seen = new Map();
  let leaks = 0;
  for (const line of lines.slice(1)) {
    const cells = line.split(',');
    const side = cells[sideIndex];
    const parts = cells[pathIndex].split('>');
    const keys = [parts.join('|')];
    for (let i = 0; i + 2 < parts.length; i++) keys.push('T:' + parts.slice(i, i + 3).join('>'));
    for (const key of keys) {
      if (seen.has(key) && seen.get(key) !== side) leaks++;
      seen.set(key, side);
    }
  }
  assert(leaks === 0, 'CSV 中同一路线/连续三章不应跨两侧，发现 ' + leaks);
  const trainRows = lines.slice(1).filter(l => l.includes('练习集')).length;
  assert(trainRows > 0 && trainRows < lines.length - 1, 'CSV 两侧都应有会话');
  await page.screenshot({ path: OUT + '/split-result-desktop.png', fullPage: true, animations: 'disabled' });

  await page.click('#logout');
  await page.waitForSelector('#login-form');
  await login(page, 'viewer@reading-transition.demo');
  await page.click('nav a[href="#splits"]');
  await page.waitForSelector('.ledger');
  await page.waitForSelector('.run-title .status', { timeout: 15000 });
  assert((await page.locator('#split-form').count()) === 0, 'viewer 不应看到新建表单');
  await page.waitForSelector('#export-split', { timeout: 15000 });
  assert((await page.locator('#export-split').count()) > 0, 'viewer 应能导出核验表');
  await page.screenshot({ path: OUT + '/split-viewer.png', fullPage: true, animations: 'disabled' });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert(overflow <= 0, '390 宽不应横向溢出，实际 ' + overflow + 'px');
  await page.screenshot({ path: OUT + '/split-mobile.png', fullPage: true, animations: 'disabled' });

  assert(errors.length === 0, '控制台不应报错：' + errors.join(' | '));
  console.log('OK', { checks: checks.length, trainRows, leaks, overflow });
  await browser.close();
})().catch(error => { console.error('FAILED', error.message); process.exit(1); });
