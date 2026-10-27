"""Print the metric diff between two results files: ``python eval/compare.py a.json b.json``."""

import sys

from classdeck.evaluation.compare import main

sys.exit(main())
