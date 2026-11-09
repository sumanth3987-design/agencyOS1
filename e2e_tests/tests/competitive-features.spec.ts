import { test, expect } from '@playwright/test';

test.describe('AgencyOS Competitive SaaS Workflows & E2E Verification', () => {

  test('Client completes dynamic intake wizard and transitions to SOW contract', async ({ page }) => {
    // 1. Authenticate as client
    await page.goto('/login');
    await page.fill('input[name="email"]', 'client@example.com');
    await page.fill('input[name="password"]', 'ClientSecurePass123!');
    await page.click('button[type="submit"]');

    // 2. Open Project Intake Wizard
    await page.goto('/start-project/website-development');
    await expect(page.locator('h1')).toContainText('Project kickoff intake');

    // 3. Gate 1: Select Package, Currency, Brand Name, and Sector
    await page.selectOption('select#package', 'Business Website');
    await page.selectOption('select#currency', 'INR');
    await page.fill('input#brand_name', 'Acme Global Ventures');
    await page.fill('input#industry', 'Logistics & AI');

    // Verify stepper indicator and advance to Gate 2
    await page.click('#nextStepBtn');

    // 4. Gate 2: Brand Direction and Velocity
    await page.selectOption('select#deadline', 'Standard Sprint (14 Days)');
    await page.fill('input#brand_colors', '#09090B, #6366F1');
    await page.fill('input#reference_sites', 'linear.app, stripe.com');

    // Advance to Gate 3
    await page.click('#nextStepBtn');

    // 5. Gate 3: Required Specialist Skills, Add-ons, and Scope Brief
    await page.fill('input#required_skills', 'Python, Flask, React, Tailwind CSS');
    await page.fill(
      'textarea#brief',
      'Deploy full-stack multi-tenant platform with automated two-stage escrow and milestone billing.'
    );

    // Verify dynamic live investment display is visible
    const totalDisplay = page.locator('#liveTotalDisplay');
    await expect(totalDisplay).toBeVisible();

    // Submit project intake form
    await page.click('#submitBtn');

    // 6. Verify transition to digital SOW agreement
    await expect(page).toHaveURL(/.*\/contract/);
    await expect(page.locator('h1')).toContainText('Statement of Work (SOW)');
  });

  test('Digital SOW signature records audit credentials and activates kickoff gate', async ({ page }) => {
    // Navigate directly to project contract
    await page.goto('/project/1/contract');

    // Accept statutory terms and provide authorized signatory details
    await page.check('input[name="agreed_terms"]');
    await page.fill('input[name="signer_name"]', 'Authorized Executive');
    await page.fill('input[name="signer_title"]', 'Managing Director & CEO');

    // Submit digital signature
    await page.click('button[type="submit"]');

    // Verify redirected to project workbench with success feedback
    await expect(page).toHaveURL(/.*\/project\/1/);
    await expect(page.locator('body')).toContainText('SOW Agreement digitally signed');
  });

  test('Interactive client notification center renders inline actions', async ({ page }) => {
    // Open client dashboard
    await page.goto('/dashboard');

    // Locate and click notification bell trigger
    const bellBtn = page.locator('[data-notification-toggle]');
    await expect(bellBtn).toBeVisible();
    await bellBtn.click();

    // Verify notification panel expands
    const panel = page.locator('#client-notification-panel');
    await expect(panel).toBeVisible();

    // Check header text inside panel
    await expect(panel.locator('h2')).toContainText('Action & Audit Dispatch');
  });

});