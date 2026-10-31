import os

DOCS_DIR = "docs"
os.makedirs(DOCS_DIR, exist_ok=True)

files_to_create = [
    os.path.join(DOCS_DIR, "prd.md"),
    os.path.join(DOCS_DIR, "architecture.md"),
    os.path.join(DOCS_DIR, "rules.md"),
    os.path.join(DOCS_DIR, "plan.md"),
    os.path.join(DOCS_DIR, "phases.md"),
    os.path.join(DOCS_DIR, "design.md"),
    os.path.join(DOCS_DIR, "memory.md"),
    os.path.join(DOCS_DIR, "tasks.md"),
    os.path.join(DOCS_DIR, "features.md"),
    os.path.join(DOCS_DIR, "schema.md"),
    "DECISIONS.md",
]

for filepath in files_to_create:
    if not os.path.exists(filepath):
        with open(filepath, "w", encoding="utf-8") as f:
            filename = os.path.basename(filepath)
            f.write(f"# {filename}\n\nPlaceholder for {filename}.\n")
        print(f"✅ Created: {filepath}")
    else:
        print(f"ℹ️ Already exists: {filepath}")

print("\n🎉 All documentation folders and files are ready in place!")
