"""Start FLS V2 from a source checkout:  python run.py [--simulate]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fls.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
