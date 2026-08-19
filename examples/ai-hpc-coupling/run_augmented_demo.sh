#!/usr/bin/env bash
set -euo pipefail

demo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
flowgentic_root="$(cd "${demo_dir}/../.." && pwd)"
workspace_root="$(cd "${flowgentic_root}/../.." && pwd)"

asyncflow_src="${ASYNCFLOW_SRC:-${workspace_root}/radical/radical.asyncflow/src}"
adr_src="${ADR_SRC:-${workspace_root}/radical/radical.adr/src}"
python_bin="${FLOWGENTIC_PYTHON:-${flowgentic_root}/.venv/bin/python}"
controller="${1:-application}"

if [[ "${controller}" != "application" && "${controller}" != "adr" ]]; then
    echo "usage: $0 [application|adr] [additional demo arguments]" >&2
    exit 2
fi
shift || true

if [[ ! -x "${python_bin}" ]]; then
    echo "Flowgentic Python environment not found: ${python_bin}" >&2
    echo "Set FLOWGENTIC_PYTHON to the desired interpreter." >&2
    exit 2
fi

if [[ ! -d "${asyncflow_src}" ]]; then
    echo "AsyncFlow source not found: ${asyncflow_src}" >&2
    echo "Set ASYNCFLOW_SRC to radical.asyncflow/src." >&2
    exit 2
fi

if [[ "${controller}" == "adr" && ! -d "${adr_src}" ]]; then
    echo "ADR source not found: ${adr_src}" >&2
    echo "Set ADR_SRC to radical.adr/src." >&2
    exit 2
fi

python_path="${flowgentic_root}/src:${asyncflow_src}"
if [[ "${controller}" == "adr" ]]; then
    python_path="${python_path}:${adr_src}"
fi
if [[ -n "${PYTHONPATH:-}" ]]; then
    python_path="${python_path}:${PYTHONPATH}"
fi

cd "${flowgentic_root}"
FLOWGENTIC_CONFIG="${FLOWGENTIC_CONFIG:-${demo_dir}/config.yml}" \
PYTHONPATH="${python_path}" exec "${python_bin}" \
    "${demo_dir}/augmented_campaign.py" \
    --controller "${controller}" \
    "$@"
