import os

output_file = "repo_dump.txt"
exclude_dirs = {
    "__pycache__",
    ".git",
    "venv",
    "env",
    "node_modules",
    ".vscode",
    "instance",
}
exclude_extensions = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".zip",
    ".pyc",
    ".db",
}

with open(output_file, "w", encoding="utf-8") as outfile:
  for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in exclude_dirs]
    for file in files:
      if any(file.endswith(ext) for ext in exclude_extensions):
        continue
      if file == output_file or file == "bundle.py":
        continue
      filepath = os.path.join(root, file)
      outfile.write(f"--- FILE: {filepath} ---\n")
      try:
        with open(filepath, "r", encoding="utf-8", errors='ignore') as f:
          outfile.write(f.read())
      except Exception as e:
        outfile.write(f"[Error reading file: {e}]")
      outfile.write("\n\n" + "=" * 50 + "\n\n")

print("Successfully generated repo_dump.txt!")