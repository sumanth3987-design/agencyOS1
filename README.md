# 1. Clone the repository
git clone https://github.com/sumanth3987-design/agencyos1.git
cd agencyos1

# 2. Create and activate a virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables (.env)
# Create a .env file in the root directory:
SECRET_KEY=your_cryptographically_secure_random_key_min_32_chars
FLASK_ENV=development
RAZORPAY_KEY_ID=rzp_test_placeholder
RAZORPAY_KEY_SECRET=placeholder_secret

# 5. Run the local application
python run.py

## Billing currencies

Project packages can be commissioned in INR, USD, EUR, or GBP using the price
matrix in `app/utils/currency.py`. Starter Retainer subscriptions remain INR-only.
Razorpay order creation and verification use the project's saved currency; the
Razorpay account must be enabled to accept the selected currency.
Stripe and wire-transfer processing are not included in this implementation.

International invoices currently display an 18% tax estimate and explicitly
state that applicable tax treatment must be confirmed for the relevant
jurisdictions. This is not a determination of international tax liability.
Apply the `20261102_multi_currency` database migration before deploying.
Specialist payout allocations retain the project's currency; actual bank
settlement and any currency conversion remain an out-of-band agency process.
Record any conversion separately; this release does not provide FX conversion.