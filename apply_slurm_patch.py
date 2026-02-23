"""
Patch Dragon's slurm.py safely:
- Replaces --ntasks with --ntasks-per-node=1
- Adds --overlap
- Backs up original slurm.py
- Deletes __pycache__ to force recompilation
"""

import os
import shutil

def patch_slurm_file():
    try:
        import dragon.launcher.wlm.slurm as slurm_module
        slurm_path = slurm_module.__file__
    except ImportError:
        print("[ERROR] Could not find Dragon slurm module.")
        return

    # Backup original
    backup_path = slurm_path + ".bak"
    if not os.path.exists(backup_path):
        shutil.copy(slurm_path, backup_path)
        print(f"[INFO] Backup created at {backup_path}")
    else:
        print(f"[INFO] Backup already exists at {backup_path}")

    # Read original file
    with open(slurm_path, "r") as f:
        lines = f.readlines()

    new_lines = []
    inside_get_wlm = False

    for line in lines:
        stripped = line.strip()

        # Patch SRUN_COMMAND_LINE inside class
        if stripped.startswith("SRUN_COMMAND_LINE"):
            indent = line[:line.find("SRUN_COMMAND_LINE")]
            new_lines.append(
                indent + 'SRUN_COMMAND_LINE = (\n'
                + indent + '    "srun "\n'
                + indent + '    "--nodes={nnodes} "\n'
                + indent + '    "--ntasks-per-node=1 "\n'
                + indent + '    "--cpu_bind=none "\n'
                + indent + '    "--overlap "\n'
                + indent + '    "-u -l -W 0"\n'
                + indent + ')\n'
            )
            continue

        # Patch _get_wlm_launch_be_args method
        if stripped.startswith("def _get_wlm_launch_be_args"):
            inside_get_wlm = True
            indent = line[:line.find("def")]
            new_lines.append(line)  # keep method definition line
            # insert new method body
            method_indent = indent + " " * 4
            new_lines.append(method_indent + "slurm_launch_be_args = [\n")
            new_lines.append(method_indent + '    "srun",\n')
            new_lines.append(method_indent + '    f"--nodes={args_map[\'nnodes\']}",\n')
            new_lines.append(method_indent + '    "--ntasks-per-node=1",\n')
            new_lines.append(method_indent + '    "--cpu_bind=none",\n')
            new_lines.append(method_indent + '    "--overlap",\n')
            new_lines.append(method_indent + "]\n")
            new_lines.append(method_indent + "return slurm_launch_be_args + launch_args\n")
            continue

        # Skip original method body lines
        if inside_get_wlm:
            if stripped == "" or stripped.startswith("return") or stripped.startswith("slurm_launch_be_args"):
                continue
            if stripped == "":
                inside_get_wlm = False
                continue
            continue

        # Keep other lines
        new_lines.append(line)

    # Write patched file
    with open(slurm_path, "w") as f:
        f.writelines(new_lines)

    print(f"[SUCCESS] Dragon slurm.py patched at {slurm_path}")

    # Delete __pycache__ folder to force recompilation
    pycache_dir = os.path.join(os.path.dirname(slurm_path), "__pycache__")
    if os.path.exists(pycache_dir):
        shutil.rmtree(pycache_dir)
        print(f"[INFO] Removed __pycache__ at {pycache_dir}")
    else:
        print(f"[INFO] No __pycache__ found at {pycache_dir}")

if __name__ == "__main__":
    patch_slurm_file()