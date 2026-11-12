p = r'e2e_tests/tests/intake-and-notifications.spec.ts'
s = open(p, encoding='utf-8').read()

old = r'''  test.beforeEach(async ({ page }) => {
    // Authenticate as active customer
    await page.goto('/login');
    await page.fill('input[name="email"]', 'customer@example.com');
    await page.fill('input[name="password"]', 'Password123');
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/dashboard/);
  });'''

new = r'''  test.beforeEach(async ({ page }) => {
    const timestamp = Date.now();
    const testEmail = `test_client_${timestamp}@example.com`;
    const testPassword = 'Password123!';

    // Register a fresh test customer directly
    await page.goto('/register');
    await page.fill('input[name="name"]', 'Test Client Enterprise');
    await page.fill('input[name="email"]', testEmail);
    await page.fill('input[name="password"]', testPassword);

    // Select customer role if role picker exists
    const roleSelect = page.locator('select[name="role"]');
    if (await roleSelect.isVisible()) {
      await roleSelect.selectOption('customer');
    }

    await page.click('button[type="submit"]');

    // If redirected to login, perform login; if auto-logged in, verify dashboard
    if (page.url().includes('/login')) {
      await page.fill('input[name="email"]', testEmail);
      await page.fill('input[name="password"]', testPassword);
      await page.click('button[type="submit"]');
    }

    await expect(page).toHaveURL(/\/dashboard/);
  });'''

if old in s:
    s = s.replace(old, new, 1)
    open(p, 'w', encoding='utf-8').write(s)
    print('REPLACED OK')
else:
    print('OLD NOT FOUND - Let us inspect file context:')
    idx = s.find('test.beforeEach')
    if idx != -1:
        print(repr(s[idx:idx+350]))
    else:
        print('test.beforeEach not found at all.')