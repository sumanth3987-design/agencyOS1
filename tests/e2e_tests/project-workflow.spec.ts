import { test, expect } from '@playwright/test';

test.describe('End-to-End Project Commissioning & Multi-Currency Flow', () => {

  test('New customers are securely blocked by KYC gate when accessing project wizard', async ({ page }) => {
    const uniqueEmail = `test_workflow_${Date.now()}@example.com`;
    const password = 'SecurePassword123!';

    // 1. Register a fresh user
    await page.goto('/register');
    await page.fill('input[name="name"], input[id="name"]', 'Workflow Tester');
    await page.fill('input[name="email"], input[type="email"]', uniqueEmail);
    await page.fill('input[name="password"], input[type="password"]', password);
    
    // Select customer role if dropdown exists
    const roleSelect = page.locator('select[name="role"], select[name="account_type"]');
    if (await roleSelect.isVisible()) {
      await roleSelect.selectOption('customer');
    }

    // Await registration to process and redirect to login page
    await Promise.all([
      page.waitForURL(/.*\/login.*/),
      page.click('button[type="submit"], input[type="submit"]')
    ]);

    // 2. Log in with the newly created credentials
    await page.fill('input[name="email"], input[type="email"]', uniqueEmail);
    await page.fill('input[name="password"], input[type="password"]', password);
    
    // Await login to process and drop the session cookie before moving on
    await Promise.all([
      page.waitForURL(/.*\/dashboard.*/),
      page.click('button[type="submit"], input[type="submit"]')
    ]);

    // 3. Attempt to navigate to the start project wizard
    await page.goto('/start-project');

    // 4. System MUST enforce KYC, block access, and redirect back to dashboard
    await expect(page).toHaveURL(/.*\/dashboard.*/);
    
    // 5. Assert the project wizard form is completely inaccessible/not rendered
    await expect(page.locator('input[name="title"], select[name="currency"]')).not.toBeVisible();
  });
});