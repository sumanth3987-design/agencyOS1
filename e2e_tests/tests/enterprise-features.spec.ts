import { test, expect } from '@playwright/test';

test.describe('AgencyOS Enterprise SaaS Feature Verification', () => {

  test('Client draws on digital signature canvas and executes binding SOW contract', async ({ page }) => {
    // 1. Log in as client
    await page.goto('/login');
    await page.fill('input[name="email"]', 'client@example.com');
    await page.fill('input[name="password"]', 'ClientSecurePass123!');
    await page.click('button[type="submit"]');

    // 2. Navigate to project contract page
    await page.goto('/project/1/contract');
    await expect(page.locator('h1')).toContainText('Statement of Work (SOW)');

    // 3. Draw on the HTML5 signature canvas
    const canvas = page.locator('#signatureCanvas');
    await expect(canvas).toBeVisible();

    const box = await canvas.boundingBox();
    if (box) {
      await page.mouse.move(box.x + 50, box.y + 50);
      await page.mouse.down();
      await page.mouse.move(box.x + 200, box.y + 60);
      await page.mouse.move(box.x + 300, box.y + 100);
      await page.mouse.up();
    }

    // 4. Fill signer credentials and terms agreement
    await page.check('input[name="agreed_terms"]');
    await page.fill('input[name="signer_name"]', 'Victoria Sterling');
    await page.fill('input[name="signer_title"]', 'Vice President of Engineering');

    // 5. Submit signature
    await page.click('button[type="submit"]');

    // 6. Verify redirected to workbench with contract executed
    await expect(page).toHaveURL(/.*\/project\/1/);
    await expect(page.locator('body')).toContainText('SOW Agreement digitally signed');
  });

});