import { test, expect } from '@playwright/test';

test.describe('Collaboration Thread & IDOR Protection Suite', () => {

  test('IDOR Boundary: Unauthenticated or unauthorized user cannot view foreign project details', async ({ page }) => {
    // Probe protected project route as guest
    await page.goto('/project/999999');

    // System must redirect guest to login with a next parameter
    await expect(page).toHaveURL(/.*\/login\?next=.*/);
  });
});