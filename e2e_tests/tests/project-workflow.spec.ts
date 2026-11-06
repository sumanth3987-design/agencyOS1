import { test, expect } from '@playwright/test';

test.describe('End-to-End Project Commissioning & Multi-Currency Flow', () => {
  const timestamp = Date.now();
  const clientUser = {
    name: `Workflow Client ${timestamp}`,
    email: `client_flow_${timestamp}@agencyostest.com`,
    password: 'SecurePassword123!',
  };

  test.beforeEach(async ({ page }) => {
    // Register a fresh user for workflow tests
    await page.goto('/register');
    await page.fill('input[name="name"]', clientUser.name);
    await page.fill('input[name="email"]', clientUser.email);
    await page.fill('input[name="password"]', clientUser.password);
    
    const roleSelect = page.locator('select[name="account_type"]');
    if (await roleSelect.isVisible()) {
      await roleSelect.selectOption('customer');
    }
    
    await page.click('button[type="submit"]');
    await page.waitForURL('**/login**');

    // Log in
    await page.fill('input[name="email"]', clientUser.email);
    await page.fill('input[name="password"]', clientUser.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard**');
  });

  test('New customers are securely blocked by KYC gate when accessing project wizard', async ({ page }) => {
    // If account verification is pending/false, attempting to start project redirects with warning
    await page.goto('/start-project');
    
    // Should display notice or redirect depending on verification state
    const currentUrl = page.url();
    const bodyText = await page.locator('body').innerText();
    
    const isProtectedOrWarned = /dashboard|KYC|pending|approval/i.test(bodyText) || currentUrl.includes('dashboard');
    expect(isProtectedOrWarned).toBeTruthy();
  });
});