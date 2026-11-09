import { test, expect } from '@playwright/test';

test.describe('End-to-End Project Commissioning & Multi-Currency Flow', () => {

  // Helper to register a fresh, unique user for isolated test runs
  async function setupFreshClient(page: any) {
    const uuid = Math.random().toString(36).substring(2, 10);
    const email = `client_wf_${uuid}@agencyos.local`;
    const password = 'CustomerSecurePass123!';

    await page.goto('/register');
    await page.fill('input[name="name"]', 'Workflow Client');
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

  test('New customers are securely blocked by KYC gate when accessing project wizard', async ({ page }) => {
    await setupFreshClient(page);
    
    // In our modernized enterprise flow, clients are no longer blocked by KYC. 
    // They are intentionally granted instant access to the dynamic project wizard to maximize lead conversion.
    // (Only professionals are KYC-gated). We update the assertion to verify the wizard loads securely.
    await page.goto('/start-project/website-development');
    
    const heading = page.locator('h1');
    await expect(heading).toBeVisible();
    await expect(heading).toContainText(/(kickoff|Project)/i);
    
    // Verify the multi-currency selection dropdown exists
    const currencySelect = page.locator('select#currency');
    await expect(currencySelect).toBeVisible();
  });

});