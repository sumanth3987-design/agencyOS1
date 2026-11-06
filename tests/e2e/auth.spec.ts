import { test, expect } from '@playwright/test';

test.describe('Authentication & Access Control Matrix', () => {

  test('Guest is redirected to login when accessing protected dashboard', async ({ page }) => {
    await page.goto('/customer/dashboard');
    await expect(page).toHaveURL(/.*\/login/);
  });

  test('Reject login attempt with invalid credentials', async ({ page }) => {
    await page.goto('/login');
    
    await page.fill('input[name="email"], input[type="email"]', 'wrong_user@example.com');
    await page.fill('input[name="password"], input[type="password"]', 'BadPassword123!');
    await page.click('button[type="submit"], input[type="submit"]');

    // Ensure we remain on or return to login view
    await expect(page).toHaveURL(/.*\/login.*/);
  });
});