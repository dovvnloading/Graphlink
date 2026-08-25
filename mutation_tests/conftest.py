"""ADR-022 stage 22.2/22.4: property-based tests that double as mutmut's
kill-suite for the 4 modules in [tool.mutmut]'s source_paths (pyproject.toml).

Deliberately separate from backend/tests/conftest.py: that file's autouse
fixtures import `backend`/`api_provider` (a FastAPI/anthropic/openai/pywebview
chain), which mutmut's sandbox never copies (only `source_paths` +
`also_copy = ["mutation_tests/"]` - see pyproject.toml's own [tool.mutmut]).
Every file here imports ONLY the 4 mutated modules directly, so the sandbox
has everything it needs without pulling in the rest of the app.
"""

import os

from hypothesis import HealthCheck, settings

# Same profile as backend/tests/conftest.py's own registration - duplicated
# rather than shared, on purpose: this directory's whole point is having no
# dependency on anything under backend/tests/.
settings.register_profile("ci", max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci" if os.environ.get("CI") else "default")
