p = r'app/blueprints/payments/stripe.py'
lines = open(p, encoding='utf-8').read().splitlines(keepends=True)

# Locate the specific else block: 'else:' (24 sp) followed by StripeWebhookEvent.mark_processed,
# then a 'db.session.commit()' at 24 spaces that belongs inside the else.
target_commit_idx = None
for i in range(len(lines)):
    if lines[i].startswith('                        else:') and 'StripeWebhookEvent.mark_processed(' in lines[i+1]:
        # scan forward for db.session.commit() at exactly 24-space indent
        for k in range(i+1, min(i+12, len(lines))):
            if lines[k].startswith('                        db.session.commit()') and not lines[k].startswith('                            '):
                target_commit_idx = k
                break
        break

if target_commit_idx is None:
    print("TARGET NOT FOUND")
else:
    # Re-indent target_commit_idx and the following 2 lines (print, return) from 24 to 28 spaces
    for m in range(target_commit_idx, target_commit_idx + 3):
        lines[m] = '                            ' + lines[m][24:]
    print("Re-indented lines", target_commit_idx+1, "-", target_commit_idx+3)

open(p, 'w', encoding='utf-8').writelines(lines)
print("DONE")
