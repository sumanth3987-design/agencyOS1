import { test, expect } from '@playwright/test';

test.describe('AgencyOS Competitive SaaS Workflows & E2E Verification', () => {

  async function setupFreshClient(page: any) {
    const uuid = Math.random().toString(36).substring(2, 10);
    const email = `client_${uuid}@agencyos.local`;
    const password = 'ClientSecurePass123!';

    await page.goto('/register');
    await page.fill('input[name="name"]', 'Automated Client');
    await page.fill('input[name="email"]', email);
    await page.fill('input[name="password"]', password);
    
    const accountType = page.locator('select[name="account_type"]');
    if (await accountType.isVisible()) {
      await accountType.selectOption('customer');
    }
    
    await page.click('button[type="submit"]');
    await page.waitForURL('**/login*'); 

    await page.fill('input[name="email"]', email);
    await page.fill('input[name="password"]', password);
    await page.click('button[type="submit"]');
    
    await page.waitForURL('**/dashboard*');
    return email;
  }

  test('Client completes dynamic intake wizard and transitions to SOW contract', async ({ page }) => {
    await setupFreshClient(page);
    await page.goto('/start-project/website-development');
    await expect(page.locator('h1')).toContainText('Project kickoff intake');

    await page.selectOption('select#package', 'Business Website');
    await page.selectOption('select#currency', 'INR');
    await page.fill('input#brand_name', 'Acme Global Ventures');
    await page.fill('input#industry', 'Logistics & AI');
    await page.click('#nextStepBtn');

    await page.selectOption('select#deadline', 'Standard Sprint (14 Days)');
    await page.fill('input#brand_colors', '#09090B, #6366F1');
    await page.click('#nextStepBtn');

    await page.fill('input#required_skills', 'Python, Flask, React, Tailwind CSS');
    await page.fill('textarea#brief', 'Deploy full-stack multi-tenant platform.');
    await page.click('#submitBtn');
    await page.waitForURL('**/contract');

    await expect(page.locator('h1')).toContainText(/(Master Service SOW|Statement of Work)/i);
  });

  test('Digital SOW signature records audit credentials and activates kickoff gate', async ({ page }) => {
    await setupFreshClient(page);

    await page.goto('/start-project/website-development');
    await page.fill('input#brand_name', 'Signature Test Corp');
    await page.fill('input#industry', 'Fintech');
    await page.click('#nextStepBtn');
    await page.click('#nextStepBtn');
    await page.fill('input#required_skills', 'Python');
    await page.fill('textarea#brief', 'Signature verification test brief.');
    
    await page.click('#submitBtn');
    await page.waitForURL('**/contract');

    const canvas = page.locator('#signatureCanvas');
    await expect(canvas).toBeVisible();
    await canvas.scrollIntoViewIfNeeded();
    await page.waitForTimeout(500); // Give browser time to finalize rendering

    const box = await canvas.boundingBox();
    if (box) {
      await page.mouse.move(box.x + 40, box.y + 40);
      await page.mouse.down();
      await page.mouse.move(box.x + 180, box.y + 50);
      await page.mouse.up();
    }

    await page.check('input[name="agreed_terms"]');
    await page.fill('input[name="signer_name"]', 'Authorized Executive');
    await page.fill('input[name="signer_title"]', 'Managing Director & CEO');

    await page.click('button[type="submit"]');
    await page.waitForURL('**/project/*');
    await expect(page.locator('body')).toContainText(/digitally signed/i);
  });

  test('Interactive client notification center renders panel', async ({ page }) => {
    await setupFreshClient(page);
    const bellBtn = page.locator('[data-notification-toggle]');
    await expect(bellBtn).toBeVisible();
    await bellBtn.click();
    const panel = page.locator('#client-notification-panel');
    await expect(panel).toBeVisible();
    await expect(panel.locator('h2')).toContainText('Action & Audit Dispatch');
  });

});