"""Generate the code reference pages and navigation."""

from pathlib import Path
import mkdocs_gen_files

nav = mkdocs_gen_files.Nav()

# Adjust this to your package structure
src = Path(__file__).parent.parent / "src"
package_dir = src / "flowgentic"


def should_skip_file(path):
	"""Check if we should skip this file."""
	return path.name == "__main__.py" or "__pycache__" in str(path)


# Process regular Python files (not __init__.py)
for path in sorted(package_dir.rglob("*.py")):
	if should_skip_file(path) or path.name == "__init__.py":
		continue

	# Get the module path relative to src
	module_path = path.relative_to(src).with_suffix("")

	# Create the documentation path
	doc_path = path.relative_to(src).with_suffix(".md")
	full_doc_path = Path("api") / doc_path

	print(f"Processing: {path} -> {full_doc_path}")

	# Get the parts for navigation
	parts = tuple(module_path.parts)

	# Add to navigation
	nav[parts] = doc_path.as_posix()

	# Generate the API documentation file
	with mkdocs_gen_files.open(full_doc_path, "w") as fd:
		ident = ".".join(parts)
		print(f"Creating documentation for: {ident}")
		fd.write(f"# {ident}\n\n::: {ident}")

	# Set edit path for the file
	mkdocs_gen_files.set_edit_path(full_doc_path, path)

# Handle __init__.py files to create index pages
for path in sorted(package_dir.rglob("__init__.py")):
	module_path = path.relative_to(src).with_suffix("")
	parts = tuple(module_path.parts)

	# Create index.md for the package/subpackage
	if len(parts) > 0:
		# Remove __init__ from parts since we're creating an index
		if parts[-1] == "__init__":
			parts = parts[:-1]

		if parts:  # Only if we have valid parts
			doc_path = Path(*parts) / "index.md"
			full_doc_path = Path("api") / doc_path

			print(f"Creating index for: {path} -> {full_doc_path}")

			# Add to navigation and create index file
			nav[parts] = doc_path.as_posix()

			with mkdocs_gen_files.open(full_doc_path, "w") as fd:
				ident = ".".join(parts)
				fd.write(f"# {ident}\n\n::: {ident}")

			mkdocs_gen_files.set_edit_path(full_doc_path, path)

# Generate the navigation summary
with mkdocs_gen_files.open("api/SUMMARY.md", "w") as nav_file:
	nav_file.writelines(nav.build_literate_nav())
