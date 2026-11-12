import { test, expect } from '@playwright/test';

/**
 * End-to-End Test Suite: Client Intake Wizard, Dynamic Pricing & Notification Center
 *
 * Verifies:
 * 1. Self-contained registration & authentication.
 * 2. Multi-step intake navigation (Gates 1 -> 2 -> 3).
 * 3. Real-time dynamic add-on price calculations and 50% deposit updates.
 * 4. Form submission and redirection to the SOW digital contract view.
 * 5. Flyout notification panel actions and keyboard accessibility.
 */

test.describe('Client Intake Wizard & Notification Center Workflows', () => {

  test.beforeEach(async ({ page }) => {
    const timestamp = Date.now();
    const testEmail = `test_client_${timestamp}@example.com`;
    const testPassword = 'Password123!';

    // Self-register to guarantee test independence
    await page.goto('/register');
    await page.fill('input[name="name"]', 'Test Client Enterprise');
    await page.fill('input[name="email"]', testEmail);
    await page.fill('input[name="password"]', testPassword);

    const roleSelect = page.locator('select[name="role"]');
    if (await roleSelect.isVisible()) {
      await roleSelect.selectOption('customer');
    }

    await page.click('button[type="submit"]');

    if (page.url().includes('/login')) {
      await page.fill('input[name="email"]', testEmail);
      await page.fill('input[name="password"]', testPassword);
      await page.click('button[type="submit"]');
    }

    await expect(page).toHaveURL(/\/dashboard/);
  });

  test('should navigate 3-gate intake wizard and dynamically calculate add-on pricing', async ({ page }) => {
    await page.goto('/start-project/website-development');

    // Verify Gate 1 Shell
    await expect(page.locator('h1')).toContainText('Project kickoff intake');
    await expect(page.locator('#progressText')).toContainText('Gate 1 of 3');

    // Fill Gate 1 Details
    await page.selectOption('#package', 'Business Website');
    await page.fill('#brand_name', 'Acme Logistics Global');
    await page.fill('#industry', 'Supply Chain Tech');

    // Advance to Gate 2
    await page.click('#nextStepBtn');
    await expect(page.locator('#progressText')).toContainText('Gate 2 of 3');

    // Fill Gate 2 Details
    await page.fill('#brand_colors', '#09090B, #6366F1');
    await page.selectOption('#deadline', 'Standard Sprint (14 Days)');
    await page.fill('#reference_sites', 'linear.app, stripe.com');

    // Advance to Gate 3
    await page.click('#nextStepBtn');
    await expect(page.locator('#progressText')).toContainText('Gate 3 of 3');

    // Base price for Business Website (INR 78,000)
    const totalDisplay = page.locator('#liveTotalDisplay');
    const depositDisplay = page.locator('#liveDepositDisplay');

    await expect(totalDisplay).toContainText('₹78,000.00');
    await expect(depositDisplay).toContainText('₹39,000.00');

    // Select Add-on: Payment Gateway Rails (+INR 12,000)
    await page.check('input[value="Payment Gateway Integration"]');
    await expect(totalDisplay).toContainText('₹90,000.00');
    await expect(depositDisplay).toContainText('₹45,000.00');

    // Select Add-on: Speed Optimization (+INR 8,000)
    await page.check('input[value="Speed & Core Web Vitals Optimization"]');
    await expect(totalDisplay).toContainText('₹98,000.00');
    await expect(depositDisplay).toContainText('₹49,000.00');

    // Fill Scope Narrative & Skills
    await page.fill('#required_skills', 'Python, Flask, React, Tailwind CSS');
    await page.fill('#brief', 'Comprehensive e-commerce logistics platform with live carrier tracking.');

    // Submit Intake -> Redirects to Digital SOW Contract
    await page.click('#submitBtn');
    await expect(page).toHaveURL(/\/project\/\d+\/contract/);
    await expect(page.locator('body')).toContainText('Milestone project created');
  });

  test('should open notification center and dismiss cleanly with Escape key', async ({ page }) => {
    await page.goto('/dashboard');

    const notificationToggle = page.locator('[data-notification-toggle]');
    const notificationPanel = page.locator('[data-notification-panel]');

    await expect(notificationToggle).toBeVisible();
    await notificationToggle.click();

    // Verify Notification Panel Expands
    await expect(notificationPanel).toBeVisible();
    await expect(page.locator('#client-notification-heading')).toContainText('Action & Audit Dispatch');

    // Press Escape to dismiss panel
    await page.keyboard.press('Escape');
    await expect(notificationPanel).toBeHidden();
  });
});