# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: intake-and-notifications.spec.ts >> Client Intake Wizard & Notification Center Workflows >> should open notification center and dismiss cleanly with Escape key
- Location: tests\intake-and-notifications.spec.ts:76:3

# Error details

```
Error: expect(page).toHaveURL(expected) failed

Expected pattern: /\/dashboard/
Received string:  "http://127.0.0.1:5000/login"
Timeout: 5000ms

Call log:
  - Expect "toHaveURL" with timeout 5000ms
    12 × locator resolved to <html lang="en" class="dark">…</html>
       - unexpected value "http://127.0.0.1:5000/login"

```

```yaml
- link "A":
  - /url: /
- heading "Access AgencyOS" [level=1]
- paragraph: Review milestone gates, approve deliverables, and inspect ledger invoices.
- button "Client"
- button "Specialist"
- button "Admin"
- text: Invalid email or password.
- button "✕"
- text: Registered Email Address
- textbox "user@organization.com"
- text: Password
- link "Forgot password?":
  - /url: /forgot-password
- textbox "••••••••"
- button "Sign In to Client Portal →"
- paragraph:
  - text: Need a client account?
  - link "Register Business":
    - /url: /register
```

# Test source

```ts
  1  | import { test, expect } from '@playwright/test';
  2  | 
  3  | /**
  4  |  * End-to-End Test Suite: Client Intake Wizard, Dynamic Pricing & Notification Center
  5  |  *
  6  |  * Verifies:
  7  |  * 1. Multi-step intake navigation (Gates 1 -> 2 -> 3).
  8  |  * 2. Real-time dynamic add-on price calculations and 50% deposit updates.
  9  |  * 3. Form submission and redirection to the SOW digital contract view.
  10 |  * 4. Flyout notification panel actions and keepalive read-state dispatches.
  11 |  */
  12 | 
  13 | test.describe('Client Intake Wizard & Notification Center Workflows', () => {
  14 | 
  15 |   test.beforeEach(async ({ page }) => {
  16 |     // Authenticate as active customer
  17 |     await page.goto('/login');
  18 |     await page.fill('input[name="email"]', 'customer@example.com');
  19 |     await page.fill('input[name="password"]', 'Password123');
  20 |     await page.click('button[type="submit"]');
> 21 |     await expect(page).toHaveURL(/\/dashboard/);
     |                        ^ Error: expect(page).toHaveURL(expected) failed
  22 |   });
  23 | 
  24 |   test('should navigate 3-gate intake wizard and dynamically calculate add-on pricing', async ({ page }) => {
  25 |     await page.goto('/start-project/website-development');
  26 | 
  27 |     // Verify Gate 1 Shell
  28 |     await expect(page.locator('h1')).toContainText('Project kickoff intake');
  29 |     await expect(page.locator('#progressText')).toContainText('Gate 1 of 3');
  30 | 
  31 |     // Fill Gate 1 Details
  32 |     await page.selectOption('#package', 'Business Website');
  33 |     await page.fill('#brand_name', 'Acme Logistics Global');
  34 |     await page.fill('#industry', 'Supply Chain Tech');
  35 | 
  36 |     // Advance to Gate 2
  37 |     await page.click('#nextStepBtn');
  38 |     await expect(page.locator('#progressText')).toContainText('Gate 2 of 3');
  39 | 
  40 |     // Fill Gate 2 Details
  41 |     await page.fill('#brand_colors', '#09090B, #6366F1');
  42 |     await page.selectOption('#deadline', 'Standard Sprint (14 Days)');
  43 |     await page.fill('#reference_sites', 'linear.app, stripe.com');
  44 | 
  45 |     // Advance to Gate 3
  46 |     await page.click('#nextStepBtn');
  47 |     await expect(page.locator('#progressText')).toContainText('Gate 3 of 3');
  48 | 
  49 |     // Base price for Business Website (INR 78,000)
  50 |     const totalDisplay = page.locator('#liveTotalDisplay');
  51 |     const depositDisplay = page.locator('#liveDepositDisplay');
  52 | 
  53 |     await expect(totalDisplay).toContainText('₹78,000.00');
  54 |     await expect(depositDisplay).toContainText('₹39,000.00');
  55 | 
  56 |     // Select Add-on: Payment Gateway Rails (+INR 12,000)
  57 |     await page.check('input[value="Payment Gateway Integration"]');
  58 |     await expect(totalDisplay).toContainText('₹90,000.00');
  59 |     await expect(depositDisplay).toContainText('₹45,000.00');
  60 | 
  61 |     // Select Add-on: Speed Optimization (+INR 8,000)
  62 |     await page.check('input[value="Speed & Core Web Vitals Optimization"]');
  63 |     await expect(totalDisplay).toContainText('₹98,000.00');
  64 |     await expect(depositDisplay).toContainText('₹49,000.00');
  65 | 
  66 |     // Fill Scope Narrative & Skills
  67 |     await page.fill('#required_skills', 'Python, Flask, React, Tailwind CSS');
  68 |     await page.fill('#brief', 'Comprehensive e-commerce logistics platform with live carrier tracking.');
  69 | 
  70 |     // Submit Intake -> Redirects to Digital SOW Contract
  71 |     await page.click('#submitBtn');
  72 |     await expect(page).toHaveURL(/\/project\/\d+\/contract/);
  73 |     await expect(page.locator('body')).toContainText('Milestone project created');
  74 |   });
  75 | 
  76 |   test('should open notification center and dismiss cleanly with Escape key', async ({ page }) => {
  77 |     await page.goto('/dashboard');
  78 | 
  79 |     const notificationToggle = page.locator('[data-notification-toggle]');
  80 |     const notificationPanel = page.locator('[data-notification-panel]');
  81 | 
  82 |     await expect(notificationToggle).toBeVisible();
  83 |     await notificationToggle.click();
  84 | 
  85 |     // Verify Notification Panel Expands
  86 |     await expect(notificationPanel).toBeVisible();
  87 |     await expect(page.locator('#client-notification-heading')).toContainText('Action & Audit Dispatch');
  88 | 
  89 |     // Press Escape to dismiss panel
  90 |     await page.keyboard.press('Escape');
  91 |     await expect(notificationPanel).toBeHidden();
  92 |   });
  93 | });
```