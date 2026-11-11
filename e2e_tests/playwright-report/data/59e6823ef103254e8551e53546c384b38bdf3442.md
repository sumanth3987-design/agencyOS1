# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: project-workflow.spec.ts >> AgencyOS Full Lifecycle: Client to Milestone Release >> should complete the full project lifecycle successfully
- Location: tests\project-workflow.spec.ts:19:3

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: page.selectOption: Test timeout of 30000ms exceeded.
Call log:
  - waiting for locator('select[name="account_type"]')

```

# Page snapshot

```yaml
- generic [ref=e2]:
  - generic [ref=e3]:
    - link "A" [ref=e4] [cursor=pointer]:
      - /url: /
    - heading "Create AgencyOS Account" [level=1] [ref=e6]
    - paragraph [ref=e7]: Initialize milestone delivery, SOW digital agreements, and live sprint ledgers.
  - generic [ref=e8]:
    - button "Client Organization" [ref=e9] [cursor=pointer]
    - button "Specialist Partner" [ref=e10] [cursor=pointer]
  - generic [ref=e11]:
    - generic [ref=e12]:
      - generic [ref=e13]: Full Legal Name *
      - textbox "e.g. Alexander Vance" [ref=e14]: E2E Test Client
    - generic [ref=e15]:
      - generic [ref=e16]:
        - generic [ref=e17]: Work Email Address *
        - textbox "name@enterprise.com" [ref=e18]: e2e_client@test.com
      - generic [ref=e19]:
        - generic [ref=e20]: Password (8+ chars) *
        - textbox "••••••••" [ref=e21]: TestPass123!
    - generic [ref=e23]:
      - generic [ref=e24]: Confirm Password *
      - textbox "••••••••" [active] [ref=e25]: TestPass123!
    - generic [ref=e26]:
      - generic [ref=e27]:
        - generic [ref=e28]: Registered Entity / Business Name
        - textbox "e.g. Tata Retail Logistics Ltd" [ref=e29]
      - generic [ref=e30]:
        - generic [ref=e31]: Phone / WhatsApp Number
        - textbox "+91 98765 43210" [ref=e32]
    - button "Create Client Organization Account →" [ref=e33] [cursor=pointer]
  - generic [ref=e34]:
    - text: Already have an account?
    - link "Sign In" [ref=e35] [cursor=pointer]:
      - /url: /login
