p = r'app/blueprints/payments/stripe.py'
content = open(p, encoding='utf-8').read()

# The file has broken structure from earlier fix attempts.
# Let me fix it by rewriting the webhook handler section.

# First, let me identify the exact issues and fix them:

# Issue 1: Line 412 - try statement must have except/finally
# The try block at line 412 should have proper except clauses
# Looking at the code, the try block starts at 412 and should have except IntegrityError and except Exception

# Issue 2: Lines 496-497 have duplicate/conflicting returns
# The proper return should be AFTER db.session.commit() inside the try block

# Issue 3: Lines 512 `if user:` has wrong indentation - should be inside the try block

# Let me fix by carefully restructuring the critical section

# Find the exact lines and fix them
lines = content.splitlines(keepends=True)

# Let me look at what the correct structure should be based on the original code pattern:
# The webhook handler has:
# - try: block starting at line 412
# - Inside try: db.session.commit() and returns
# - except IntegrityError: block
# - except (ValueError, KeyError, TypeError): block returning 400
# - except Exception: block returning 500
# - Then the function continues with other logic

# The Pylance errors indicate:
# - Line 412: try block missing proper except clauses (but it has them, so maybe the indentation is wrong)
# - Lines 496-497: duplicate returns causing syntax issues
# - Lines 499-501: indentation wrong
# - Line 512: if user: at wrong level

# Let me just rewrite the entire webhook function's critical section using a Python script approach

# Actually, let me take a simpler approach - I'll use the editor to fix specific lines
print("I'll fix the stripe.py file using targeted edits")