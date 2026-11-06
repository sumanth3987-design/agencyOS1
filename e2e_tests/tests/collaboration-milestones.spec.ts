import { test, expect } from '@playwright/test';

test.describe('Collaboration Thread & IDOR Protection Suite', () => {
  test('IDOR Boundary: Unauthenticated or unauthorized user cannot view foreign project details', async ({ page }) => {
    // Attempt to access an arbitrary project ID (e.g., ID 999999) while unauthenticated or logged out
    await page.goto('/project/999999');

    // Should be safely intercepted and redirected to login or dashboard with warning
    await page.waitForURL(/\/(login|dashboard)/);
    const url = page.url();
    expect(url).toMatch(/(login|dashboard)/);
  });
});