```

# Test source

```ts
  1   | import { test, expect } from '@playwright/test';
  2   | 
  3   | /**
  4   |  * THE GOLD STANDARD WORKFLOW TEST
  5   |  * This test simulates the entire lifecycle of an AgencyOS project.
  6   |  * It spans 3 different user roles: Client, Specialist, and Admin.
  7   |  */
  8   | 
  9   | test.describe('AgencyOS Full Lifecycle: Client to Milestone Release', () => {
  10  |   
  11  |   // Test Credentials
  12  |   const CLIENT_EMAIL = 'e2e_client@test.com';
  13  |   const CLIENT_PASS = 'TestPass123!';
  14  |   const PRO_EMAIL = 'e2e_pro@test.com';
  15  |   const PRO_PASS = 'TestPass123!';
  16  |   const ADMIN_EMAIL = 'admin@test.com';
  17  |   const ADMIN_PASS = 'admin'; // Assuming your create-admin command uses this
  18  | 
  19  |   test('should complete the full project lifecycle successfully', async ({ page }) => {
  20  |     
  21  |     // ---------------------------------------------------------
  22  |     // 1. CLIENT REGISTRATION & ONBOARDING
  23  |     // ---------------------------------------------------------
  24  |     await page.goto('/register');
  25  |     await page.fill('input[name="name"]', 'E2E Test Client');
  26  |     await page.fill('input[name="email"]', CLIENT_EMAIL);
  27  |     await page.fill('input[name="password"]', CLIENT_PASS);
  28  |     await page.fill('input[name="password_confirm"]', CLIENT_PASS);
> 29  |     await page.selectOption('select[name="account_type"]', 'customer');
      |                ^ Error: page.selectOption: Test timeout of 30000ms exceeded.
  30  |     await page.click('button[type="submit"]');
  31  | 
  32  |     // Note: In a real test environment, we would mock the email verification.
  33  |     // For this E2E flow, we assume email verification is disabled for testing 
  34  |     // or we manually trigger the verify link.
  35  |     await page.goto('/login');
  36  |     await page.fill('input[name="email"]', CLIENT_EMAIL);
  37  |     await page.fill('input[name="password"]', CLIENT_PASS);
  38  |     await page.click('button[type="submit"]');
  39  |     await expect(page).toHaveURL(/.*dashboard/);
  40  | 
  41  |     // ---------------------------------------------------------
  42  |     // 2. CLIENT STARTS A PROJECT & SIGNS SOW
  43  |     // ---------------------------------------------------------
  44  |     await page.goto('/start-project/website-development');
  45  |     await page.fill('textarea[name="requirements"]', 'Building a high-end agency portfolio.');
  46  |     await page.selectOption('select[name="package"]', 'Basic Website');
  47  |     await page.click('button[type="submit"]');
  48  | 
  49  |     // Client must sign the SOW
  50  |     await expect(page).toHaveURL(/.*contract/);
  51  |     await page.fill('input[name="signer_name"]', 'E2E Client');
  52  |     await page.fill('input[name="signer_title"]', 'CEO');
  53  |     await page.check('input[name="agreed_terms"]');
  54  |     await page.click('button[type="submit"]');
  55  |     await expect(page.locator('text=SOW Agreement digitally signed')).toBeVisible();
  56  | 
  57  |     // ---------------------------------------------------------
  58  |     // 3. CLIENT PAYS MILESTONE 1 (Simulated)
  59  |     // ---------------------------------------------------------
  60  |     await page.goto(`/payment/${await page.locator('a[href*="/payment/"]').first().getAttribute('href') || ''}`);
  61  |     // We trigger the simulator button
  62  |     await page.click('text=Simulate Test Payment');
  63  |     await expect(page.locator('text=Milestone 1 deposit')).toBeVisible();
  64  | 
  65  |     // ---------------------------------------------------------
  66  |     // 4. SPECIALIST JOINS & APPLIES
  67  |     // ---------------------------------------------------------
  68  |     // Log out client and log in as Pro
  69  |     await page.goto('/logout');
  70  |     await page.goto('/login');
  71  |     await page.fill('input[name="email"]', PRO_EMAIL);
  72  |     await page.fill('input[name="password"]', PRO_PASS);
  73  |     await page.selectOption('select[name="login_role"]', 'professional');
  74  |     await page.click('button[type="submit"]');
  75  | 
  76  |     // Navigate to Marketplace and Apply
  77  |     await page.goto('/professional/projects');
  78  |     await page.click('text=Apply'); // Clicks first available project
  79  |     await page.fill('textarea[name="proposal"]', 'I am the best developer for this.');
  80  |     await page.click('button[type="submit"]');
  81  |     await expect(page.locator('text=Application registered')).toBeVisible();
  82  | 
  83  |     // ---------------------------------------------------------
  84  |     // 5. ADMIN ASSIGNS SPECIALIST
  85  |     // ---------------------------------------------------------
  86  |     // Log out pro and log in as Admin
  87  |     await page.goto('/logout');
  88  |     await page.goto('/login');
  89  |     await page.fill('input[name="email"]', ADMIN_EMAIL);
  90  |     await page.fill('input[name="password"]', ADMIN_PASS);
  91  |     await page.click('button[type="submit"]');
  92  | 
  93  |     await page.goto('/admin/applications');
  94  |     await page.click('text=Assign Specialist'); // Assumes an assign button exists
  95  |     // Simulate assignment logic...
  96  |     // (In a real test, we'd select the Pro and click confirm)
  97  |     
  98  |     // ---------------------------------------------------------
  99  |     // 6. SPECIALIST SUBMITS DELIVERABLE
  100 |     // ---------------------------------------------------------
  101 |     await page.goto('/logout');
  102 |     await page.goto('/login');
  103 |     await page.fill('input[name="email"]', PRO_EMAIL);
  104 |     await page.fill('input[name="password"]', PRO_PASS);
  105 |     await page.click('button[type="submit"]');
  106 | 
  107 |     await page.goto('/professional/my-projects');
  108 |     await page.click('text=Commence Development');
  109 |     await page.goto('/professional/project-workspace'); // Navigate to active project
  110 |     
  111 |     await page.fill('textarea[name="completion_notes"]', 'The site is ready for review.');
  112 |     await page.click('button[type="submit"]');
  113 |     await expect(page.locator('text=Milestone deliverable')).toBeVisible();
  114 | 
  115 |     // ---------------------------------------------------------
  116 |     // 7. CLIENT APPROVES & ESCROW RELEASES
  117 |     // ---------------------------------------------------------
  118 |     await page.goto('/logout');
  119 |     await page.goto('/login');
  120 |     await page.fill('input[name="email"]', CLIENT_EMAIL);
  121 |     await page.fill('input[name="password"]', CLIENT_PASS);
  122 |     await page.click('button[type="submit"]');
  123 | 
  124 |     await page.goto('/project-details'); // Navigate to project
  125 |     await page.click('text=Approve'); 
  126 |     await expect(page.locator('text=Milestone signed off')).toBeVisible();
  127 | 
  128 |     console.log('✅ SUCCESS: Full Project Lifecycle Verified.');
  129 |   });
```