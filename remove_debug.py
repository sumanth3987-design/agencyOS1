p = r'app/blueprints/payments/stripe.py'
lines = open(p, encoding='utf-8').read().splitlines(keepends=True)
kept = [ln for ln in lines if 'DEBUG' not in ln]
open(p, 'w', encoding='utf-8').writelines(kept)
print("Removed", len(lines) - len(kept), "debug lines")
