"""Allow ``python -m claude_demo`` to invoke the CLI."""

from .cli.__main__ import main

raise SystemExit(main())
