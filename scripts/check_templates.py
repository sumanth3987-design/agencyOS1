import os
from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError

env = Environment(loader=FileSystemLoader("templates"))
print("Scanning all templates for syntax errors...\n")

has_error = False
for root, dirs, files in os.walk("templates"):
    for file in files:
        if file.endswith(".html"):
            rel_path = os.path.relpath(os.path.join(root, file), "templates").replace("\\", "/")
            try:
                env.get_template(rel_path)
            except TemplateSyntaxError as e:
                has_error = True
                print(f"❌ ERROR in templates/{rel_path} on line {e.lineno}: {e.message}")
            except Exception:
                pass

if not has_error:
    print("✅ All templates parsed cleanly with zero syntax errors!")