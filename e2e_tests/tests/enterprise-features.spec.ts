import { test, expect } from '@playwright/test';

test.describe('AgencyOS Enterprise SaaS Feature Verification', () => {

  // Helper to register a fresh, unique user and wait for DB commit
  async function setupFreshClient(page: any) {
    const uuid = Math.random().toString(36).substring(2, 10);
    const email = `client_${uuid}@agencyos.local`;
    const password = 'CustomerSecurePass123!';

    await page.goto('/register');
    await page.fill('input[name="name"]', 'E2E Enterprise Client');
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

  test('Client draws on digital signature canvas and executes binding SOW contract', async ({ page }) => {
    await setupFreshClient(page);

    // Commission a new project to reach contract signing page
    await page.goto('/start-project/website-development');
    await page.fill('input#brand_name', 'Canvas Signer Corp');
    await page.fill('input#industry', 'Design');
    await page.click('#nextStepBtn');
    await page.click('#nextStepBtn');
    await page.fill('input#required_skills', 'React, Canvas');
    await page.fill('textarea#brief', 'Canvas signature verification test.');
    
    await page.click('#submitBtn');
    await page.waitForURL('**/contract');

    // Verify we are on contract signing page
    await expect(page.locator('h1')).toContainText(/(Master Service SOW|Statement of Work)/i);

    // Draw on the HTML5 signature canvas
    const canvas = page.locator('#signatureCanvas');
    await expect(canvas).toBeVisible();
    await canvas.scrollIntoViewIfNeeded(); // Fix for Firefox mouse events missing the element

    const box = await canvas.boundingBox();
    if (box) {
      await page.mouse.move(box.x + 40, box.y + 40);
      await page.mouse.down();
      await page.mouse.move(box.x + 180, box.y + 50);
      await page.mouse.move(box.x + 280, box.y + 90);
      await page.mouse.up();
    }

    // Fill signer credentials and terms agreement
    await page.check('input[name="agreed_terms"]');
    await page.fill('input[name="signer_name"]', 'Victoria Sterling');
    await page.fill('input[name="signer_title"]', 'Vice President of Engineering');

    // Submit signature
    await page.click('button[type="submit"]');
    await page.waitForURL('**/project/*');

    // Verify redirected to workbench with contract executed
    await expect(page.locator('body')).toContainText(/digitally signed/i);
  });

});