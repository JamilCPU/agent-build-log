const { test, expect } = require('@playwright/test');

const BASE = 'http://localhost:3456';

test('stats show agent commits', async ({ page }) => {
  await page.goto(BASE);
  await page.waitForSelector('#statTotal');

  const total = await page.locator('#statTotal').textContent();
  console.log('Total events:', total);
  expect(parseInt(total)).toBeGreaterThan(0);
});

test('no human commits visible', async ({ page }) => {
  await page.goto(BASE);
  await page.waitForSelector('.event-card', { timeout: 8000 });

  const humanBadges = await page.locator('.agent-badge:text("human")').count();
  console.log('Human badges visible:', humanBadges);
  expect(humanBadges).toBe(0);
});

test('agent commits show with agent badges', async ({ page }) => {
  await page.goto(BASE);
  await page.waitForSelector('.event-card', { timeout: 8000 });

  const cards = await page.locator('.event-card').count();
  console.log('Event cards:', cards);
  expect(cards).toBeGreaterThan(0);

  // Every card should have a non-human agent badge
  const firstAgent = await page.locator('.event-card').first().locator('.agent-badge').first().textContent();
  console.log('First agent:', firstAgent);
  expect(firstAgent.toLowerCase()).not.toBe('human');
});

test('sidebar lists agents and repos', async ({ page }) => {
  await page.goto(BASE);
  await page.waitForSelector('#agentFilters button', { timeout: 8000 });

  const agentFilters = await page.locator('#agentFilters button').count();
  const repoFilters = await page.locator('#repoFilters button').count();
  console.log('Agent filters:', agentFilters, '| Repo filters:', repoFilters);
  expect(agentFilters).toBeGreaterThan(0);
  expect(repoFilters).toBeGreaterThan(0);
});
